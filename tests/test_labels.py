"""삼중 배리어 라벨링 검증."""

import numpy as np
import pandas as pd
import pytest

from bayesfutures.labels import triple_barrier


def _frame(rows):
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="1D", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1.0)


def test_upper_barrier_hit_first():
    """상단을 먼저 건드리면 y=1."""
    rows = [[100, 101, 99, 100]] * 20 + [[100, 130, 99, 128]] + [[128, 129, 127, 128]] * 5
    df = _frame(rows)
    out = triple_barrier(df, atr_window=5, up_mult=1.0, down_mult=1.0, horizon=5)
    t = 19
    assert out["y"].iloc[t] == 1.0
    assert out["bars_held"].iloc[t] == 1


def test_lower_barrier_hit_first():
    rows = [[100, 101, 99, 100]] * 20 + [[100, 101, 70, 72]] + [[72, 73, 71, 72]] * 5
    df = _frame(rows)
    out = triple_barrier(df, atr_window=5, up_mult=1.0, down_mult=1.0, horizon=5)
    assert out["y"].iloc[19] == 0.0


def test_both_barriers_same_bar_is_conservative():
    """한 봉에서 양쪽을 다 건드리면 순서를 모르므로 손절(0)로 본다."""
    rows = [[100, 101, 99, 100]] * 20 + [[100, 140, 60, 100]] + [[100, 101, 99, 100]] * 5
    df = _frame(rows)
    out = triple_barrier(df, atr_window=5, up_mult=1.0, down_mult=1.0, horizon=5)
    assert out["y"].iloc[19] == 0.0


def test_timeout_uses_terminal_direction():
    """배리어를 못 건드리면 만기 종가 방향으로 판정하고 timeout 표시."""
    rows = [[100, 100.5, 99.5, 100]] * 20 + [[100, 100.4, 99.6, 100.2]] * 4
    df = _frame(rows)
    out = triple_barrier(df, atr_window=5, up_mult=5.0, down_mult=5.0, horizon=3)
    t = 19
    assert bool(out["timeout"].iloc[t]) is True
    assert out["y"].iloc[t] == 1.0


def test_no_lookahead_past_end():
    """끝부분은 미래 봉이 없으므로 라벨이 불완전 — 마지막 봉은 NaN."""
    df = _frame([[100, 101, 99, 100]] * 30)
    out = triple_barrier(df, atr_window=5, horizon=10)
    assert np.isnan(out["y"].iloc[-1])


def test_barriers_scale_with_atr():
    df = _frame([[100, 105, 95, 100]] * 40)
    out = triple_barrier(df, atr_window=14, up_mult=2.0, down_mult=1.0, horizon=5)
    row = out.iloc[30]
    assert row["upper"] - 100 == pytest.approx(2.0 * row["atr"])
    assert 100 - row["lower"] == pytest.approx(1.0 * row["atr"])
