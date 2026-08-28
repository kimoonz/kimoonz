from datetime import datetime

from paradogo.dashboard import Palette, date_status, render_board, render_changes, render_month
from paradogo.inventory import Change, ChangeKind, Slot, Snapshot


def snap(slots):
    return Snapshot(taken_at=datetime(2026, 10, 1, 9, 0), slots=tuple(slots), source="api")


def test_date_status_is_true_if_any_cabin_is_open():
    s = snap(
        [
            Slot("2026-10-03", "A", False),
            Slot("2026-10-03", "B", True),
            Slot("2026-10-04", "A", False),
        ]
    )
    assert date_status(s) == {"2026-10-03": True, "2026-10-04": False}


def test_render_month_marks_open_soldout_and_unknown():
    board = render_month(2026, 10, {"2026-10-03": True, "2026-10-04": False}, set())
    assert " 3●" in board
    assert " 4○" in board
    assert " 5·" in board  # 정보 없는 날


def test_render_month_has_a_row_per_week():
    board = render_month(2026, 10, {}, set()).splitlines()
    assert board[0].strip() == "2026년 10월"
    assert board[1].strip().startswith("일 월")
    assert len(board) >= 6


def test_palette_can_be_disabled():
    assert Palette(False)("x", "\033[32m") == "x"
    assert Palette(True)("x", "\033[32m").startswith("\033[32m")


def test_render_changes_shows_placeholder_when_empty():
    assert "변화 없음" in render_changes([])


def test_render_changes_truncates_long_lists():
    changes = [
        Change(ChangeKind.OPENED, Slot(f"2026-10-{d:02d}", "A", True), None,
               datetime(2026, 10, 1, 9, 0))
        for d in range(1, 13)
    ]
    out = render_changes(changes, limit=3)
    assert out.count("\n") == 3  # 3건 + '외 N건'
    assert "외 9건" in out


def test_render_board_without_snapshot_does_not_crash():
    out = render_board(None, set(), [], {}, 10.0, 1, color=False)
    assert "첫 스냅샷" in out


def test_render_board_includes_health_and_counts():
    s = snap([Slot("2026-10-03", "A", True)])
    out = render_board(
        s, {"2026-10-03"}, [], {"success_rate": 0.5, "count": 10, "avg_ms": 200}, 12.0, 7, 3,
        color=False,
    )
    assert "7회차" in out
    assert "50%" in out
    assert "누적 취소 감지 3건" in out
    assert "1칸 중 1칸 예약가능" in out
