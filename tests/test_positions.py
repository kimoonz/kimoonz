"""포지션 추적 · 청산 판정 검증."""

from datetime import datetime, timezone

import pandas as pd
import pytest

from bayesfutures.positions import OpenPosition, check_exit, force_exit

ENTRY_BAR = "2026-01-01T00:00:00+00:00"


def _bars(highs, lows, closes=None):
    n = len(highs)
    idx = pd.date_range("2026-01-01", periods=n, freq="1D", tz="UTC")
    closes = closes or [(h + l) / 2 for h, l in zip(highs, lows)]
    return pd.DataFrame({"open": closes, "high": highs, "low": lows,
                         "close": closes, "volume": [1.0] * n}, index=idx)


def _long(horizon=10):
    return OpenPosition("gold", "daily", "LONG", 100.0, 95.0, 105.0,
                        ENTRY_BAR, horizon, 0.62, 5.0)


def _short(horizon=10):
    return OpenPosition("gold", "daily", "SHORT", 100.0, 105.0, 95.0,
                        ENTRY_BAR, horizon, 0.62, 5.0)


def test_long_target_hit():
    ev = check_exit(_long(), _bars([100, 101, 106], [99, 98, 100]))
    assert ev.reason == "target"
    assert ev.exit_price == 105.0
    assert ev.bars_held == 2
    assert ev.pnl_r == pytest.approx(1.0)
    assert ev.is_win


def test_long_stop_hit():
    ev = check_exit(_long(), _bars([100, 101], [99, 94]))
    assert ev.reason == "stop"
    assert ev.pnl_r == pytest.approx(-1.0)
    assert not ev.is_win


def test_short_target_is_the_lower_barrier():
    ev = check_exit(_short(), _bars([100, 101], [99, 94]))
    assert ev.reason == "target"
    assert ev.exit_price == 95.0
    assert ev.pnl_r == pytest.approx(1.0)


def test_short_stop_is_the_upper_barrier():
    ev = check_exit(_short(), _bars([100, 106], [99, 98]))
    assert ev.reason == "stop"
    assert ev.pnl_r == pytest.approx(-1.0)


def test_both_touched_same_bar_is_conservative():
    """한 봉에서 목표와 손절을 다 건드리면 손절로 본다 (라벨 규칙과 동일)."""
    ev = check_exit(_long(), _bars([100, 110], [99, 90]))
    assert ev.reason == "stop"


def test_timeout_exits_at_close():
    ev = check_exit(_long(horizon=3), _bars([100, 101, 102, 103], [99, 98, 97, 96],
                                            closes=[100, 101, 102, 103]))
    assert ev.reason == "timeout"
    assert ev.exit_price == 103.0
    assert ev.bars_held == 3


def test_still_open_returns_none():
    assert check_exit(_long(horizon=10), _bars([100, 101], [99, 98])) is None


def test_entry_bar_itself_is_not_examined():
    """진입 봉 자체로는 청산되지 않는다 (진입 후의 움직임만 본다)."""
    bars = _bars([110], [90])                 # 진입 봉이 양쪽을 다 건드림
    assert check_exit(_long(), bars) is None


def test_no_bars_after_entry():
    idx = pd.date_range("2025-12-01", periods=3, freq="1D", tz="UTC")
    old = pd.DataFrame({"open": [1.0] * 3, "high": [1.0] * 3, "low": [1.0] * 3,
                        "close": [1.0] * 3, "volume": [1.0] * 3}, index=idx)
    assert check_exit(_long(), old) is None


def test_force_exit_for_reversal():
    ev = force_exit(_long(), 103.0, datetime(2026, 1, 5, tzinfo=timezone.utc), 4)
    assert ev.reason == "reverse"
    assert ev.pnl_r == pytest.approx(0.6)
    assert "반대 신호" in ev.reason_ko


def test_pnl_is_symmetric_for_short():
    short = _short()
    assert short.pnl_r(90.0) == pytest.approx(2.0)     # 하락 = 이익
    assert short.pnl_r(110.0) == pytest.approx(-2.0)


def test_roundtrip_serialization():
    pos = _long()
    assert OpenPosition.from_dict(pos.to_dict()) == pos


def test_from_dict_ignores_unknown_fields():
    data = _long().to_dict() | {"레거시필드": 1}
    assert OpenPosition.from_dict(data).side == "LONG"
