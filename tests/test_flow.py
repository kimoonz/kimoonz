import pytest

from paradogo.config import Config
from paradogo.flow import BookingFlow, Offer, month_distance, parse_month_label

BASE = {
    "site": {"base_url": "https://example.test"},
    "account": {"login_id": "u", "password": "p"},
    "target": {"check_in_dates": ["2026-10-03"]},
}


def make_flow(cabin_types):
    raw = {**BASE, "target": {"check_in_dates": ["2026-10-03"], "cabin_types": cabin_types}}
    return BookingFlow(session=None, smap=None, cfg=Config.from_dict(raw))


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026년 10월", (2026, 10)),
        ("2026.10", (2026, 10)),
        ("2026-10", (2026, 10)),
        (" 10월 ", (None, 10)),
        ("2027 년 1 월", (2027, 1)),
    ],
)
def test_parse_month_label(text, expected):
    assert parse_month_label(text) == expected


def test_parse_month_label_rejects_garbage():
    with pytest.raises(ValueError):
        parse_month_label("예약하기")


def test_month_distance_forward_and_backward():
    assert month_distance((2026, 9), (2026, 10)) == 1
    assert month_distance((2026, 12), (2027, 2)) == 2
    assert month_distance((2026, 11), (2026, 9)) == -2


def test_month_distance_without_year_never_goes_backward():
    # 연도를 못 읽으면 뒤로 가는 대신 12개월 안에서 앞으로만 센다.
    assert month_distance((None, 12), (2027, 2)) == 2
    assert month_distance((None, 3), (2027, 1)) == 10


OFFERS = [
    Offer(index=0, name="스탠다드 캐빈 A", stay_date="2026-10-03"),
    Offer(index=1, name="프리미엄 캐빈 B", stay_date="2026-10-03"),
]


def test_pick_offer_without_preference_takes_first():
    assert make_flow([]).pick_offer(OFFERS).index == 0


def test_pick_offer_respects_priority_order():
    assert make_flow(["프리미엄", "스탠다드"]).pick_offer(OFFERS).index == 1


def test_pick_offer_falls_through_to_second_preference():
    assert make_flow(["디럭스", "스탠다드"]).pick_offer(OFFERS).index == 0


def test_pick_offer_returns_none_when_nothing_matches():
    assert make_flow(["디럭스"]).pick_offer(OFFERS) is None


def test_pick_offer_on_empty_list():
    assert make_flow([]).pick_offer([]) is None


def test_offer_str_includes_date_and_price():
    offer = Offer(index=0, name="캐빈 A", price="250,000원", stay_date="2026-10-03")
    assert str(offer) == "2026-10-03 캐빈 A / 250,000원"


# --- 추적기 보조 로직 -------------------------------------------------------


def test_upcoming_months_rolls_over_year():
    from datetime import datetime

    from paradogo.tracker import upcoming_months

    assert upcoming_months(datetime(2026, 11, 15), 3) == [(2026, 11), (2026, 12), (2027, 1)]
    assert upcoming_months(datetime(2026, 11, 15), 1) == [(2026, 11)]
    assert upcoming_months(datetime(2026, 11, 15), 0) == [(2026, 11)]


def test_tracker_cooldown_blocks_immediate_retry(tmp_path):
    from paradogo.notify import Notifier
    from paradogo.store import TrackerStore
    from paradogo.tracker import Tracker

    cfg = Config.from_dict({**BASE, "run": {"track": {"reserve_cooldown_minutes": 10}}})
    with TrackerStore(tmp_path / "t.db") as store:
        tracker = Tracker(cfg, smap=None, notifier=Notifier(cfg.notify), store=store)
        key = ("2026-10-03", "캐빈 A")
        assert tracker.can_attempt(key)
        tracker.mark_attempt(key)
        assert not tracker.can_attempt(key)
        assert tracker.can_attempt(("2026-10-04", "캐빈 A"))


def test_tracker_cooldown_disabled_allows_retry(tmp_path):
    from paradogo.notify import Notifier
    from paradogo.store import TrackerStore
    from paradogo.tracker import Tracker

    cfg = Config.from_dict({**BASE, "run": {"track": {"reserve_cooldown_minutes": 0}}})
    with TrackerStore(tmp_path / "t.db") as store:
        tracker = Tracker(cfg, smap=None, notifier=Notifier(cfg.notify), store=store)
        tracker.mark_attempt(("2026-10-03", "캐빈 A"))
        assert tracker.can_attempt(("2026-10-03", "캐빈 A"))


def test_tracker_targets_come_from_config(tmp_path):
    from paradogo.notify import Notifier
    from paradogo.store import TrackerStore
    from paradogo.tracker import Tracker

    cfg = Config.from_dict(
        {**BASE, "target": {"check_in_dates": ["2026-10-03"], "cabin_types": ["프리미엄"]}}
    )
    with TrackerStore(tmp_path / "t.db") as store:
        tracker = Tracker(cfg, smap=None, notifier=Notifier(cfg.notify), store=store)
    assert tracker.targets.dates == frozenset({"2026-10-03"})
    assert tracker.targets.cabin_keywords == ("프리미엄",)


def test_months_to_track_always_includes_target_months():
    # 8월에 10월 예약을 노리는 경우, '오늘부터 2개월'만 보면 목표를 통째로 놓친다.
    from datetime import date, datetime

    from paradogo.tracker import months_to_track

    months = months_to_track(datetime(2026, 8, 28), [date(2026, 10, 3)], 2)
    assert months == [(2026, 8), (2026, 9), (2026, 10)]


def test_months_to_track_without_targets_uses_lookahead():
    from datetime import datetime

    from paradogo.tracker import months_to_track

    assert months_to_track(datetime(2026, 8, 1), [], 2) == [(2026, 8), (2026, 9)]


def test_months_to_track_deduplicates_and_sorts():
    from datetime import date, datetime

    from paradogo.tracker import months_to_track

    months = months_to_track(datetime(2026, 12, 1), [date(2026, 12, 25), date(2027, 1, 2)], 2)
    assert months == [(2026, 12), (2027, 1)]
