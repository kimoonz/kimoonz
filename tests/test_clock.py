from datetime import datetime

from paradogo.clock import (
    KST,
    ClockSync,
    humanize,
    next_open_datetime,
    target_stay_month,
)


def dt(*args):
    return datetime(*args, tzinfo=KST)


def test_before_open_returns_this_month():
    assert next_open_datetime(dt(2026, 9, 1, 8, 59)) == dt(2026, 9, 1, 9, 0)


def test_after_open_rolls_to_next_month():
    assert next_open_datetime(dt(2026, 9, 1, 9, 0, 1)) == dt(2026, 10, 1, 9, 0)


def test_exact_open_second_is_treated_as_passed():
    assert next_open_datetime(dt(2026, 9, 1, 9, 0, 0)) == dt(2026, 10, 1, 9, 0)


def test_year_rollover():
    assert next_open_datetime(dt(2026, 12, 5)) == dt(2027, 1, 1, 9, 0)


def test_day_clamped_to_short_month():
    # 2월에는 31일이 없으므로 말일로 맞춰진다.
    assert next_open_datetime(dt(2027, 2, 1), day_of_month=31) == dt(2027, 2, 28, 9, 0)


def test_stay_month_is_the_month_after_open():
    assert target_stay_month(dt(2026, 9, 1, 9, 0)) == (2026, 10)
    assert target_stay_month(dt(2026, 12, 1, 9, 0)) == (2027, 1)


def test_humanize():
    assert humanize(0) == "0초"
    assert humanize(-5) == "지남"
    assert humanize(3661) == "1시간 1분 1초"
    assert humanize(90000).startswith("1일")


def test_clock_sync_defaults_to_no_offset():
    clock = ClockSync()
    assert clock.offset_seconds == 0.0
    assert clock.measured is False
