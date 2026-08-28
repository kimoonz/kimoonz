"""재고 스냅샷과 상태 전환 감지.

'빈자리 있나?'를 매번 새로 묻는 대신, 그 달 전체의 (날짜 × 캐빈) 상태를 한 장의
스냅샷으로 찍고 직전 스냅샷과 비교한다. **마감 → 예약가능** 으로 뒤집힌 항목이
곧 방금 발생한 취소다.

이 모듈은 순수 함수만 담는다(네트워크·DB 없음). 전환 판정 로직을 브라우저 없이
테스트할 수 있게 하기 위해서다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .zones import ZonePreference

# 달력 DOM만 읽어 캐빈 구분 없이 날짜 단위로만 볼 때 쓰는 이름.
DATE_ONLY_CABIN = "(달력)"

_COMPACT_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_LOOSE_DATE = re.compile(r"^(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})$")


def normalize_date(value: object) -> str:
    """'20261003', '2026.10.3', '2026-10-03' 등을 모두 'YYYY-MM-DD' 로 맞춘다."""
    text = str(value).strip()
    if not text:
        return ""
    compact = _COMPACT_DATE.match(text)
    if compact:
        y, m, d = compact.groups()
        return f"{y}-{m}-{d}"
    loose = _LOOSE_DATE.match(text)
    if loose:
        y, m, d = loose.groups()
        return f"{y}-{int(m):02d}-{int(d):02d}"
    return text


@dataclass(frozen=True, slots=True)
class Slot:
    """특정 날짜의 특정 캐빈 한 칸."""

    stay_date: str  # YYYY-MM-DD
    cabin: str
    available: bool
    remaining: int | None = None  # 잔여 수량을 주는 사이트에서만 채워진다
    price: str = ""
    zone: str = ""  # 구역 A~H. 못 읽으면 빈 문자열

    @property
    def key(self) -> tuple[str, str]:
        return (self.stay_date, self.cabin)

    @property
    def zone_label(self) -> str:
        return f"{self.zone}구역" if self.zone else "구역미상"

    def __str__(self) -> str:
        mark = "예약가능" if self.available else "마감"
        rest = f" (잔여 {self.remaining})" if self.remaining is not None else ""
        zone = f"[{self.zone}] " if self.zone else ""
        return f"{self.stay_date} {zone}{self.cabin} — {mark}{rest}"


@dataclass(slots=True)
class Snapshot:
    """한 시점의 재고 전체."""

    taken_at: datetime
    slots: tuple[Slot, ...] = ()
    source: str = "dom"  # dom | api

    def by_key(self) -> dict[tuple[str, str], Slot]:
        return {slot.key: slot for slot in self.slots}

    @property
    def available_count(self) -> int:
        return sum(1 for s in self.slots if s.available)

    def dates(self) -> list[str]:
        return sorted({s.stay_date for s in self.slots})

    def cabins(self) -> list[str]:
        return sorted({s.cabin for s in self.slots})


class ChangeKind(str, Enum):
    OPENED = "opened"        # 마감 → 예약가능  (취소 발생)
    CLOSED = "closed"        # 예약가능 → 마감  (누가 잡아감)
    RESTOCKED = "restocked"  # 예약가능인데 잔여 수량이 늘어남 (부분 취소)
    APPEARED = "appeared"    # 목록에 새로 등장 (예약 오픈 등)
    VANISHED = "vanished"    # 목록에서 사라짐

    @property
    def is_bookable(self) -> bool:
        """지금 당장 잡으러 가야 하는 전환인가."""
        return self in (ChangeKind.OPENED, ChangeKind.RESTOCKED, ChangeKind.APPEARED)

    @property
    def label(self) -> str:
        return {
            ChangeKind.OPENED: "취소 발생",
            ChangeKind.CLOSED: "마감됨",
            ChangeKind.RESTOCKED: "잔여 증가",
            ChangeKind.APPEARED: "새로 등장",
            ChangeKind.VANISHED: "사라짐",
        }[self]


@dataclass(slots=True)
class Change:
    kind: ChangeKind
    slot: Slot
    previous: Slot | None = None
    at: datetime | None = None

    @property
    def stay_date(self) -> str:
        return self.slot.stay_date

    @property
    def cabin(self) -> str:
        return self.slot.cabin

    def __str__(self) -> str:
        base = f"[{self.kind.label}] {self.slot.stay_date} {self.slot.cabin}"
        if self.kind is ChangeKind.RESTOCKED and self.previous is not None:
            base += f" 잔여 {self.previous.remaining} → {self.slot.remaining}"
        elif self.slot.price:
            base += f" ({self.slot.price})"
        return base


def diff(old: Snapshot | None, new: Snapshot) -> list[Change]:
    """두 스냅샷을 비교해 전환 목록을 만든다.

    ``old`` 가 None 이면(첫 관측) 전환이 아니라 '기준선'이므로 빈 목록을 돌려준다.
    첫 관측에서 예약가능한 칸을 취소라고 알리면 오탐이 된다.
    """
    if old is None:
        return []

    before = old.by_key()
    after = new.by_key()
    changes: list[Change] = []

    for key, slot in after.items():
        prev = before.get(key)
        if prev is None:
            changes.append(Change(ChangeKind.APPEARED, slot, None, new.taken_at))
            continue
        if slot.available and not prev.available:
            changes.append(Change(ChangeKind.OPENED, slot, prev, new.taken_at))
        elif prev.available and not slot.available:
            changes.append(Change(ChangeKind.CLOSED, slot, prev, new.taken_at))
        elif (
            slot.available
            and prev.available
            and slot.remaining is not None
            and prev.remaining is not None
            and slot.remaining > prev.remaining
        ):
            changes.append(Change(ChangeKind.RESTOCKED, slot, prev, new.taken_at))

    for key, prev in before.items():
        if key not in after:
            changes.append(Change(ChangeKind.VANISHED, prev, prev, new.taken_at))

    changes.sort(key=lambda c: (c.slot.stay_date, c.slot.cabin))
    return changes


@dataclass(slots=True)
class TargetFilter:
    """어떤 전환에 반응할지 거르는 조건."""

    dates: frozenset[str] = frozenset()      # 비우면 모든 날짜
    cabin_keywords: tuple[str, ...] = ()     # 비우면 모든 캐빈
    zones: ZonePreference = field(default_factory=ZonePreference)

    def matches(self, slot: Slot) -> bool:
        if self.dates and slot.stay_date not in self.dates:
            return False
        # 달력만 읽는 폴백 모드에서는 캐빈·구역을 알 수 없으므로 조건을 적용하지 않는다.
        # (여기서 걸러버리면 취소를 통째로 놓친다. 무엇인지는 들어가서 확인한다.)
        if slot.cabin == DATE_ONLY_CABIN:
            return True
        if not self.zones.allows(slot.zone):
            return False
        if self.cabin_keywords and not any(k in slot.cabin for k in self.cabin_keywords):
            return False
        return True

    def bookable(self, changes: list[Change]) -> list[Change]:
        """지금 잡으러 갈 만한 전환만 남긴다."""
        return [
            c for c in changes
            if c.kind.is_bookable and c.slot.available and self.matches(c.slot)
        ]
