"""발송 이력 · 재시작 복구 검증."""

from datetime import date, datetime, timedelta, timezone

from bayesfutures.state import AlertState

DAY = 86400


def _now():
    return datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def test_first_signal_always_sends(tmp_path):
    st = AlertState.load(tmp_path)
    assert st.should_send("gold", "daily", "LONG", _now(), 3, DAY)


def test_same_bar_never_sends_twice(tmp_path):
    st = AlertState.load(tmp_path)
    bar = _now()
    st.record_signal("gold", "daily", "LONG", bar, 0.62)
    assert not st.should_send("gold", "daily", "LONG", bar, 3, DAY)


def test_cooldown_blocks_same_direction(tmp_path):
    st = AlertState.load(tmp_path)
    bar = _now()
    st.record_signal("gold", "daily", "LONG", bar, 0.62)
    assert not st.should_send("gold", "daily", "LONG", bar + timedelta(days=2), 3, DAY)
    assert st.should_send("gold", "daily", "LONG", bar + timedelta(days=3), 3, DAY)


def test_direction_change_bypasses_cooldown(tmp_path):
    """방향이 뒤집히면 쿨다운을 무시하고 즉시 알린다."""
    st = AlertState.load(tmp_path)
    bar = _now()
    st.record_signal("gold", "daily", "LONG", bar, 0.62)
    assert st.should_send("gold", "daily", "SHORT", bar + timedelta(days=1), 3, DAY)


def test_instruments_and_timeframes_are_independent(tmp_path):
    st = AlertState.load(tmp_path)
    bar = _now()
    st.record_signal("gold", "daily", "LONG", bar, 0.62)
    assert st.should_send("silver", "daily", "LONG", bar, 3, DAY)
    assert st.should_send("gold", "hourly", "LONG", bar, 3, DAY)


def test_survives_restart(tmp_path):
    """PC 재시작 후에도 같은 신호를 다시 보내지 않아야 한다."""
    st = AlertState.load(tmp_path)
    bar = _now()
    st.record_signal("gold", "daily", "LONG", bar, 0.62)
    reloaded = AlertState.load(tmp_path)          # 새 프로세스 흉내
    assert not reloaded.should_send("gold", "daily", "LONG", bar, 3, DAY)


def test_corrupt_state_file_does_not_crash(tmp_path):
    (tmp_path / "alerts.json").write_text("{망가진 json", encoding="utf-8")
    st = AlertState.load(tmp_path)
    assert st.should_send("gold", "daily", "LONG", _now(), 3, DAY)


def test_briefing_once_per_day(tmp_path):
    st = AlertState.load(tmp_path)
    today = date(2026, 8, 28)
    assert not st.briefing_sent_today(today)
    st.record_briefing(today)
    assert st.briefing_sent_today(today)
    assert not st.briefing_sent_today(date(2026, 8, 29))
    assert AlertState.load(tmp_path).briefing_sent_today(today)
