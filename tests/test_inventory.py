from datetime import datetime

import pytest

from paradogo.inventory import (
    DATE_ONLY_CABIN,
    Change,
    ChangeKind,
    Slot,
    Snapshot,
    TargetFilter,
    diff,
    normalize_date,
)

T0 = datetime(2026, 10, 1, 9, 0, 0)
T1 = datetime(2026, 10, 1, 9, 0, 20)


def snap(slots, at=T1):
    return Snapshot(taken_at=at, slots=tuple(slots))


def slot(date="2026-10-03", cabin="캐빈 A", available=True, remaining=None):
    return Slot(stay_date=date, cabin=cabin, available=available, remaining=remaining)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("20261003", "2026-10-03"),
        ("2026-10-03", "2026-10-03"),
        ("2026.10.3", "2026-10-03"),
        ("2026/10/03", "2026-10-03"),
        (20261003, "2026-10-03"),
        ("", ""),
    ],
)
def test_normalize_date(raw, expected):
    assert normalize_date(raw) == expected


def test_first_snapshot_reports_no_changes():
    # 첫 관측에서 예약가능한 칸을 '취소'라고 알리면 전부 오탐이 된다.
    assert diff(None, snap([slot(available=True)])) == []


def test_soldout_to_available_is_a_cancellation():
    old = snap([slot(available=False)], T0)
    new = snap([slot(available=True)])
    changes = diff(old, new)
    assert [c.kind for c in changes] == [ChangeKind.OPENED]
    assert changes[0].kind.is_bookable


def test_available_to_soldout_is_closed():
    changes = diff(snap([slot(available=True)], T0), snap([slot(available=False)]))
    assert [c.kind for c in changes] == [ChangeKind.CLOSED]
    assert not changes[0].kind.is_bookable


def test_no_change_produces_nothing():
    assert diff(snap([slot(available=True)], T0), snap([slot(available=True)])) == []


def test_remaining_increase_is_restocked():
    old = snap([slot(available=True, remaining=1)], T0)
    new = snap([slot(available=True, remaining=3)])
    changes = diff(old, new)
    assert [c.kind for c in changes] == [ChangeKind.RESTOCKED]
    assert changes[0].previous.remaining == 1


def test_remaining_decrease_is_not_reported():
    old = snap([slot(available=True, remaining=3)], T0)
    new = snap([slot(available=True, remaining=1)])
    assert diff(old, new) == []


def test_new_key_is_appeared_and_missing_key_is_vanished():
    old = snap([slot(cabin="캐빈 A")], T0)
    new = snap([slot(cabin="캐빈 B")])
    kinds = {(c.cabin, c.kind) for c in diff(old, new)}
    assert kinds == {("캐빈 B", ChangeKind.APPEARED), ("캐빈 A", ChangeKind.VANISHED)}


def test_changes_are_sorted_by_date_then_cabin():
    old = snap([slot("2026-10-05", "B", False), slot("2026-10-03", "A", False)], T0)
    new = snap([slot("2026-10-05", "B", True), slot("2026-10-03", "A", True)])
    changes = diff(old, new)
    assert [(c.stay_date, c.cabin) for c in changes] == [
        ("2026-10-03", "A"),
        ("2026-10-05", "B"),
    ]


def test_target_filter_keeps_only_matching_openings():
    old = snap(
        [
            slot("2026-10-03", "프리미엄 캐빈", False),
            slot("2026-10-03", "스탠다드 캐빈", False),
            slot("2026-10-09", "프리미엄 캐빈", False),
        ],
        T0,
    )
    new = snap(
        [
            slot("2026-10-03", "프리미엄 캐빈", True),
            slot("2026-10-03", "스탠다드 캐빈", True),
            slot("2026-10-09", "프리미엄 캐빈", True),
        ]
    )
    targets = TargetFilter(frozenset({"2026-10-03"}), ("프리미엄",))
    bookable = targets.bookable(diff(old, new))
    assert [(c.stay_date, c.cabin) for c in bookable] == [("2026-10-03", "프리미엄 캐빈")]


def test_target_filter_ignores_cabin_when_only_calendar_is_known():
    # 달력만 읽는 폴백 모드에서 캐빈 조건으로 걸러버리면 취소를 통째로 놓친다.
    targets = TargetFilter(frozenset({"2026-10-03"}), ("프리미엄",))
    assert targets.matches(Slot("2026-10-03", DATE_ONLY_CABIN, True))


def test_target_filter_without_conditions_accepts_everything():
    targets = TargetFilter()
    assert targets.matches(slot("2027-01-01", "아무캐빈"))


def test_bookable_excludes_closed_and_unavailable():
    changes = [
        Change(ChangeKind.CLOSED, slot(available=False)),
        Change(ChangeKind.OPENED, slot(available=True)),
        Change(ChangeKind.VANISHED, slot(available=True)),
    ]
    assert [c.kind for c in TargetFilter().bookable(changes)] == [ChangeKind.OPENED]


def test_snapshot_helpers():
    s = snap([slot("2026-10-03", "A", True), slot("2026-10-04", "B", False)])
    assert s.available_count == 1
    assert s.dates() == ["2026-10-03", "2026-10-04"]
    assert s.cabins() == ["A", "B"]
