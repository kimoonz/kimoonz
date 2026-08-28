"""워크포워드 백테스트.

과거 어느 시점 t 에서도, 그 시점까지의 데이터만으로 모델을 다시 학습해
t 봉의 확률을 예측한다. 학습 구간의 마지막 horizon 봉은 라벨 미확정이라
잘라낸다(embargo). 이렇게 해야 '실제로 그때 알 수 있었던 정보'만 쓴다.

측정 항목
  - 확률 품질: AUC, Brier, 로그손실, 신뢰도 곡선
  - 매매 성과: 임계치를 넘은 신호만 실제로 진입했다고 보고 수수료·슬리피지 차감
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .bayes import auc, brier_score, log_loss, reliability
from .config import Config
from .model import InstrumentModel

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    predictions: pd.DataFrame        # asof, prob, y, timeout, price, atr
    metrics: dict
    reliability: pd.DataFrame
    trades: pd.DataFrame

    def summary_lines(self) -> list[str]:
        m = self.metrics
        out = [
            f"표본 {m['n']}건  기준상승률 {m['base_rate']:.1%}",
            f"AUC {m['auc']:.3f}   Brier {m['brier']:.4f} (기준 {m['brier_base']:.4f})"
            f"   로그손실 {m['logloss']:.4f}",
            f"보정계수 a 평균 {m['calib_a_mean']:.3f}",
        ]
        if m["n_trades"]:
            out += [
                f"신호 {m['n_trades']}건  적중률 {m['hit_rate']:.1%}"
                f"  기대 {m['avg_r']:+.3f}R/건  누적 {m['total_r']:+.1f}R",
                f"수익팩터 {m['profit_factor']:.2f}  최대낙폭 {m['max_dd_r']:.1f}R",
            ]
        else:
            out.append("임계치를 넘은 신호 없음")
        return out


def walk_forward(cfg: Config, model: InstrumentModel, refit_every: int = 63,
                 max_points: int | None = None) -> BacktestResult:
    """모델 하나에 대한 워크포워드 실행."""
    model.load()
    df, X, labels = model.df, model.X, model.labels
    tf = model.tf
    horizon = tf.label.horizon
    n = len(df)

    start = tf.bayes.min_train + horizon + 60
    if max_points:
        start = max(start, n - max_points)
    if start >= n - horizon:
        raise ValueError(f"{model.inst.name}/{model.timeframe}: 백테스트 표본 부족")

    rows, calib_as = [], []
    fitted_upto = -1
    for i in range(start, n - horizon):
        if i - fitted_upto >= refit_every or model.model is None:
            try:
                model.fit(end=i)                # i 봉은 학습에 미포함
                fitted_upto = i
                calib_as.append(model.model.calib_a)
            except Exception as exc:
                log.debug("재학습 실패 @%d: %s", i, exc)
                continue
        soft = {"regime": model.regime[i:i + 1]} if model.regime is not None else None
        try:
            prob = float(model.model.predict_proba(X.iloc[[i]], soft=soft)[0])
        except Exception:
            continue
        rows.append({
            "asof": df.index[i], "prob": prob, "base": model.model.base_rate,
            "y": labels["y"].iloc[i],
            "timeout": bool(labels["timeout"].iloc[i]), "price": float(df["close"].iloc[i]),
            "atr": float(labels["atr"].iloc[i]), "bars_held": labels["bars_held"].iloc[i],
        })

    preds = pd.DataFrame(rows).dropna(subset=["y"])
    if preds.empty:
        raise ValueError("워크포워드 결과 없음")

    p = preds["prob"].to_numpy()
    y = preds["y"].to_numpy()
    trades = _simulate(cfg, model, preds)
    metrics = {
        "instrument": model.inst.key, "timeframe": model.timeframe,
        "n": len(preds), "base_rate": float(y.mean()),
        "auc": auc(p, y), "brier": brier_score(p, y),
        "brier_base": brier_score(np.full_like(p, y.mean()), y),
        "logloss": log_loss(p, y),
        "calib_a_mean": float(np.mean(calib_as)) if calib_as else float("nan"),
        "period": f"{preds['asof'].iloc[0].date()} ~ {preds['asof'].iloc[-1].date()}",
    }
    metrics.update(_trade_metrics(trades))
    return BacktestResult(preds, metrics, reliability(p, y), trades)


def _simulate(cfg: Config, model: InstrumentModel, preds: pd.DataFrame) -> pd.DataFrame:
    """임계치를 넘은 확률만 진입했다고 가정하고 R 단위 손익을 계산.

    손익은 라벨 배리어로 정해진다. 매수는 상단이 목표·하단이 손절이고,
    매도는 그 반대다 (비대칭 배리어에서 매도 손익비는 매수의 역수).
    거기서 수수료+슬리피지를 R 단위로 환산해 뺀다.
    """
    sig, lab = model.tf.signal, model.tf.label
    spec = model.inst.micro
    rows = []
    for _, r in preds.iterrows():
        lift = r["prob"] - r.get("base", 0.5)
        if r["prob"] >= sig.long_threshold and lift >= sig.min_prob_over_base:
            side = 1
        elif r["prob"] <= sig.short_threshold and -lift >= sig.min_prob_over_base:
            side = -1
        else:
            continue
        won = (r["y"] == 1.0) if side == 1 else (r["y"] == 0.0)
        # 매도는 배리어가 뒤바뀐다 — 하단이 목표(down_mult), 상단이 손절(up_mult)
        target_mult, stop_mult = ((lab.up_mult, lab.down_mult) if side == 1
                                  else (lab.down_mult, lab.up_mult))
        gross_r = target_mult / stop_mult if won else -1.0

        stop_usd = stop_mult * r["atr"] * spec.point_value
        cost_usd = (cfg.costs.commission_per_contract
                    + cfg.costs.slippage_ticks * spec.tick_value)
        cost_r = cost_usd / stop_usd if stop_usd > 0 else 0.0
        rows.append({
            "asof": r["asof"], "side": side, "prob": r["prob"], "won": bool(won),
            "gross_r": gross_r, "cost_r": cost_r, "net_r": gross_r - cost_r,
            "price": r["price"], "atr": r["atr"], "timeout": r["timeout"],
        })
    return pd.DataFrame(rows)


def _trade_metrics(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"n_trades": 0, "hit_rate": float("nan"), "avg_r": float("nan"),
                "total_r": 0.0, "profit_factor": float("nan"), "max_dd_r": 0.0,
                "n_long": 0, "n_short": 0}
    r = trades["net_r"].to_numpy()
    equity = np.cumsum(r)
    peak = np.maximum.accumulate(equity)
    gains = r[r > 0].sum()
    losses = -r[r < 0].sum()
    return {
        "n_trades": len(trades), "hit_rate": float(trades["won"].mean()),
        "avg_r": float(r.mean()), "total_r": float(r.sum()),
        "profit_factor": float(gains / losses) if losses > 0 else float("inf"),
        "max_dd_r": float((peak - equity).max()),
        "n_long": int((trades["side"] == 1).sum()),
        "n_short": int((trades["side"] == -1).sum()),
    }
