"""재고를 읽어오는 두 가지 경로.

* ``ApiSource`` — 사이트가 쓰는 재고 조회 API를 직접 호출한다. 한 번에 한 달치를
  받아오고 응답이 JSON이라 빠르다(수십~수백 ms). 날짜 × 캐빈 단위까지 보인다.
* ``DomSource`` — API를 못 찾았을 때의 폴백. 달력 페이지를 열어 날짜 칸을 통째로
  읽는다. 한 달에 페이지 로딩 한 번이면 되도록 셀을 한꺼번에 훑는다.
  대신 **날짜 단위**까지만 보이고, 어떤 캐빈이 풀렸는지는 실제로 들어가 봐야 안다.

두 소스 모두 같은 ``Snapshot`` 을 돌려주므로 추적기 쪽은 어느 경로인지 몰라도 된다.
"""

from __future__ import annotations

import json
import logging
from calendar import monthrange
from typing import Any

from .clock import now_kst
from .config import ApiConfig
from .errors import ParadogoError
from .inventory import DATE_ONLY_CABIN, Slot, Snapshot, normalize_date
from .selectors import SelectorMap, first_nonempty
from .zones import extract_zone

log = logging.getLogger(__name__)



class SourceError(ParadogoError):
    """재고 조회에 실패했을 때."""


def month_placeholders(year: int, month: int) -> dict[str, str]:
    """URL/본문 템플릿에 쓸 수 있는 값들."""
    last_day = monthrange(year, month)[1]
    return {
        "year": str(year),
        "month": str(month),
        "month02": f"{month:02d}",
        "ym": f"{year}{month:02d}",
        "ym_dash": f"{year}-{month:02d}",
        "first_day": f"{year}-{month:02d}-01",
        "last_day": f"{year}-{month:02d}-{last_day:02d}",
    }


def dig(data: Any, path: str) -> Any:
    """'data.list' 같은 점 표기로 중첩 구조를 따라간다. 빈 경로면 그대로 돌려준다."""
    if not path:
        return data
    current = data
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(current, list):
            # 리스트 중간에 끼어 있으면 첫 요소를 따라간다.
            if not current:
                return None
            current = current[0]
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _render_field(item: dict[str, Any], spec: str) -> str:
    """필드명이면 값을, '{a}-{b}' 같은 템플릿이면 조합한 문자열을 돌려준다."""
    if "{" in spec:
        try:
            return spec.format(**item)
        except (KeyError, IndexError):
            return ""
    value = item.get(spec)
    return "" if value is None else str(value)


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        return None


def slots_from_items(
    cfg: ApiConfig,
    items: list[dict[str, Any]],
    zone_patterns: tuple[str, ...] | None = None,
) -> list[Slot]:
    """API 응답의 항목 목록을 Slot 목록으로 옮긴다.

    구역은 전용 필드(``api.zone_field``)가 있으면 그걸 쓰고, 없으면 캐빈 이름에서 뽑는다.
    """
    slots: list[Slot] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        stay_date = normalize_date(_render_field(item, cfg.date_field))
        if not stay_date:
            continue
        cabin = _render_field(item, cfg.cabin_field) if cfg.cabin_field else DATE_ONLY_CABIN
        remaining = _to_int(item.get(cfg.remaining_field)) if cfg.remaining_field else None

        if cfg.status_field:
            status = str(item.get(cfg.status_field, "")).strip()
            available = status in cfg.status_available_values
            # 상태가 '가능'이라도 잔여가 0이면 실제로는 못 잡는다.
            if available and remaining is not None:
                available = remaining > 0
        elif remaining is not None:
            available = remaining > 0
        else:
            # 판정 근거가 없으면 '목록에 있으면 예약 가능'으로 본다.
            available = True

        cabin_name = cabin.strip() or DATE_ONLY_CABIN
        if cfg.zone_field:
            zone = extract_zone(_render_field(item, cfg.zone_field), zone_patterns) or ""
        else:
            zone = extract_zone(cabin_name, zone_patterns)

        slots.append(
            Slot(
                stay_date=stay_date,
                cabin=cabin_name,
                available=available,
                remaining=remaining,
                price=_render_field(item, cfg.price_field) if cfg.price_field else "",
                zone=zone,
            )
        )
    return slots


class ApiSource:
    name = "api"

    def __init__(
        self,
        cfg: ApiConfig,
        request_context: Any,
        zone_patterns: tuple[str, ...] | None = None,
    ) -> None:
        self.cfg = cfg
        self.request = request_context  # Playwright APIRequestContext (쿠키 공유)
        self.zone_patterns = zone_patterns

    async def fetch(self, months: list[tuple[int, int]]) -> Snapshot:
        collected: list[Slot] = []
        for year, month in months:
            values = month_placeholders(year, month)
            url = self.cfg.url_template.format(**values)
            body = self.cfg.body_template.format(**values) if self.cfg.body_template else None
            options: dict[str, Any] = {"headers": self.cfg.headers or {}}
            if body:
                options["data"] = json.loads(body) if body.strip().startswith(("{", "[")) else body

            if self.cfg.method == "POST":
                response = await self.request.post(url, **options)
            else:
                response = await self.request.get(url, **options)
            if not response.ok:
                raise SourceError(f"재고 API가 {response.status} 를 돌려줬습니다: {url}")
            try:
                payload = await response.json()
            except Exception as exc:
                raise SourceError(f"재고 API 응답이 JSON이 아닙니다: {url} ({exc})") from exc

            items = dig(payload, self.cfg.items_path)
            if items is None:
                raise SourceError(
                    f"items_path '{self.cfg.items_path}' 로 목록을 찾지 못했습니다. "
                    f"`python -m paradogo sniff` 로 응답 구조를 다시 확인하세요."
                )
            if isinstance(items, dict):
                items = list(items.values())
            if not isinstance(items, list):
                raise SourceError(f"items_path 가 가리키는 값이 목록이 아닙니다: {type(items)}")
            collected.extend(slots_from_items(self.cfg, items, self.zone_patterns))

        return Snapshot(taken_at=now_kst(), slots=tuple(collected), source=self.name)


# 달력 셀을 통째로 읽어오는 스크립트. 셀마다 개별 조회하면 한 달에 수십 초가 걸린다.
_CELL_SCRAPE_JS = """
(els, attr) => els.map(el => ({
  raw: (attr && el.getAttribute(attr)) || (el.getAttribute('data-date') || ''),
  text: (el.innerText || '').trim(),
  cls: (el.className || '').toString(),
  disabled: el.hasAttribute('disabled')
            || el.getAttribute('aria-disabled') === 'true'
            || el.classList.contains('disabled'),
}))
"""


class DomSource:
    """달력 DOM에서 날짜 단위 가용 여부를 읽는다."""

    name = "dom"

    def __init__(self, flow: Any, smap: SelectorMap) -> None:
        self.flow = flow
        self.smap = smap

    @property
    def _soldout_tokens(self) -> list[str]:
        tokens = self.smap.candidates("booking.day_soldout_tokens")
        return tokens or ["soldout", "disabled", "마감", "예약불가", "예약마감"]

    @property
    def _date_attr(self) -> str:
        found = self.smap.candidates("booking.day_date_attr")
        return found[0] if found else "data-date"

    async def fetch(self, months: list[tuple[int, int]]) -> Snapshot:
        page = self.flow.page
        collected: list[Slot] = []

        for year, month in months:
            await self.flow.goto_booking()
            await self.flow.ensure_month(year, month)
            cells = await first_nonempty(page, self.smap, "booking.day_cell_all", required=False)
            if cells is None:
                raise SourceError(
                    "booking.day_cell_all 로 달력 셀을 찾지 못했습니다. "
                    "selectors.yaml 에서 달력의 모든 날짜 칸을 잡는 셀렉터를 지정하세요."
                )
            rows = await cells.evaluate_all(_CELL_SCRAPE_JS, self._date_attr)

            for row in rows:
                stay_date = normalize_date(row.get("raw") or "")
                if not stay_date or stay_date.count("-") != 2:
                    # 속성이 없으면 셀 텍스트의 '일'과 현재 달을 합쳐 만든다.
                    day_text = "".join(ch for ch in row.get("text", "") if ch.isdigit())[:2]
                    if not day_text:
                        continue
                    stay_date = f"{year}-{month:02d}-{int(day_text):02d}"

                haystack = f"{row.get('cls', '')} {row.get('text', '')}".lower()
                soldout = bool(row.get("disabled")) or any(
                    token.lower() in haystack for token in self._soldout_tokens
                )
                collected.append(
                    Slot(
                        stay_date=stay_date,
                        cabin=DATE_ONLY_CABIN,
                        available=not soldout,
                    )
                )

        # 같은 날짜가 여러 번 잡히면(중복 렌더링) 예약 가능한 쪽을 남긴다.
        merged: dict[tuple[str, str], Slot] = {}
        for slot in collected:
            existing = merged.get(slot.key)
            if existing is None or (slot.available and not existing.available):
                merged[slot.key] = slot
        return Snapshot(taken_at=now_kst(), slots=tuple(merged.values()), source=self.name)
