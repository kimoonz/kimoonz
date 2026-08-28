from paradogo.sniff import find_item_arrays, guess_mapping, score_candidate, templatize_url

PAYLOAD = {
    "code": "0000",
    "data": {
        "list": [
            {"rsvDate": "20261003", "roomNm": "프리미엄 캐빈 B", "restCnt": 2, "price": 320000},
            {"rsvDate": "20261004", "roomNm": "프리미엄 캐빈 B", "restCnt": 0, "price": 320000},
        ]
    },
}


def test_find_item_arrays_locates_nested_list():
    paths = [path for path, _ in find_item_arrays(PAYLOAD)]
    assert "data.list" in paths


def test_guess_mapping_picks_fields():
    guess = guess_mapping("https://x/api?year=2026&month=10", PAYLOAD)
    assert guess["items_path"] == "data.list"
    assert guess["date_field"] == "rsvDate"
    assert guess["cabin_field"] == "roomNm"
    assert guess["remaining_field"] == "restCnt"
    assert guess["price_field"] == "price"


def test_guess_mapping_templatizes_year_and_month():
    guess = guess_mapping("https://x/api?year=2026&month=10", PAYLOAD)
    assert guess["url"] == "https://x/api?year={year}&month={month02}"


def test_guess_mapping_returns_none_for_unrelated_json():
    assert guess_mapping("https://x/config", {"banners": [{"img": "a.png"}]}) is None


def test_guess_mapping_returns_none_without_arrays():
    assert guess_mapping("https://x/ping", {"ok": True}) is None


def test_status_field_used_when_no_remaining_count():
    payload = {"items": [{"dt": "2026-10-03", "saleStat": "Y"}]}
    guess = guess_mapping("https://x/a", payload)
    assert guess["date_field"] == "dt"
    assert guess["status_field"] == "saleStat"
    assert guess["remaining_field"] == ""


def test_score_prefers_date_bearing_items():
    assert score_candidate({"rsvDate": "20261003", "restCnt": 1}) > score_candidate(
        {"img": "a.png", "title": "이벤트"}
    )


def test_templatize_url_variants():
    assert templatize_url("https://x/a?ym=202610") == "https://x/a?ym={year}{month02}"
    assert templatize_url("https://x/a?YEAR=2026&MONTH=10") == (
        "https://x/a?YEAR={year}&MONTH={month02}"
    )
    assert templatize_url("https://x/a/2026-10") == "https://x/a/{year}-{month02}"
    assert templatize_url("https://x/plain") == "https://x/plain"
