"""설정 로딩 (config.yaml + 환경변수)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


@dataclass
class AccountConfig:
    equity_usd: float = 10_000.0       # 계좌 평가금
    risk_per_trade_pct: float = 1.0    # 1회 매매에 걸 리스크 (%)
    kelly_fraction: float = 0.25       # 켈리의 몇 배를 쓸지 (1/4 켈리)
    max_contracts: int = 10            # 안전장치: 최대 계약 수


@dataclass
class CostConfig:
    commission_per_contract: float = 2.5   # 왕복 수수료 (USD, 마이크로 기준)
    slippage_ticks: float = 1.0            # 진입+청산 합산 슬리피지 (틱)


@dataclass
class LabelConfig:
    """삼중 배리어 라벨링 파라미터."""

    atr_window: int = 14
    up_mult: float = 1.0      # 상단 배리어 = 종가 + up_mult * ATR
    down_mult: float = 1.0    # 하단 배리어 = 종가 - down_mult * ATR
    horizon: int = 10         # 최대 보유 봉 수


@dataclass
class BayesConfig:
    n_bins: int = 5                 # 피처 분위 구간 수
    laplace_alpha: float = 1.0      # 라플라스 평활
    shrink_m: float = 40.0          # 경험적 베이즈 축소 (구간 표본 수 기준)
    prior_strength: float = 20.0    # 사전분포 Beta 유사표본 수
    prior_up_rate: float = 0.5      # 사전 상승확률
    train_window: int = 1500        # 학습에 쓰는 최근 봉 수
    min_train: int = 300            # 최소 학습 표본
    validation_frac: float = 0.25   # 보정(calibration)용 검증 비율
    use_regime: bool = True         # HMM 국면 증거 사용
    regime_states: int = 3


@dataclass
class SignalConfig:
    """확률 -> 매매 결정."""

    long_threshold: float = 0.58    # 이 이상이면 매수
    short_threshold: float = 0.42   # 이 이하이면 매도
    min_edge_r: float = 0.05        # 수수료 반영 기대값 최소치 (R 단위)
    min_prob_over_base: float = 0.02  # 기준확률을 이만큼은 넘어야 '증거'로 인정
    cooldown_bars: int = 3          # 같은 방향 재알림 대기 봉 수
    # 손절/목표는 LabelConfig 의 up_mult/down_mult 를 그대로 쓴다.
    # 확률이 '목표를 손절보다 먼저 건드릴 확률'이므로, 실제 주문의 손절·목표가
    # 라벨 배리어와 다르면 그 확률은 제안하는 매매와 무관한 숫자가 된다.
    confidence_scaling: bool = True # 확률이 높을수록 크게 잡을지
    full_size_prob: float = 0.70    # 이 확률 이상이면 리스크 한도 100% 사용
    min_size_frac: float = 0.25     # 임계치 근처에서 쓸 최소 비율


@dataclass
class TimeframeConfig:
    enabled: bool = True
    interval: str = "1d"            # yfinance interval
    lookback_days: int = 7300       # 받아올 기간(일)
    label: LabelConfig = field(default_factory=LabelConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    bayes: BayesConfig = field(default_factory=BayesConfig)


@dataclass
class StrategyConfig:
    """추세·변동성·분산 배분 전략 (검증에서 수익이 확인된 구조)."""

    enabled: bool = True
    trend_fast: int = 126           # 빠른 추세 창 (영업일)
    trend_slow: int = 252           # 느린 추세 창
    vol_window: int = 60            # 실현변동성 측정 창
    target_vol: float = 0.0         # 0이면 계좌 규모에 맞춰 자동 결정
    max_scale: float = 3.0          # 종목별 비중 상한
    min_delta_contracts: int = 1    # 비중 조정 알림 최소 계약 변화
    rebalance_hour_kst: int = 8     # 배분 점검 시각 (하루 1회면 충분한 전략)


@dataclass
class AlertConfig:
    briefing_enabled: bool = True
    briefing_time_kst: str = "08:00"   # 매일 브리핑 시각 (한국시간)
    signal_alerts: bool = True
    exit_alerts: bool = True          # 목표/손절/시간만료 도달 시 청산 알림
    send_on_hold: bool = False        # 관망도 개별 알림으로 보낼지
    show_position_size: bool = False  # 계약 수 계산을 알림에 포함할지
    timezone: str = "Asia/Seoul"


@dataclass
class DataConfig:
    cache_dir: str = "data_cache"
    cache_minutes: int = 30           # 캐시 유효 시간
    source: str = "auto"              # auto | yahoo | stooq
    use_extras: bool = True           # 상관 종목 증거 사용


@dataclass
class Config:
    instruments: list[str] = field(
        default_factory=lambda: ["gold", "silver", "crude", "nasdaq"]
    )
    account: AccountConfig = field(default_factory=AccountConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    data: DataConfig = field(default_factory=DataConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    timeframes: dict[str, TimeframeConfig] = field(
        default_factory=lambda: {
            "daily": TimeframeConfig(interval="1d", lookback_days=7300),
            "hourly": TimeframeConfig(
                interval="1h",
                lookback_days=720,
                label=LabelConfig(atr_window=14, horizon=8),
                signal=SignalConfig(long_threshold=0.60, short_threshold=0.40,
                                    cooldown_bars=6),
                bayes=BayesConfig(train_window=3000),
            ),
        }
    )
    poll_seconds: int = 300            # 상시 실행 시 확인 주기
    state_dir: str = "state"

    # --- 비밀값 ---------------------------------------------------------
    # 봇 토큰은 환경변수에서만 읽는다. 이 폴더가 클라우드로 동기화되거나
    # 깃에 올라가도 토큰은 따라가지 않는다.
    #   Windows:  [Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN", "<토큰>", "User")
    @property
    def telegram_token(self) -> str | None:
        return os.environ.get("TELEGRAM_BOT_TOKEN") or None

    @property
    def telegram_chat_id(self) -> str | None:
        """이 알림을 받을 방.

        용도별 전용 변수를 먼저 본다. 봇 하나로 여러 자동화를 돌릴 때
        선물 신호가 다른 피드에 섞이지 않게 하려는 것이다.
        마지막으로 state/telegram_config.json 을 본다(슈퍼그룹 전환 시
        새 chat_id 가 여기에 자동 저장된다).
        """
        for key in ("TELEGRAM_CHAT_ID_FUTURES", "TELEGRAM_CHAT_ID"):
            value = (os.environ.get(key) or "").strip()
            if value:
                return value
        saved = self.chat_id_file
        if saved.exists():
            try:
                stored = json.loads(saved.read_text(encoding="utf-8")).get("chat_id")
                return str(stored).strip() or None if stored else None
            except (json.JSONDecodeError, OSError):
                return None
        return None

    @property
    def chat_id_file(self) -> Path:
        return Path(self.state_dir) / "telegram_config.json"

    def save_chat_id(self, chat_id: str) -> None:
        """슈퍼그룹 전환 등으로 바뀐 chat_id 를 남긴다. 토큰은 절대 안 쓴다."""
        path = self.chat_id_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"chat_id": str(chat_id)}, indent=2,
                                       ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def _build(cls: type, data: Any) -> Any:
    """중첩 dataclass를 dict에서 재귀적으로 만든다 (알 수 없는 키는 에러)."""
    if not is_dataclass(cls) or data is None:
        return data
    if not isinstance(data, dict):
        raise TypeError(f"{cls.__name__} 설정은 매핑이어야 합니다 (받은 값: {type(data).__name__})")
    kwargs: dict[str, Any] = {}
    known = {f.name: f for f in fields(cls)}
    for key, value in data.items():
        if key not in known:
            raise KeyError(f"{cls.__name__}에 알 수 없는 설정 키: '{key}'")
        ftype = known[key].type
        if is_dataclass(ftype):
            kwargs[key] = _build(ftype, value)
        else:
            kwargs[key] = value
    return cls(**kwargs)


def load_config(path: str | Path | None = None) -> Config:
    """config.yaml을 읽어 Config를 만든다. 파일이 없으면 기본값."""
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not path.exists():
        return Config()

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config()

    if "instruments" in raw:
        cfg.instruments = list(raw["instruments"])
    if "poll_seconds" in raw:
        cfg.poll_seconds = int(raw["poll_seconds"])
    if "state_dir" in raw:
        cfg.state_dir = str(raw["state_dir"])
    for name, cls in (
        ("account", AccountConfig),
        ("costs", CostConfig),
        ("data", DataConfig),
        ("alerts", AlertConfig),
        ("strategy", StrategyConfig),
    ):
        if name in raw:
            setattr(cfg, name, _build(cls, raw[name]))

    if "timeframes" in raw:
        tfs: dict[str, TimeframeConfig] = {}
        for tf_name, tf_raw in (raw["timeframes"] or {}).items():
            base = cfg.timeframes.get(tf_name, TimeframeConfig())
            merged = _merge_timeframe(base, tf_raw or {})
            tfs[tf_name] = merged
        cfg.timeframes = tfs
    return cfg


def _merge_timeframe(base: TimeframeConfig, raw: dict) -> TimeframeConfig:
    """기본 타임프레임 설정 위에 yaml 값을 덮어쓴다."""
    out = TimeframeConfig(
        enabled=raw.get("enabled", base.enabled),
        interval=raw.get("interval", base.interval),
        lookback_days=raw.get("lookback_days", base.lookback_days),
        label=base.label,
        signal=base.signal,
        bayes=base.bayes,
    )
    for name, cls in (("label", LabelConfig), ("signal", SignalConfig), ("bayes", BayesConfig)):
        if name in raw and raw[name]:
            current = getattr(out, name)
            merged = {f.name: getattr(current, f.name) for f in fields(cls)}
            for key, value in raw[name].items():
                if key not in merged:
                    raise KeyError(f"{cls.__name__}에 알 수 없는 설정 키: '{key}'")
                merged[key] = value
            setattr(out, name, cls(**merged))
    return out
