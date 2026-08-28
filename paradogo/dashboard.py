"""터미널 대시보드.

렌더링은 순수 함수로 두고(문자열만 돌려준다) 화면 갱신만 따로 한다. 그래야 표시
로직을 브라우저 없이 테스트할 수 있다.
"""

from __future__ import annotations

import calendar
import os
import sys
from collections import defaultdict
from datetime import datetime

from .inventory import Change, ChangeKind, Snapshot

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
INVERT = "\033[7m"

WEEKDAYS = "일 월 화 수 목 금 토"


def supports_color(stream: object | None = None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


class Palette:
    """색을 끌 수 있게 감싼다(파이프로 넘길 때나 로그 파일용)."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *codes: str) -> str:
        if not self.enabled or not codes:
            return text
        return "".join(codes) + text + RESET


def date_status(snapshot: Snapshot) -> dict[str, bool]:
    """날짜별로 '하나라도 예약 가능한가'를 집계한다."""
    status: dict[str, bool] = {}
    for slot in snapshot.slots:
        status[slot.stay_date] = status.get(slot.stay_date, False) or slot.available
    return status


def render_month(
    year: int,
    month: int,
    status: dict[str, bool],
    targets: set[str],
    palette: Palette | None = None,
) -> str:
    """한 달치 달력. ● 예약가능 / ○ 마감 / · 정보없음, 대상 날짜는 반전 표시."""
    paint = palette or Palette(False)
    lines = [paint(f"  {year}년 {month}월", BOLD), f"  {WEEKDAYS}"]
    for week in calendar.Calendar(firstweekday=6).monthdayscalendar(year, month):
        cells = []
        for day in week:
            if day == 0:
                cells.append("  ")
                continue
            iso = f"{year}-{month:02d}-{day:02d}"
            if iso not in status:
                mark, color = "·", DIM
            elif status[iso]:
                mark, color = "●", GREEN
            else:
                mark, color = "○", DIM
            cell = f"{day:2d}{mark}"
            cells.append(paint(cell, INVERT, color) if iso in targets else paint(cell, color))
        lines.append("  " + " ".join(cells))
    return "\n".join(lines)


def render_changes(changes: list[Change], palette: Palette | None = None, limit: int = 8) -> str:
    paint = palette or Palette(False)
    if not changes:
        return paint("  (변화 없음)", DIM)
    lines = []
    for change in changes[:limit]:
        color = {
            ChangeKind.OPENED: GREEN,
            ChangeKind.RESTOCKED: GREEN,
            ChangeKind.APPEARED: CYAN,
            ChangeKind.CLOSED: RED,
            ChangeKind.VANISHED: DIM,
        }[change.kind]
        stamp = change.at.strftime("%H:%M:%S") if change.at else "--:--:--"
        lines.append(f"  {paint(stamp, DIM)} {paint(str(change), color)}")
    if len(changes) > limit:
        lines.append(paint(f"  … 외 {len(changes) - limit}건", DIM))
    return "\n".join(lines)


def render_board(
    snapshot: Snapshot | None,
    targets: set[str],
    recent: list[Change],
    health: dict[str, float | int],
    next_poll_in: float,
    round_no: int,
    opened_total: int = 0,
    color: bool = True,
) -> str:
    """대시보드 전체."""
    paint = Palette(color)
    out: list[str] = []
    out.append(paint("═" * 62, DIM))
    out.append(paint(" 파라다이스 도고 캐빈 재고 추적", BOLD))
    out.append(paint("═" * 62, DIM))

    if snapshot is None:
        out.append(paint("  아직 첫 스냅샷을 받지 못했습니다…", DIM))
    else:
        status = date_status(snapshot)
        months = sorted({(int(d[:4]), int(d[5:7])) for d in status})
        for year, month in months:
            out.append("")
            out.append(render_month(year, month, status, targets, paint))
        out.append("")
        out.append(
            f"  {paint('●', GREEN)} 예약가능  {paint('○', DIM)} 마감  "
            f"{paint('·', DIM)} 정보없음  {paint(' 대상 ', INVERT)}"
        )
        out.append(
            paint(
                f"  스냅샷 {snapshot.taken_at.strftime('%H:%M:%S')} · "
                f"{len(snapshot.slots)}칸 중 {snapshot.available_count}칸 예약가능 · "
                f"경로 {snapshot.source}",
                DIM,
            )
        )

    out.append("")
    out.append(paint(" 최근 변화", BOLD))
    out.append(render_changes(recent, paint))

    out.append("")
    rate = float(health.get("success_rate", 0.0)) * 100
    rate_color = GREEN if rate >= 95 else (YELLOW if rate >= 70 else RED)
    out.append(
        f"  {round_no}회차 · 폴링 성공률 {paint(f'{rate:.0f}%', rate_color)}"
        f" (최근 {int(health.get('count', 0))}회, 평균 {float(health.get('avg_ms', 0)):.0f}ms)"
        f" · 누적 취소 감지 {paint(str(opened_total), BOLD)}건"
        f" · 다음 확인 {next_poll_in:.0f}초 후"
    )
    out.append(paint("  Ctrl+C 로 종료", DIM))
    return "\n".join(out)


def clear_screen() -> None:
    sys.stdout.write("\033[H\033[J")
    sys.stdout.flush()


def draw(text: str) -> None:
    clear_screen()
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
