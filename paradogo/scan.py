"""지금 예약 가능한 날짜를 한 번 읽어서 보여준다 (읽기 전용).

추적기를 켜기 전에 이걸로 먼저 확인하는 게 안전하다. 셀렉터나 api 설정이 틀렸으면
여기서 바로 드러나고, 아무것도 클릭하지 않으므로 잘못 눌릴 위험이 없다.
"""

from __future__ import annotations

import logging
from datetime import date

from .browser import BrowserSession
from .clock import now_kst
from .config import Config
from .dashboard import Palette, date_status, render_month, supports_color
from .flow import BookingFlow
from .inventory import DATE_ONLY_CABIN, Snapshot
from .selectors import SelectorMap
from .sources import ApiSource, DomSource
from .tracker import months_to_track

log = logging.getLogger(__name__)


def render_scan(snapshot: Snapshot, targets: set[str], color: bool = True) -> str:
    """달력 + 예약 가능 목록."""
    paint = Palette(color)
    status = date_status(snapshot)
    months = sorted({(int(d[:4]), int(d[5:7])) for d in status})

    out: list[str] = []
    for year, month in months:
        out.append(render_month(year, month, status, targets, paint))
        out.append("")

    available = sorted(
        (s for s in snapshot.slots if s.available), key=lambda s: (s.stay_date, s.cabin)
    )
    if available:
        out.append(f"예약 가능 {len(available)}건")
        for slot in available:
            mark = " ←대상" if slot.stay_date in targets else ""
            cabin = "" if slot.cabin == DATE_ONLY_CABIN else f" {slot.cabin}"
            zone = f" [{slot.zone}]" if slot.zone else ""
            rest = f" (잔여 {slot.remaining})" if slot.remaining is not None else ""
            price = f" {slot.price}" if slot.price else ""
            out.append(f"  {slot.stay_date}{zone}{cabin}{rest}{price}{mark}")
    else:
        out.append("예약 가능한 자리가 없습니다. (전부 마감)")

    # 구역이 실제로 읽히는지 여기서 확인할 수 있어야 한다.
    # 전부 '미상'이면 zones 설정이 아무 효과도 내지 못한다는 뜻이다.
    by_zone: dict[str, int] = {}
    for slot in snapshot.slots:
        if slot.cabin != DATE_ONLY_CABIN:
            by_zone[slot.zone or "미상"] = by_zone.get(slot.zone or "미상", 0) + 1
    if by_zone:
        out.append("")
        out.append("구역 인식 결과: " + ", ".join(
            f"{name} {count}칸" for name, count in sorted(by_zone.items())
        ))
        if set(by_zone) == {"미상"}:
            out.append(
                "  구역을 하나도 읽지 못했습니다. target.zones 를 써도 효과가 없습니다."
                " → target.zone_pattern 에 정규식을 직접 주거나,"
                " selectors.yaml 의 booking.room_zone 을 지정하세요."
            )

    missing = sorted(targets - set(status))
    if missing:
        out.append("")
        out.append(
            "설정한 대상 날짜인데 조회 결과에 없습니다(아직 오픈 전이거나 조회 범위 밖): "
            + ", ".join(missing)
        )

    out.append("")
    out.append(
        f"조회 경로 {snapshot.source} · {len(snapshot.slots)}칸 · "
        f"{snapshot.taken_at.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    return "\n".join(out)


async def run_scan(cfg: Config, smap: SelectorMap, months_ahead: int | None = None) -> Snapshot:
    """재고를 한 번만 읽는다. 로그인은 저장된 세션이 있을 때만 쓰고 새로 시도하지 않는다."""
    async with BrowserSession(cfg) as session:
        flow = BookingFlow(session, smap, cfg)
        await flow.goto_booking()
        if not await flow.is_logged_in():
            log.warning(
                "로그인 상태가 아닙니다. 비로그인 상태에서 보이는 재고만 읽습니다. "
                "→ `python -m paradogo login --manual` 로 세션을 먼저 만들어 두세요."
            )

        if cfg.api.usable:
            source = ApiSource(cfg.api, session.context.request, cfg.target.zone_patterns)
        else:
            source = DomSource(flow, smap)
        log.info("조회 경로: %s", source.name)

        months = months_to_track(
            now_kst(),
            cfg.target.check_in_dates,
            months_ahead or cfg.run.track.months_ahead,
        )
        log.info("조회 범위: %s", ", ".join(f"{y}-{m:02d}" for y, m in months))
        return await source.fetch(months)
