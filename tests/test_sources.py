import pytest

from paradogo.config import ApiConfig
from paradogo.inventory import DATE_ONLY_CABIN
from paradogo.sources import dig, month_placeholders, slots_from_items


def cfg(**kw):
    base = dict(enabled=True, url_template="https://x/{ym}", date_field="rsvDate")
    base.update(kw)
    return ApiConfig(**base)


def test_month_placeholders():
    values = month_placeholders(2026, 10)
    assert values["ym"] == "202610"
    assert values["month02"] == "10"
    assert values["first_day"] == "2026-10-01"
    assert values["last_day"] == "2026-10-31"


def test_month_placeholders_handles_february_leap():
    assert month_placeholders(2028, 2)["last_day"] == "2028-02-29"


def test_url_template_renders():
    values = month_placeholders(2026, 10)
    assert "https://x/api?ym=202610".format() == "https://x/api?ym={ym}".format(**values)


@pytest.mark.parametrize(
    "path,expected",
    [("", {"a": 1}), ("a", 1), ("b.c", None), ("a.b", None)],
)
def test_dig_paths(path, expected):
    assert dig({"a": 1}, path) == expected


def test_dig_nested_and_through_list():
    payload = {"data": {"list": [1, 2]}}
    assert dig(payload, "data.list") == [1, 2]
    assert dig({"data": [{"list": [7]}]}, "data.list") == [7]


def test_remaining_field_decides_availability():
    items = [
        {"rsvDate": "20261003", "roomNm": "캐빈 A", "restCnt": 2},
        {"rsvDate": "20261004", "roomNm": "캐빈 A", "restCnt": 0},
    ]
    slots = slots_from_items(cfg(cabin_field="roomNm", remaining_field="restCnt"), items)
    assert [(s.stay_date, s.available, s.remaining) for s in slots] == [
        ("2026-10-03", True, 2),
        ("2026-10-04", False, 0),
    ]


def test_status_field_decides_availability():
    items = [
        {"rsvDate": "2026-10-03", "status": "Y"},
        {"rsvDate": "2026-10-04", "status": "N"},
    ]
    slots = slots_from_items(
        cfg(status_field="status", status_available_values=["Y"]), items
    )
    assert [(s.stay_date, s.available) for s in slots] == [
        ("2026-10-03", True),
        ("2026-10-04", False),
    ]


def test_zero_remaining_overrides_available_status():
    # 상태는 '가능'인데 잔여가 0인 응답을 그대로 믿으면 헛걸음을 한다.
    items = [{"rsvDate": "2026-10-03", "status": "Y", "restCnt": 0}]
    slots = slots_from_items(
        cfg(status_field="status", status_available_values=["Y"], remaining_field="restCnt"),
        items,
    )
    assert slots[0].available is False


def test_missing_cabin_field_falls_back_to_date_only():
    slots = slots_from_items(cfg(), [{"rsvDate": "20261003"}])
    assert slots[0].cabin == DATE_ONLY_CABIN
    assert slots[0].available is True  # 판정 근거가 없으면 '있으면 가능'


def test_date_field_can_be_a_template():
    items = [{"y": "2026", "m": "10", "d": "03"}]
    slots = slots_from_items(cfg(date_field="{y}-{m}-{d}"), items)
    assert slots[0].stay_date == "2026-10-03"


def test_items_without_date_are_skipped():
    slots = slots_from_items(cfg(), [{"roomNm": "캐빈"}, "쓰레기", {"rsvDate": "20261003"}])
    assert len(slots) == 1


def test_price_and_comma_separated_remaining():
    items = [{"rsvDate": "20261003", "restCnt": "1,000", "price": "320,000원"}]
    slots = slots_from_items(cfg(remaining_field="restCnt", price_field="price"), items)
    assert slots[0].remaining == 1000
    assert slots[0].price == "320,000원"
