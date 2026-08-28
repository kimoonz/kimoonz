from datetime import date

import yaml

from paradogo.config import Config
from paradogo.wizard import (
    FALLBACKS,
    check_calendar,
    check_guest,
    check_login_form,
    check_rooms,
    fill_defaults,
    merge_selectors,
    nest,
    render_config,
    soldout_selector,
)

LOGIN_SCREEN = {
    "inputs": [
        {"tag": "input", "type": "text", "id": "userId", "name": "userId",
         "placeholder": "아이디", "selector": "#userId"},
        {"tag": "input", "type": "password", "id": "userPw", "name": "userPw",
         "placeholder": "비밀번호", "selector": "#userPw"},
    ],
    "clickables": [{"text": "로그인", "href": "", "selector": "button.login"}],
    "dateish": [],
    "repeated": [],
}

CALENDAR_SCREEN = {
    "inputs": [],
    "clickables": [
        {"text": "로그아웃", "href": "/logout", "selector": "a.logout"},
        {"text": "다음달", "href": "", "selector": "button.next"},
    ],
    "dateish": [],
    "repeated": [],
}

CALENDAR_EXTRA = {
    "monthLabels": [{"text": "2026년 9월", "selector": "span.month"}],
    "dayAttrs": [{"attr": "data-date", "tag": "td", "count": 30}],
    "soldoutTokens": ["soldout", "마감"],
}

ROOMS_SCREEN = {
    "inputs": [],
    "clickables": [{"text": "예약하기", "href": "", "selector": "button.btn-reserve"}],
    "dateish": [],
    "repeated": [{"selector": "li.room-item", "count": 8}],
}


# --- 단계별 화면 검증 -------------------------------------------------------


def test_login_screen_is_recognized_by_password_field():
    ok, detail = check_login_form(LOGIN_SCREEN)
    assert ok and "비밀번호" in detail


def test_login_screen_rejected_without_password_field():
    ok, detail = check_login_form(ROOMS_SCREEN)
    assert not ok and "로그인 화면" in detail


def test_calendar_recognized_by_day_cells():
    ok, detail = check_calendar(CALENDAR_SCREEN, CALENDAR_EXTRA)
    assert ok and "30" in detail


def test_calendar_recognized_by_month_label_alone():
    extra = {"monthLabels": CALENDAR_EXTRA["monthLabels"], "dayAttrs": [], "soldoutTokens": []}
    ok, detail = check_calendar(CALENDAR_SCREEN, extra)
    assert ok and "2026년 9월" in detail


def test_calendar_warns_when_login_marker_missing():
    screen = {**CALENDAR_SCREEN, "clickables": [{"text": "다음달", "href": "", "selector": ".next"}]}
    ok, detail = check_calendar(screen, CALENDAR_EXTRA)
    assert ok and "로그인 표시" in detail


def test_calendar_rejected_when_nothing_calendar_like():
    ok, detail = check_calendar(LOGIN_SCREEN, {"monthLabels": [], "dayAttrs": []})
    assert not ok and "달력" in detail


def test_rooms_recognized_by_repeat_and_reserve_button():
    ok, detail = check_rooms(ROOMS_SCREEN)
    assert ok and "8" in detail


def test_rooms_rejected_without_reserve_button():
    ok, detail = check_rooms({**ROOMS_SCREEN, "clickables": []})
    assert not ok and "예약하기" in detail


def test_rooms_rejected_without_repeated_items():
    ok, detail = check_rooms({**ROOMS_SCREEN, "repeated": []})
    assert not ok and "반복" in detail


def test_guest_screen_needs_a_couple_of_inputs():
    assert check_guest(LOGIN_SCREEN)[0] is True
    assert check_guest({"inputs": []})[0] is False


# --- 셀렉터 조합 ------------------------------------------------------------


def test_success_marker_comes_from_the_post_login_screen_only():
    # 로그인 '전' 화면의 '로그인' 링크를 성공 표식으로 삼으면 항상 로그인 상태로 오판한다.
    merged = merge_selectors(
        {"login_form": LOGIN_SCREEN, "calendar": CALENDAR_SCREEN}, CALENDAR_EXTRA
    )
    assert merged["login.success_marker"] == ["text=로그아웃", "a.logout"]
    assert merged["login.submit"] == ["text=로그인", "button.login"]


def test_calendar_attributes_become_day_selectors():
    merged = merge_selectors({}, CALENDAR_EXTRA)
    assert merged["booking.day_cell"] == ["[data-date='{date}']"]
    assert merged["booking.day_cell_all"] == ["td[data-date]", "[data-date]"]
    assert merged["booking.day_date_attr"] == ["data-date"]
    # 체크아웃도 같은 달력에서 고르므로 같은 셀렉터가 쓰인다(1박/2박 지정에 필요).
    assert merged["booking.checkout_cell"] == ["[data-date='{date}']"]


def test_month_label_is_taken_from_calendar_scan():
    merged = merge_selectors({}, CALENDAR_EXTRA)
    assert merged["booking.month_label"] == ["span.month"]


def test_soldout_tokens_become_class_or_text_selectors():
    merged = merge_selectors({}, CALENDAR_EXTRA)
    assert merged["booking.day_soldout_marker"] == [".soldout", "text=마감"]
    assert merged["booking.day_soldout_tokens"] == ["soldout", "마감"]


def test_soldout_selector_distinguishes_class_from_text():
    assert soldout_selector("sold-out") == ".sold-out"
    assert soldout_selector("마감") == "text=마감"
    assert soldout_selector("예약불가") == "text=예약불가"


def test_room_card_comes_from_the_room_screen():
    merged = merge_selectors({"rooms": ROOMS_SCREEN}, {})
    assert "li.room-item" in merged["booking.room_card"]


def test_empty_stages_produce_nothing_but_do_not_crash():
    assert merge_selectors({}, {}) == {}


def test_fill_defaults_keeps_discovered_and_fills_the_rest():
    filled = fill_defaults({"login.id_input": ["#myId"]})
    assert filled["login.id_input"] == ["#myId"]
    assert filled["payment.marker"] == FALLBACKS["payment.marker"]
    # 필수 키가 하나도 비지 않아야 바로 실행할 수 있다.
    required = ["login.pw_input", "booking.day_cell", "booking.room_card", "payment.marker"]
    assert all(filled[key] for key in required)


def test_nest_turns_dotted_keys_into_yaml_blocks():
    assert nest({"login.id_input": ["#a"], "payment.marker": ["#b"]}) == {
        "login": {"id_input": ["#a"]},
        "payment": {"marker": ["#b"]},
    }


# --- 설정 파일 생성 ---------------------------------------------------------


def test_rendered_config_is_valid_and_loadable(tmp_path, monkeypatch):
    monkeypatch.delenv("PARADOGO_ID", raising=False)
    text = render_config(
        base_url="https://www.paradisespa.co.kr",
        login_path="/member/login",
        booking_path="/reservation/cabin",
        dates=[date(2026, 9, 19)],
        nights=[2, 1],
        zones=["C", "D"],
        exclude_zones=["A"],
        api={"enabled": True, "url": "https://x/{year}{month02}", "items_path": "data.list",
             "date_field": "rsvDate"},
    )
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")

    cfg = Config.load(path)
    assert cfg.target.check_in_dates == [date(2026, 9, 19)]
    assert cfg.target.nights_options == [2, 1]
    assert cfg.target.zones == ["C", "D"]
    assert cfg.target.exclude_zones == ["A"]
    assert cfg.api.usable
    # 마법사가 만든 설정은 바로 예약까지 진행하도록 dry_run 이 꺼져 있어야 한다.
    assert cfg.run.dry_run is False


def test_rendered_config_without_api_falls_back_to_dom(tmp_path):
    text = render_config(
        base_url="https://www.paradisespa.co.kr",
        login_path="/login",
        booking_path="/cabin",
        dates=[date(2026, 9, 19)],
        nights=[1],
        zones=[],
        exclude_zones=[],
        api=None,
    )
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    cfg = Config.load(path)
    assert not cfg.api.usable
    assert cfg.target.zones == []


def test_rendered_config_never_stores_a_password_literal():
    text = render_config(
        base_url="https://www.paradisespa.co.kr", login_path="/l", booking_path="/b",
        dates=[date(2026, 9, 19)], nights=[1], zones=[], exclude_zones=[],
    )
    raw = yaml.safe_load(text)
    assert raw["account"]["password"].startswith("${")
    assert raw["account"]["login_id"].startswith("${")
