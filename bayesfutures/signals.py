"""확률 -> 매매 결정 (방향, 진입/손절/목표, 계약 수).

핵심 계산
  기대값(R) = p·(목표/손절) - (1-p) - 비용   ... 롱 기준
  켈리 f*   = p - (1-p)/R_ratio              ... 분수 켈리로 축소해서 사용
  계약 수   = (계좌 × 리스크%) / (손절폭 × 계약승수)

'확률이 임계치를 넘었다'만으로는 부족하다. 수수료·슬리피지를 뺀 기대값이
양수여야 진입 신호로 인정한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from .config import Config
from .instruments import ContractSpec
from .model import Prediction


class Side(Enum):
    LONG = "매수"
    SHORT = "매도"
    FLAT = "관망"


@dataclass
class Sizing:
    spec: ContractSpec
    contracts: int
    risk_usd: float           # 실제 걸리는 금액 (계약수 × 손절폭 × 승수)
    kelly_contracts: float    # 분수 켈리가 제안한 계약 수 (참고용)
    per_contract_risk: float = 0.0   # 1계약당 손절 시 손실
    full_budget_contracts: int = 0   # 신뢰도 스케일링 없이 리스크 한도만 봤을 때
    size_factor: float = 1.0         # 적용된 신뢰도 배수

    @property
    def blocked_by_confidence(self) -> bool:
        """리스크 한도로는 잡히는데 신뢰도 스케일링 때문에 0이 된 경우."""
        return self.contracts == 0 and self.full_budget_contracts >= 1


@dataclass
class Signal:
    pred: Prediction
    side: Side
    prob: float              # 해당 방향이 맞을 확률
    entry: float
    stop: float
    target: float
    stop_distance: float
    expected_r: float        # 비용 차감 기대값 (R 단위)
    kelly_fraction: float    # 분수 켈리 (자본 대비)
    lift: float = 0.0        # 기준확률 대비 초과분 (증거가 만든 부분)
    sizing: list[Sizing] = field(default_factory=list)
    reasons: list[dict] = field(default_factory=list)
    cost_r: float = 0.0

    @property
    def is_actionable(self) -> bool:
        return self.side is not Side.FLAT

    @property
    def risk_reward(self) -> float:
        return abs(self.target - self.entry) / max(abs(self.entry - self.stop), 1e-9)


def build_signal(cfg: Config, pred: Prediction) -> Signal:
    """예측 하나를 매매 신호로 변환."""
    tf = cfg.timeframes[pred.timeframe]
    sc, lc = tf.signal, tf.label
    inst = pred.instrument
    atr = pred.atr
    entry = pred.last_price if pred.last_price > 0 else pred.price

    p_up = pred.prob_up
    # 기준확률(그 종목의 장기 드리프트) 대비 초과분.
    # 절대 임계치만 쓰면, 기준확률이 낮은 종목은 모든 봉이 매도 신호가 되고
    # 높은 종목은 모든 봉이 매수 신호가 된다. 그건 증거가 아니라 드리프트다.
    lift = p_up - pred.base_rate
    min_lift = sc.min_prob_over_base

    if p_up >= sc.long_threshold and lift >= min_lift:
        side, prob = Side.LONG, p_up
    elif p_up <= sc.short_threshold and -lift >= min_lift:
        side, prob = Side.SHORT, 1.0 - p_up
    else:
        side, prob = Side.FLAT, max(p_up, 1.0 - p_up)

    # 손절·목표는 라벨 배리어와 같아야 확률이 의미를 가진다.
    # 매도는 배리어 역할이 뒤바뀐다: 하단이 목표, 상단이 손절.
    up_dist, down_dist = lc.up_mult * atr, lc.down_mult * atr
    if side is Side.SHORT:
        target_distance, stop_distance = down_dist, up_dist
    else:
        target_distance, stop_distance = up_dist, down_dist

    direction = 1 if side is Side.LONG else -1 if side is Side.SHORT else 0
    stop = entry - direction * stop_distance
    target = entry + direction * target_distance

    # 비용을 R 단위로 (마이크로 계약 기준 — 정규 계약도 비율은 같다)
    spec = inst.micro
    stop_usd = stop_distance * spec.point_value if stop_distance > 0 else 0.0
    cost_usd = cfg.costs.commission_per_contract + cfg.costs.slippage_ticks * spec.tick_value
    cost_r = cost_usd / stop_usd if stop_usd > 0 else 0.0

    rr = target_distance / stop_distance if stop_distance > 0 else 1.0
    expected_r = prob * rr - (1.0 - prob) - cost_r

    # 켈리: f* = p - (1-p)/rr
    kelly_raw = prob - (1.0 - prob) / rr if rr > 0 else 0.0
    kelly = max(0.0, kelly_raw) * cfg.account.kelly_fraction

    if side is not Side.FLAT and expected_r < sc.min_edge_r:
        side, direction = Side.FLAT, 0     # 기대값이 안 나오면 관망

    # 확률이 임계치를 겨우 넘었으면 작게, 확실하면 크게
    size_factor = 1.0
    if sc.confidence_scaling and side is not Side.FLAT:
        threshold = sc.long_threshold if side is Side.LONG else 1.0 - sc.short_threshold
        span = max(sc.full_size_prob - threshold, 1e-6)
        raw = (prob - threshold) / span
        size_factor = float(min(1.0, max(sc.min_size_frac, raw)))

    sizing: list[Sizing] = []
    if side is not Side.FLAT:
        for spec_i in (inst.micro, inst.full):
            sizing.append(_size(cfg, spec_i, stop_distance, kelly, size_factor))

    return Signal(
        pred=pred, side=side, prob=prob,
        entry=inst.round_price(entry), stop=inst.round_price(stop),
        target=inst.round_price(target), stop_distance=stop_distance,
        expected_r=expected_r, kelly_fraction=kelly, lift=lift, sizing=sizing,
        reasons=pred.contributions, cost_r=cost_r,
    )


def _size(cfg: Config, spec: ContractSpec, stop_distance: float, kelly: float,
          size_factor: float = 1.0) -> Sizing:
    """계약 수 = min(리스크 한도, 켈리 상한) × 신뢰도 배수.

    리스크 한도가 하드 캡이다. 1:1 손익비에서 켈리는 자본의 20%를 걸라고 하는데
    그건 리스크 1% 규칙보다 훨씬 공격적이라 실제로는 거의 안 걸린다.
    확률에 따라 크기를 바꾸는 건 size_factor 가 담당한다.
    """
    risk_budget = cfg.account.equity_usd * cfg.account.risk_per_trade_pct / 100.0
    per_contract_risk = stop_distance * spec.point_value
    if per_contract_risk <= 0:
        return Sizing(spec, 0, 0.0, 0.0)

    by_kelly = (cfg.account.equity_usd * kelly) / per_contract_risk if kelly > 0 else 0.0

    def _floor(scaled_budget: float) -> int:
        n = scaled_budget / per_contract_risk
        n = min(n, by_kelly) if by_kelly > 0 else n
        return max(0, min(int(math.floor(n)), cfg.account.max_contracts))

    contracts = _floor(risk_budget * size_factor)
    full = _floor(risk_budget)
    return Sizing(spec, contracts, contracts * per_contract_risk, by_kelly,
                  per_contract_risk=per_contract_risk, full_budget_contracts=full,
                  size_factor=size_factor)


def combine(signals: dict[str, Signal]) -> str | None:
    """일봉·1시간봉 신호가 같은 방향이면 '겹침'으로 표시."""
    actionable = {k: s for k, s in signals.items() if s.is_actionable}
    if len(actionable) < 2:
        return None
    sides = {s.side for s in actionable.values()}
    if len(sides) == 1:
        return f"일봉·1시간봉 모두 {next(iter(sides)).value}"
    return "타임프레임 간 방향 충돌 — 신중"
