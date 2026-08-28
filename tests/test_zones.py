import pytest

from paradogo.zones import DEFAULT_ZONE_PATTERNS, ZonePreference, extract_zone, normalize_zone


@pytest.mark.parametrize(
    "text,expected",
    [
        ("A구역 3번 캐빈", "A"),
        ("A 구역", "A"),
        ("구역 B", "B"),
        ("C존", "C"),
        ("존 C", "C"),
        ("D동 캐빈", "D"),
        ("E-03", "E"),
        ("E - 12", "E"),
        ("F타입 캐빈", "F"),
        ("프리미엄 캐빈 G", "G"),
        ("캐빈 (H)", "H"),
        ("c구역", "C"),
        ("프리미엄 캐빈", ""),
        ("", ""),
    ],
)
def test_extract_zone(text, expected):
    assert extract_zone(text) == expected


def test_custom_pattern_overrides_defaults():
    assert extract_zone("SITE:07:D", (r"SITE:\d+:([A-H])",)) == "D"


def test_broken_custom_pattern_does_not_crash():
    assert extract_zone("A구역", (r"([A-",)) == ""


def test_default_patterns_are_ordered_specific_first():
    # 'A구역' 이 '홀로 떨어진 한 글자' 규칙보다 먼저 잡혀야 한다.
    assert DEFAULT_ZONE_PATTERNS[0].startswith("([A-Za-z])")
    assert extract_zone("B 캐빈 A구역") == "A"


@pytest.mark.parametrize("raw,expected", [("a", "A"), (" B ", "B"), ("C구역", "C"), ("", "")])
def test_normalize_zone(raw, expected):
    assert normalize_zone(raw) == expected


def test_build_normalizes_and_deduplicates():
    pref = ZonePreference.build(["c", "C", "d"], ["a"])
    assert pref.wanted == ("C", "D")
    assert pref.excluded == frozenset({"A"})


def test_build_lets_exclusion_win_over_wanted():
    pref = ZonePreference.build(["A", "C"], ["A"])
    assert pref.wanted == ("C",)
    assert not pref.allows("A")


def test_allows_is_lenient_about_unknown_zone():
    # 감지·알림 단계에서 구역 미상을 걸러버리면 취소를 통째로 놓친다.
    assert ZonePreference.build(["C"]).allows("")


def test_selectable_is_strict_about_unknown_zone():
    assert not ZonePreference.build(["C"]).selectable("")
    assert ZonePreference.build(["C"], strict=False).selectable("")
    assert ZonePreference().selectable("")  # 구역을 안 따지면 상관없다


def test_rank_orders_by_preference_then_unknown():
    pref = ZonePreference.build(["C", "D"])
    assert pref.rank("C") < pref.rank("D") < pref.rank("E") <= pref.rank("")


def test_inactive_preference_allows_everything():
    pref = ZonePreference()
    assert not pref.active
    assert pref.allows("A") and pref.allows("") and pref.selectable("A")
