"""종목 × 타임프레임 단위 모델 조립.

데이터 -> 피처 -> 삼중배리어 라벨 -> HMM 국면 -> 베이즈 엔진 -> 예측.

라벨은 미래 horizon 봉을 봐야 확정되므로, 학습 데이터의 마지막 horizon 봉은
반드시 잘라낸다(embargo). 이걸 안 하면 미래 정보가 새서 백테스트가 뻥튀기된다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .bayes import BayesModel
from .config import Config, TimeframeConfig
from .data import DataError, DataLoader
from .features import build_features
from .instruments import Instrument
from .labels import triple_barrier
from .regime import GaussianHMM

log = logging.getLogger(__name__)

_INTERVAL_SECONDS = {
    "1m": 60, "2m": 120, "5m": 300, "15m": 900, "30m": 1800,
    "60m": 3600, "1h": 3600, "90m": 5400, "1d": 86400, "1wk": 604800,
}


def interval_seconds(interval: str) -> int:
    if interval not in _INTERVAL_SECONDS:
        raise ValueError(f"지원하지 않는 봉 간격: {interval}")
    return _INTERVAL_SECONDS[interval]


def last_closed_index(index: pd.DatetimeIndex, interval: str,
                      now: datetime | None = None) -> int:
    """마지막으로 '마감된' 봉의 위치. 진행 중인 봉은 제외한다."""
    now = now or datetime.now(timezone.utc)
    dur = timedelta(seconds=interval_seconds(interval))
    for i in range(len(index) - 1, -1, -1):
        if index[i] + dur <= now:
            return i
    return -1


@dataclass
class Prediction:
    """한 종목/타임프레임의 최신 확률 판단."""

    instrument: Instrument
    timeframe: str
    interval: str
    asof: pd.Timestamp                  # 판단 근거가 된 봉의 시각
    price: float                        # 그 봉의 종가
    last_price: float                   # 가장 최근 시세 (진행 중 봉 포함)
    atr: float
    prob_up: float                      # 상단 배리어 선터치 사후확률
    raw_logodds: float
    contributions: list[dict]
    regime_probs: np.ndarray | None
    regime_names: list[str]
    n_train: int
    calib_a: float
    base_rate: float
    diagnostics: dict = field(default_factory=dict)
    bar_closed: bool = True


class InstrumentModel:
    """한 종목 × 한 타임프레임."""

    def __init__(self, cfg: Config, instrument: Instrument, timeframe: str,
                 tf: TimeframeConfig, loader: DataLoader):
        self.cfg = cfg
        self.inst = instrument
        self.timeframe = timeframe
        self.tf = tf
        self.loader = loader
        self.intraday = not tf.interval.endswith(("d", "wk"))

        self.df: pd.DataFrame | None = None
        self.X: pd.DataFrame | None = None
        self.labels: pd.DataFrame | None = None
        self.hmm: GaussianHMM | None = None
        self.regime: np.ndarray | None = None
        self.model: BayesModel | None = None

    # ------------------------------------------------------------------ 데이터
    def load(self, force: bool = False) -> None:
        if self.df is not None and not force:
            return
        df = self.loader.get(self.inst.yahoo, self.tf.interval, self.tf.lookback_days)

        extras: dict[str, pd.DataFrame] = {}
        if self.cfg.data.use_extras:
            for sym in self.inst.extras:
                try:
                    extras[sym] = self.loader.get(sym, self.tf.interval, self.tf.lookback_days)
                except (DataError, Exception) as exc:   # 보조 데이터는 없어도 진행
                    log.info("%s 보조데이터 %s 생략: %s", self.inst.key, sym, exc)

        self.df = df
        self.X = build_features(df, atr_window=self.tf.label.atr_window,
                                extras=extras, intraday=self.intraday)
        self.labels = triple_barrier(
            df, atr_window=self.tf.label.atr_window, up_mult=self.tf.label.up_mult,
            down_mult=self.tf.label.down_mult, horizon=self.tf.label.horizon,
        )

    def _regime_input(self) -> np.ndarray:
        ret = np.log(self.df["close"]).diff()
        vol = ret.rolling(60, min_periods=20).std().replace(0.0, np.nan)
        z = (ret / vol).replace([np.inf, -np.inf], np.nan)
        return z.to_numpy(dtype=float)

    def fit_regime(self, fit_end: int) -> None:
        """국면 HMM 학습 (fit_end 까지의 데이터만)."""
        if not self.tf.bayes.use_regime:
            self.hmm, self.regime = None, None
            return
        z = self._regime_input()
        train = z[:fit_end]
        train = train[np.isfinite(train)]
        try:
            self.hmm = GaussianHMM(n_states=self.tf.bayes.regime_states).fit(train)
            self.regime = self.hmm.filter(z)
        except Exception as exc:
            log.info("%s HMM 학습 실패, 국면 증거 생략: %s", self.inst.key, exc)
            self.hmm, self.regime = None, None

    # ------------------------------------------------------------------ 학습
    def fit(self, end: int | None = None) -> BayesModel:
        """end 위치(미포함)까지의 데이터로 학습. end=None이면 전체."""
        self.load()
        n = len(self.df)
        end = n if end is None else end
        horizon = self.tf.label.horizon
        # 라벨이 확정된 구간까지만 (embargo)
        label_end = max(end - horizon, 0)
        start = max(label_end - self.tf.bayes.train_window, 0)
        if label_end - start < self.tf.bayes.min_train:
            raise ValueError(
                f"{self.inst.name}/{self.timeframe}: 학습 표본 부족 "
                f"({label_end - start} < {self.tf.bayes.min_train})"
            )

        self.fit_regime(label_end)

        X = self.X.iloc[start:label_end]
        y = self.labels["y"].iloc[start:label_end]
        soft = None
        if self.regime is not None:
            soft = {"regime": self.regime[start:label_end]}

        bc = self.tf.bayes
        model = BayesModel(
            n_bins=bc.n_bins, laplace_alpha=bc.laplace_alpha, shrink_m=bc.shrink_m,
            prior_strength=bc.prior_strength, prior_up_rate=bc.prior_up_rate,
            validation_frac=bc.validation_frac,
        )
        model.fit(X, y, soft=soft, embargo=horizon)
        self.model = model
        return model

    # ------------------------------------------------------------------ 예측
    def predict_latest(self, now: datetime | None = None,
                       allow_incomplete: bool = False) -> Prediction:
        """가장 최근 마감된 봉 기준으로 확률을 계산."""
        self.load()
        n = len(self.df)
        i_closed = last_closed_index(self.df.index, self.tf.interval, now)
        i = n - 1 if allow_incomplete else i_closed
        if i < 0:
            raise ValueError("마감된 봉이 없음")
        bar_closed = i <= i_closed

        if self.model is None:
            self.fit(end=i + 1)

        row = self.X.iloc[[i]]
        soft = None
        soft_row = None
        if self.regime is not None:
            soft = {"regime": self.regime[i:i + 1]}
            soft_row = {"regime": self.regime[i]}
        prob = float(self.model.predict_proba(row, soft=soft)[0])
        raw = float(self.model._raw_score(row, soft)[0])
        contribs = self.model.explain(self.X.iloc[i], soft_row=soft_row)

        return Prediction(
            instrument=self.inst,
            timeframe=self.timeframe,
            interval=self.tf.interval,
            asof=self.df.index[i],
            price=float(self.df["close"].iloc[i]),
            last_price=float(self.df["close"].iloc[-1]),
            atr=float(self.labels["atr"].iloc[i]),
            prob_up=prob,
            raw_logodds=raw,
            contributions=contribs,
            regime_probs=self.regime[i] if self.regime is not None else None,
            regime_names=self.hmm.describe() if self.hmm else [],
            n_train=self.model.n_train,
            calib_a=self.model.calib_a,
            base_rate=self.model.base_rate,
            bar_closed=bar_closed,
        )
