"""거래 종목 명세 (심볼, 계약 승수, 틱, 거래시간)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ContractSpec:
    """단일 선물 계약 규격."""

    code: str            # 거래소 심볼 (예: MGC)
    name: str            # 한글 이름
    point_value: float   # 가격 1.0 움직일 때 손익 (USD)
    tick_size: float     # 최소 호가 단위

    @property
    def tick_value(self) -> float:
        """1틱 손익 (USD)."""
        return self.point_value * self.tick_size


@dataclass(frozen=True)
class Instrument:
    """종목 하나 = 데이터 심볼 + 마이크로/정규 계약 규격."""

    key: str                      # 내부 식별자 (gold, silver, crude, nasdaq)
    name: str                     # 한글 이름
    yahoo: str                    # yfinance 심볼
    stooq: str                    # stooq 폴백 심볼
    micro: ContractSpec
    full: ContractSpec
    price_decimals: int = 2
    # 상관 종목 (추가 증거로 사용, 없으면 조용히 건너뜀)
    extras: tuple[str, ...] = field(default_factory=tuple)

    def round_price(self, price: float, spec: ContractSpec | None = None) -> float:
        """호가 단위에 맞춰 가격을 스냅."""
        tick = (spec or self.micro).tick_size
        return round(round(price / tick) * tick, 10)

    def fmt(self, price: float) -> str:
        return f"{price:,.{self.price_decimals}f}"


GOLD = Instrument(
    key="gold",
    name="금",
    yahoo="GC=F",
    stooq="gc.f",
    micro=ContractSpec("MGC", "마이크로 금", point_value=10.0, tick_size=0.10),
    full=ContractSpec("GC", "정규 금", point_value=100.0, tick_size=0.10),
    price_decimals=1,
    extras=("DX-Y.NYB",),
)

SILVER = Instrument(
    key="silver",
    name="은",
    yahoo="SI=F",
    stooq="si.f",
    micro=ContractSpec("SIL", "마이크로 은", point_value=1000.0, tick_size=0.005),
    full=ContractSpec("SI", "정규 은", point_value=5000.0, tick_size=0.005),
    price_decimals=3,
    extras=("DX-Y.NYB", "GC=F"),
)

CRUDE = Instrument(
    key="crude",
    name="크루드오일",
    yahoo="CL=F",
    stooq="cl.f",
    micro=ContractSpec("MCL", "마이크로 오일", point_value=100.0, tick_size=0.01),
    full=ContractSpec("CL", "정규 오일", point_value=1000.0, tick_size=0.01),
    price_decimals=2,
    extras=("DX-Y.NYB",),
)

NASDAQ = Instrument(
    key="nasdaq",
    name="나스닥100",
    yahoo="NQ=F",
    stooq="^ndx",
    micro=ContractSpec("MNQ", "마이크로 나스닥", point_value=2.0, tick_size=0.25),
    full=ContractSpec("NQ", "정규 나스닥", point_value=20.0, tick_size=0.25),
    price_decimals=2,
    extras=("^VIX",),
)

INSTRUMENTS: dict[str, Instrument] = {
    i.key: i for i in (GOLD, SILVER, CRUDE, NASDAQ)
}


def get(key: str) -> Instrument:
    try:
        return INSTRUMENTS[key]
    except KeyError:
        raise KeyError(
            f"알 수 없는 종목 '{key}'. 사용 가능: {', '.join(INSTRUMENTS)}"
        ) from None
