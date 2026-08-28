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


def test_open_datetime_for_stay_is_the_first_of_the_previous_month():
    from datetime import date

    from paradogo.clock import open_datetime_for_stay

    # 9월 투숙분은 8월 1일 09:00 에 열린다.
    assert open_datetime_for_stay(date(2026, 9, 19)) == dt(2026, 8, 1, 9, 0)
    assert open_datetime_for_stay(date(2026, 10, 3)) == dt(2026, 9, 1, 9, 0)


def test_open_datetime_for_stay_crosses_year_boundary():
    from datetime import date

    from paradogo.clock import open_datetime_for_stay

    assert open_datetime_for_stay(date(2027, 1, 2)) == dt(2026, 12, 1, 9, 0)


def test_open_datetime_for_stay_respects_custom_open_time():
    from datetime import date

    from paradogo.clock import open_datetime_for_stay

    assert open_datetime_for_stay(date(2026, 9, 19), day_of_month=5, hour=14) == dt(
        2026, 8, 5, 14, 0
    )
