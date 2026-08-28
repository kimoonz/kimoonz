"""진입한 포지션 추적 + 청산 시점 판정.

모델의 라벨이 "10봉 안에 목표와 손절 중 어디를 먼저 치는가" 이므로,
청산 규칙은 이미 정해져 있다. 그걸 그대로 감시해서 알려준다.

  ① 목표 도달  → 익절
  ② 손절 도달  → 손절
  ③ 보유기간 만료 → 시간 청산 (그 봉 종가)
  ④ 반대 신호  → 청산 후 반대 진입

한 봉에서 목표와 손절을 동시에 건드리면 순서를 알 수 없으므로
labels.triple_barrier 와 같은 규칙으로 손절을 먼저 본다(보수적).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

import pandas as pd

EXIT_REASONS_KO = {
    "target": "익절 — 목표 도달",
    "stop": "손절 — 손절선 도달",
    "timeout": "시간 청산 — 보유기간 만료",
    "reverse": "반대 신호 — 방향 전환",
}


@dataclass
class OpenPosition:
    """알림을 보낸 뒤 '진입했다고 가정하고' 추적하는 포지션."""

    instrument: str
    timeframe: str
    side: str              # "LONG" | "SHORT"
    entry: float
    stop: float
    target: float
    entry_bar: str         # 진입 근거가 된 봉의 ISO 시각
    horizon: int           # 최대 보유 봉 수
    prob: float
    atr: float

    @property
    def direction(self) -> int:
        return 1 if self.side == "LONG" else -1

    @property
    def stop_distance(self) -> float:
        return abs(self.entry - self.stop)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "OpenPosition":
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)

    def pnl_r(self, exit_price: float) -> float:
        """R 단위 손익 (1R = 손절폭)."""
        dist = self.stop_distance
        if dist <= 0:
            return 0.0
        return (exit_price - self.entry) * self.direction / dist


@dataclass
class ExitEvent:
    position: OpenPosition
    reason: str            # target | stop | timeout | reverse
    exit_price: float
    exit_bar: datetime
    bars_held: int

    @property
    def reason_ko(self) -> str:
        return EXIT_REASONS_KO.get(self.reason, self.reason)

    @property
    def pnl_price(self) -> float:
        return (self.exit_price - self.position.entry) * self.position.direction

    @property
    def pnl_r(self) -> float:
        return self.position.pnl_r(self.exit_price)

    @property
    def is_win(self) -> bool:
        return self.pnl_r > 0


def check_exit(pos: OpenPosition, df: pd.DataFrame) -> ExitEvent | None:
    """진입 봉 이후 시세를 훑어 청산 시점을 찾는다. 아직이면 None.

    df 는 전체 시세 (진입 봉이 포함된 상태). 진입 봉 다음 봉부터 검사한다.
    """
    entry_ts = pd.Timestamp(pos.entry_bar)
    if entry_ts.tzinfo is None:
        entry_ts = entry_ts.tz_localize("UTC")
    after = df[df.index > entry_ts]
    if after.empty:
        return None

    long = pos.side == "LONG"
    for k, (ts, bar) in enumerate(after.iterrows(), start=1):
        hit_target = bar["high"] >= pos.target if long else bar["low"] <= pos.target
        hit_stop = bar["low"] <= pos.stop if long else bar["high"] >= pos.stop

        if hit_stop:                       # 동시 터치도 손절 우선 (라벨과 동일 규칙)
            return ExitEvent(pos, "stop", pos.stop, ts.to_pydatetime(), k)
        if hit_target:
            return ExitEvent(pos, "target", pos.target, ts.to_pydatetime(), k)
        if k >= pos.horizon:
            return ExitEvent(pos, "timeout", float(bar["close"]), ts.to_pydatetime(), k)
    return None


def force_exit(pos: OpenPosition, price: float, when: datetime,
               bars_held: int, reason: str = "reverse") -> ExitEvent:
    """반대 신호 등으로 강제 청산."""
    return ExitEvent(pos, reason, price, when, bars_held)
