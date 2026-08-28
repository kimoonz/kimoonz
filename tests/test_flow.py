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
