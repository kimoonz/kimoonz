from datetime import datetime, timedelta

from paradogo.clock import KST
from paradogo.inventory import Change, ChangeKind, Slot, Snapshot, diff
from paradogo.store import TrackerStore

T0 = datetime(2026, 10, 1, 9, 0, 0, tzinfo=KST)


def snap(slots, at=T0):
    return Snapshot(taken_at=at, slots=tuple(slots))


def slot(date="2026-10-03", cabin="캐빈 A", available=True, remaining=None):
    return Slot(stay_date=date, cabin=cabin, available=available, remaining=remaining)


def test_state_roundtrip_survives_restart(tmp_path):
    db = tmp_path / "t.db"
    with TrackerStore(db) as store:
        assert store.load_state() is None
        store.save_state(snap([slot(available=False), slot(cabin="캐빈 B", available=True)]))

    # 새 프로세스처럼 다시 연다.
    with TrackerStore(db) as store:
        restored = store.load_state()
    assert restored is not None
    assert {(s.cabin, s.available) for s in restored.slots} == {
        ("캐빈 A", False),
        ("캐빈 B", True),
    }


def test_restored_state_diffs_against_new_snapshot(tmp_path):
    # 재시작 직후에도 '원래 비어 있던 자리'와 '방금 풀린 자리'를 구분할 수 있어야 한다.
    db = tmp_path / "t.db"
    with TrackerStore(db) as store:
        store.save_state(snap([slot(available=False)]))
    with TrackerStore(db) as store:
        previous = store.load_state()
    changes = diff(previous, snap([slot(available=True)], T0 + timedelta(seconds=20)))
    assert [c.kind for c in changes] == [ChangeKind.OPENED]


def test_save_state_removes_slots_that_disappeared(tmp_path):
    with TrackerStore(tmp_path / "t.db") as store:
        store.save_state(snap([slot(cabin="A"), slot(cabin="B")]))
        store.save_state(snap([slot(cabin="A")]))
        restored = store.load_state()
    assert [s.cabin for s in restored.slots] == ["A"]


def test_events_are_recorded_and_counted(tmp_path):
    with TrackerStore(tmp_path / "t.db") as store:
        store.record_events(
            [
                Change(ChangeKind.OPENED, slot(), None, T0),
                Change(ChangeKind.CLOSED, slot(available=False), None, T0),
            ]
        )
        assert store.counts()["events"] == 2
        assert store.counts()["opened"] == 1
        assert [r["kind"] for r in store.recent_events(kinds=["opened"])] == ["opened"]


def test_record_events_with_empty_list_is_a_noop(tmp_path):
    with TrackerStore(tmp_path / "t.db") as store:
        store.record_events([])
        assert store.counts()["events"] == 0


def test_poll_health_tracks_failures(tmp_path):
    with TrackerStore(tmp_path / "t.db") as store:
        for _ in range(3):
            store.record_poll("api", True, 10, 120)
        store.record_poll("api", False, 0, 5000, "timeout")
        health = store.poll_health()
    assert health["count"] == 4
    assert health["success_rate"] == 0.75


def test_poll_health_on_empty_db(tmp_path):
    with TrackerStore(tmp_path / "t.db") as store:
        assert store.poll_health() == {"count": 0, "success_rate": 0.0, "avg_ms": 0.0}


def test_cancellation_stats_group_by_hour_and_date(tmp_path):
    with TrackerStore(tmp_path / "t.db") as store:
        store.record_events(
            [
                Change(ChangeKind.OPENED, slot("2026-10-03"), None, T0.replace(hour=9)),
                Change(ChangeKind.OPENED, slot("2026-10-03"), None, T0.replace(hour=9)),
                Change(ChangeKind.OPENED, slot("2026-10-05"), None, T0.replace(hour=22)),
                Change(ChangeKind.CLOSED, slot("2026-10-05"), None, T0.replace(hour=22)),
            ]
        )
        assert store.cancellation_by_hour() == [(9, 2), (22, 1)]
        assert store.cancellation_by_date() == [("2026-10-03", 2), ("2026-10-05", 1)]


def test_survival_time_measures_open_to_close(tmp_path):
    with TrackerStore(tmp_path / "t.db") as store:
        store.record_events([Change(ChangeKind.OPENED, slot(), None, T0)])
        store.record_events(
            [Change(ChangeKind.CLOSED, slot(available=False), None, T0 + timedelta(seconds=45))]
        )
        survival = store.survival_times()
    assert survival == [("2026-10-03", "캐빈 A", 45.0)]


def test_survival_ignores_close_without_matching_open(tmp_path):
    with TrackerStore(tmp_path / "t.db") as store:
        store.record_events([Change(ChangeKind.CLOSED, slot(available=False), None, T0)])
        assert store.survival_times() == []
