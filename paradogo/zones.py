"""구역(A~H) 인식과 우선순위.

사이트가 구역을 어떻게 적는지 모르므로 흔한 표기를 두루 인식한다.
('A구역', '구역 A', 'A존', 'A동', 'A-03', '캐빈 A' …)
표기가 특이하면 ``target.zone_pattern`` 에 정규식을 직접 주면 된다.
캡처 그룹 1번이 구역 이름이 된다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# 위에서부터 순서대로 시도한다. 구체적인 표기를 먼저 둔다.
DEFAULT_ZONE_PATTERNS: tuple[str, ...] = (
    r"([A-Za-z])\s*구역",          # A구역, A 구역
    r"구역\s*([A-Za-z])",          # 구역 A
    r"([A-Za-z])\s*[존죤]",        # A존
    r"존\s*([A-Za-z])",            # 존 A
    r"([A-Za-z])\s*동(?![시작])",  # A동  ('동시' 같은 낱말은 피한다)
    r"([A-Za-z])\s*[-–]\s*\d+",    # A-03, A - 3
    r"([A-Za-z])\s*타입",          # A타입
    r"캐빈\s*([A-Za-z])(?![A-Za-z])",  # 캐빈 A
    r"(?:^|[\s(\[])([A-Za-z])(?:$|[\s)\]])",  # 홀로 떨어진 한 글자
)


def extract_zone(text: str, patterns: tuple[str, ...] | None = None) -> str:
    """캐빈 이름에서 구역 한 글자를 뽑는다. 못 찾으면 빈 문자열."""
    if not text:
        return ""
    for pattern in patterns or DEFAULT_ZONE_PATTERNS:
        try:
            match = re.search(pattern, text)
        except re.error as exc:
            log.warning("구역 정규식이 잘못됐습니다(%r): %s", pattern, exc)
            continue
        if match and match.groups():
            found = (match.group(1) or "").strip().upper()
            if found:
                return found
    return ""


def normalize_zone(value: object) -> str:
    """'a구역', ' B ' 같은 입력을 'A', 'B' 로 맞춘다."""
    text = str(value).strip().upper()
    if not text:
        return ""
    # 'A구역' 처럼 적어도 받아준다. (\b 는 한글이 뒤따르면 경계로 안 쳐서 못 쓴다)
    letters = re.match(r"^([A-Z])(?![A-Za-z])", text)
    return letters.group(1) if letters else text


@dataclass(frozen=True, slots=True)
class ZonePreference:
    """어느 구역을 원하는지.

    ``wanted`` 는 우선순위 순서다. 비어 있으면 구역을 따지지 않는다.
    """

    wanted: tuple[str, ...] = ()
    excluded: frozenset[str] = field(default_factory=frozenset)
    strict: bool = True  # 구역을 못 읽은 항목을 예약 후보에서 뺄지

    @property
    def active(self) -> bool:
        return bool(self.wanted or self.excluded)

    def allows(self, zone: str) -> bool:
        """**감지·알림용** 판정. 구역을 모르면 통과시킨다.

        여기서 걸러버리면 구역 표기를 못 읽는 사이트에서 취소를 통째로 놓친다.
        """
        zone = normalize_zone(zone)
        if zone and zone in self.excluded:
            return False
        if self.wanted and zone and zone not in self.wanted:
            return False
        return True

    def selectable(self, zone: str) -> bool:
        """**예약 선택용** 판정. strict 면 구역을 모르는 항목은 고르지 않는다.

        잘못된 구역을 잡아 놓고 결제 화면까지 가는 것보다, 안 고르고 알리는 편이 낫다.
        """
        zone = normalize_zone(zone)
        if not zone:
            return not (self.strict and bool(self.wanted))
        return self.allows(zone)

    def rank(self, zone: str) -> int:
        """우선순위. 낮을수록 먼저 고른다."""
        zone = normalize_zone(zone)
        if zone and zone in self.wanted:
            return self.wanted.index(zone)
        return len(self.wanted) + (1 if not zone else 0)

    @classmethod
    def build(
        cls,
        wanted: list[str] | tuple[str, ...] = (),
        excluded: list[str] | tuple[str, ...] = (),
        strict: bool = True,
    ) -> "ZonePreference":
        want = tuple(dict.fromkeys(normalize_zone(z) for z in wanted if str(z).strip()))
        deny = frozenset(normalize_zone(z) for z in excluded if str(z).strip())
        conflict = [z for z in want if z in deny]
        if conflict:
            # 양쪽에 다 적힌 구역은 제외가 이긴다. 조용히 예약해 버리면 안 된다.
            log.warning(
                "구역 %s 이(가) zones 와 exclude_zones 양쪽에 있습니다. 제외로 처리합니다.",
                ", ".join(conflict),
            )
            want = tuple(z for z in want if z not in deny)
        return cls(wanted=want, excluded=deny, strict=strict)
