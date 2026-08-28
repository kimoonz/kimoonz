"""추세·변동성·분산 배분 전략.

검증에서 실제로 수익이 확인된 구조다. 방향을 맞히려 하지 않고,
'언제 들고 있을지'와 '얼마나 들고 있을지'만 정한다.

  1. 분산   — 금·은·오일·나스닥은 서로 다른 데서 움직인다
              (금/나스닥 상관 0.03, 오일/나스닥 0.18)
  2. 변동성 조절 — 각 종목이 목표 변동성만큼만 기여하도록 비중을 역변동성으로
  3. 추세 필터 — 126일선과 252일선을 모두 넘을 때만 보유 (롱 온리)

숏은 넣지 않는다. 검증에서 숏 사이드는 가치를 파괴했다
(표본외 샤프 롱숏 0.20~0.36 vs 롱온리 0.54~0.58).

2006~2026 검증 (표본내 2006~2017 / 표본외 2018~2026, 왕복 2bp 차감):
  나스닥 단독 보유    샤프 0.69 / 0.86,  최대낙폭 -54% / -35%
  4종목 균등 보유     샤프 0.48 / 0.92,  최대낙폭 -47% / -35%
  + 변동성 조절       샤프 0.58 / 0.96,  최대낙폭 -28% / -19%
  + 추세 이중필터     샤프 0.82 / 0.99,  최대낙폭 -12% / -13%
파라미터 강건성: 추세창 11개 조합 표본외 샤프 0.72~1.02,
변동성 파라미터 9개 조합 0.94~0.99, 비용 20bp까지 0.85 유지.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config
from .instruments import Instrument

TRADING_DAYS = 252


@dataclass
class StrategyParams:
    """검증된 기본값. 강건성 확인 범위 안에서 바꿔도 된다."""

    trend_fast: int = 126        # 빠른 추세 창 (영업일)
    trend_slow: int = 252        # 느린 추세 창
    vol_window: int = 60         # 실현변동성 측정 창
    target_vol: float = 0.15     # 포트폴리오 목표 연변동성
    max_scale: float = 3.0       # 종목별 비중 상한 (저변동 구간 과대노출 방지)
    long_only: bool = True       # 숏은 검증에서 가치를 파괴했다


@dataclass
class InstrumentTarget:
    """한 종목의 현재 목표 상태."""

    instrument: Instrument
    price: float
    asof: pd.Timestamp
    trend_on: bool
    trend_fast_ok: bool
    trend_slow_ok: bool
    ma_fast: float               # trend_fast일 전 가격 (돌파 기준선)
    ma_slow: float
    realized_vol: float          # 연환산 실현변동성
    target_weight: float         # 계좌 대비 목표 노출 비중
    contract_notional: float     # 마이크로 1계약 명목가치
    target_contracts: int        # 목표 계약 수 (마이크로)
    full_contracts: int = 0      # 정규 계약 환산

    @property
    def actual_weight(self) -> float:
        return self.target_contracts * self.contract_notional / self.equity if self.equity else 0.0

    equity: float = 0.0

    @property
    def exit_level(self) -> float:
        """이 가격 아래로 내려가면 추세 이탈 (둘 중 높은 기준선)."""
        return max(self.ma_fast, self.ma_slow)

    @property
    def distance_to_exit(self) -> float:
        """추세 이탈까지 남은 폭 (가격 기준). 보유 중일 때만 의미 있음."""
        if not self.trend_on:
            return 0.0
        return self.price - self.exit_level

    @property
    def blocked_by_granularity(self) -> bool:
        """추세는 켜졌는데 1계약이 목표 비중을 넘어서 못 잡는 상태."""
        return self.trend_on and self.target_contracts == 0 and self.target_weight > 0

    @property
    def equity_needed(self) -> float:
        """1계약을 목표 비중 안에서 잡으려면 필요한 계좌."""
        if self.target_weight <= 0:
            return float("inf")
        return self.contract_notional / self.target_weight


def realized_vol(close: pd.Series, window: int) -> pd.Series:
    """연환산 실현변동성."""
    return np.log(close).diff().rolling(window).std() * np.sqrt(TRADING_DAYS)


def trend_signals(close: pd.Series, fast: int, slow: int) -> pd.DataFrame:
    """두 기간 모두 상승일 때만 보유."""
    fast_ok = close > close.shift(fast)
    slow_ok = close > close.shift(slow)
    return pd.DataFrame({
        "fast_ok": fast_ok, "slow_ok": slow_ok,
        "on": (fast_ok & slow_ok).astype(float),
        "ref_fast": close.shift(fast), "ref_slow": close.shift(slow),
    })


def target_weights(prices: pd.DataFrame, params: StrategyParams) -> pd.DataFrame:
    """각 시점·종목의 목표 비중 (계좌 대비)."""
    n = max(len(prices.columns), 1)
    weights = {}
    for col in prices.columns:
        close = prices[col]
        rv = realized_vol(close, params.vol_window).replace(0.0, np.nan)
        scale = (params.target_vol / rv).clip(upper=params.max_scale)
        on = trend_signals(close, params.trend_fast, params.trend_slow)["on"]
        weights[col] = (scale * on / n).fillna(0.0)
    return pd.DataFrame(weights, index=prices.index)


def evaluate(prices: pd.DataFrame, params: StrategyParams, equity: float,
             point_values: dict[str, float], cost_bps: float = 2.0,
             integer_contracts: bool = True) -> pd.Series:
    """전략의 일간 수익률 시계열. integer_contracts=False 면 이론 비중."""
    ret = prices.pct_change()
    tw = target_weights(prices, params)
    if integer_contracts:
        notional = prices * pd.Series(point_values)
        contracts = np.floor((tw * equity) / notional).clip(lower=0)
        weights = contracts * notional / equity
    else:
        weights = tw
    weights = weights.shift(1).fillna(0.0)
    gross = (weights * ret).sum(axis=1)
    turnover = weights.diff().abs().sum(axis=1).fillna(0.0)
    return gross - turnover * cost_bps / 10_000


def performance(returns: pd.Series) -> dict:
    """연수익·샤프·최대낙폭·칼마."""
    r = returns.dropna()
    if len(r) < 30 or r.std() == 0:
        return {}
    ann = float(r.mean() * TRADING_DAYS)
    vol = float(r.std() * np.sqrt(TRADING_DAYS))
    equity_curve = (1 + r).cumprod()
    mdd = float((equity_curve / equity_curve.cummax() - 1).min())
    return {
        "연수익": ann, "변동성": vol, "샤프": ann / vol if vol else float("nan"),
        "최대낙폭": mdd, "칼마": ann / abs(mdd) if mdd < 0 else float("nan"),
        "거래일": len(r), "누적": float(equity_curve.iloc[-1] - 1),
    }


def suggest_target_vol(prices: pd.DataFrame, point_values: dict[str, float],
                       equity: float, params: StrategyParams,
                       min_contracts: float = 2.0,
                       cap: float = 0.40) -> float:
    """계좌 규모에 맞는 목표 변동성을 찾는다.

    정수 계약 반올림 때문에, 계좌가 작으면 목표 변동성이 낮을 때
    '항상 0계약'이 되어 전략이 아예 안 굴러간다. 평균 보유 계약이
    min_contracts 이상이 되는 최소 목표 변동성을 고른다.

    상한(cap)을 두는 이유: 목표 변동성은 낙폭에 거의 비례한다.
    검증에서 $50,000·40%가 샤프 0.89·최대낙폭 -16.5% 였고, 60%로 올리면
    샤프는 0.99로 오르지만 낙폭이 -27.5%가 된다. 계좌가 그보다 작으면
    변동성을 더 올리는 게 아니라 종목 수를 줄이는 게 맞다.
    """
    notional = prices * pd.Series(point_values)
    for tv in (0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60):
        if tv > cap:
            break
        trial = StrategyParams(**{**params.__dict__, "target_vol": tv})
        tw = target_weights(prices, trial)
        contracts = np.floor((tw * equity) / notional).clip(lower=0)
        held = contracts.sum(axis=1).tail(TRADING_DAYS * 10)
        if len(held) and float(held.mean()) >= min_contracts:
            return tv
    return cap


def affordable_instruments(prices: pd.DataFrame, point_values: dict[str, float],
                           equity: float, max_weight: float = 0.5) -> list[str]:
    """1계약 명목가치가 계좌의 max_weight 를 넘지 않는 종목만.

    마이크로 나스닥 1계약이 $59,000인데 계좌가 $50,000이면, 그 한 종목이
    노출 119%가 된다. 그런 종목은 분산의 일부가 될 수 없다.
    """
    last = prices.ffill().iloc[-1]
    return [c for c in prices.columns
            if last[c] * point_values[c] <= equity * max_weight]


def current_targets(data: dict[str, pd.DataFrame], instruments: dict[str, Instrument],
                    params: StrategyParams, equity: float,
                    asof_index: int = -1) -> list[InstrumentTarget]:
    """지금 각 종목을 얼마나 들고 있어야 하는지."""
    keys = [k for k in instruments if k in data and len(data[k]) > params.trend_slow]
    if not keys:
        return []
    prices = pd.DataFrame({k: data[k]["close"] for k in keys}).ffill()
    tw = target_weights(prices, params)

    out: list[InstrumentTarget] = []
    for key in keys:
        inst = instruments[key]
        close = prices[key]
        sig = trend_signals(close, params.trend_fast, params.trend_slow)
        rv = realized_vol(close, params.vol_window)
        price = float(close.iloc[asof_index])
        notional = price * inst.micro.point_value
        weight = float(tw[key].iloc[asof_index])
        contracts = int(math.floor(weight * equity / notional)) if notional > 0 else 0

        out.append(InstrumentTarget(
            instrument=inst, price=price, asof=close.index[asof_index],
            trend_on=bool(sig["on"].iloc[asof_index]),
            trend_fast_ok=bool(sig["fast_ok"].iloc[asof_index]),
            trend_slow_ok=bool(sig["slow_ok"].iloc[asof_index]),
            ma_fast=float(sig["ref_fast"].iloc[asof_index]),
            ma_slow=float(sig["ref_slow"].iloc[asof_index]),
            realized_vol=float(rv.iloc[asof_index]),
            target_weight=weight, contract_notional=notional,
            target_contracts=max(0, contracts),
            full_contracts=int(math.floor(weight * equity
                                          / (price * inst.full.point_value)))
            if price > 0 else 0,
            equity=equity,
        ))
    return out


# ---------------------------------------------------------------- 배분 변경 신호
@dataclass
class AllocationChange:
    """목표 계약 수가 바뀌었을 때 나가는 신호."""

    target: InstrumentTarget
    held: int                    # 지금 들고 있는 계약 수
    action: str                  # enter | exit | increase | decrease
    entry_price: float | None = None    # 청산/조정 시 기존 진입가
    entry_date: str | None = None

    @property
    def delta(self) -> int:
        return self.target.target_contracts - self.held

    @property
    def reason(self) -> str:
        t = self.target
        if self.action == "enter":
            return "추세 진입 — 126일·252일 기준선 모두 상향"
        if self.action == "exit":
            if not t.trend_fast_ok and not t.trend_slow_ok:
                return "추세 이탈 — 126일·252일 기준선 모두 하향"
            if not t.trend_fast_ok:
                return "추세 이탈 — 126일 기준선 하향"
            if not t.trend_slow_ok:
                return "추세 이탈 — 252일 기준선 하향"
            if t.blocked_by_granularity:
                return (f"변동성 상승(연 {t.realized_vol:.0%})으로 목표 비중이 "
                        f"1계약보다 작아짐")
            return "목표 비중 축소로 전량 청산"
        verb = "확대" if self.action == "increase" else "축소"
        return (f"변동성 연 {t.realized_vol:.0%} — 목표 비중 {t.target_weight:.0%}"
                f"로 {verb}")


def allocation_changes(targets: list[InstrumentTarget], held: dict[str, int],
                       min_delta: int = 1) -> list[AllocationChange]:
    """목표와 보유가 다른 종목만 골라낸다.

    min_delta 로 잔떨림을 거른다. 계약 1개 차이로 매번 알림이 오면
    수수료만 나가고 실행도 피곤하다.
    """
    out = []
    for t in targets:
        key = t.instrument.key
        now = int(held.get(key, 0))
        want = t.target_contracts
        if now == want:
            continue
        if now == 0:
            action = "enter"
        elif want == 0:
            action = "exit"
        elif want > now:
            action = "increase"
        else:
            action = "decrease"
        # 진입·청산은 항상 알리고, 비중 조정만 잔떨림 필터를 건다
        if action in ("increase", "decrease") and abs(want - now) < min_delta:
            continue
        out.append(AllocationChange(target=t, held=now, action=action))
    return out
