import pytest

from paradogo.errors import SelectorNotFound
from paradogo.selectors import (
    SelectorMap,
    first_nonempty,
    first_visible,
    is_present,
)

RAW = {
    "login": {"id_input": ["#a", "#b"], "submit": "text=로그인"},
    "booking": {"day_cell": ["[data-date='{date}']", "td.day"], "empty": []},
}


class FakeLocator:
    def __init__(self, selector, visible_set, counts):
        self.selector = selector
        self._visible = visible_set
        self._counts = counts

    @property
    def first(self):
        return self

    async def wait_for(self, state="visible", timeout=0):
        if self.selector not in self._visible:
            raise TimeoutError(f"{self.selector} not visible")

    async def count(self):
        return self._counts.get(self.selector, 0)


class FakePage:
    def __init__(self, visible=(), counts=None):
        self.visible = set(visible)
        self.counts = counts or {}
        self.queried: list[str] = []

    def locator(self, selector):
        self.queried.append(selector)
        return FakeLocator(selector, self.visible, self.counts)


def test_flatten_nested_and_scalar():
    smap = SelectorMap.from_dict(RAW)
    assert smap.entries["login.id_input"] == ["#a", "#b"]
    assert smap.entries["login.submit"] == ["text=로그인"]
    assert smap.entries["booking.empty"] == []


def test_placeholder_substitution():
    smap = SelectorMap.from_dict(RAW)
    assert smap.candidates("booking.day_cell", date="2026-10-03")[0] == "[data-date='2026-10-03']"


def test_unknown_placeholder_keeps_raw_candidate():
    smap = SelectorMap.from_dict({"x": ["div[style*='{unknown}']"]})
    assert smap.candidates("x", date="d") == ["div[style*='{unknown}']"]


def test_has_and_missing():
    smap = SelectorMap.from_dict(RAW)
    assert smap.has("login.id_input")
    assert not smap.has("booking.empty")
    assert smap.missing(["login.id_input", "booking.empty", "nope"]) == ["booking.empty", "nope"]


async def test_first_visible_falls_through_to_second_candidate():
    smap = SelectorMap.from_dict(RAW)
    page = FakePage(visible={"#b"})
    found = await first_visible(page, smap, "login.id_input", timeout_ms=100)
    assert found.selector == "#b"
    assert page.queried == ["#a", "#b"]


async def test_first_visible_raises_with_candidate_list():
    smap = SelectorMap.from_dict(RAW)
    page = FakePage(visible=set())
    with pytest.raises(SelectorNotFound) as excinfo:
        await first_visible(page, smap, "login.id_input", timeout_ms=100)
    assert excinfo.value.candidates == ["#a", "#b"]
    assert "#a" in str(excinfo.value)


async def test_first_visible_optional_returns_none():
    smap = SelectorMap.from_dict(RAW)
    page = FakePage(visible=set())
    assert await first_visible(page, smap, "login.id_input", 100, required=False) is None


async def test_empty_candidate_list_is_selector_not_found():
    smap = SelectorMap.from_dict(RAW)
    with pytest.raises(SelectorNotFound):
        await first_visible(FakePage(), smap, "booking.empty", timeout_ms=50)


async def test_is_present():
    smap = SelectorMap.from_dict(RAW)
    assert await is_present(FakePage(visible={"#a"}), smap, "login.id_input", 100) is True
    assert await is_present(FakePage(), smap, "login.id_input", 100) is False


async def test_first_nonempty_picks_candidate_with_matches():
    smap = SelectorMap.from_dict(RAW)
    page = FakePage(counts={"td.day": 5})
    found = await first_nonempty(page, smap, "booking.day_cell", date="2026-10-03")
    assert found.selector == "td.day"
