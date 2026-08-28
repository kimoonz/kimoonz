"""데이터 정제 · 피처 계산 검증."""

import numpy as np
import pandas as pd
import pytest

from bayesfutures.data import DataError, _normalize, synthetic
from bayesfutures.features import atr, build_features, rsi, true_range
from bayesfutures.model import interval_seconds, last_closed_index


def _ohlc(n=300, seed=0):
    return synthetic(bars=n, seed=seed)


def test_normalize_lowercases_and_sorts():
    idx = pd.to_datetime(["2024-01-03", "2024-01-01", "2024-01-02"])
    raw = pd.DataFrame({"Open": [1, 2, 3], "High": [2, 3, 4], "Low": [0.5, 1, 2],
                        "Close": [1.5, 2.5, 3.5], "Volume": [10, 20, 30]}, index=idx)
    out = _normalize(raw)
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert out.index.is_monotonic_increasing
    assert str(out.index.tz) == "UTC"


def test_normalize_drops_duplicates_and_bad_bars():
    idx = pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-02"])
    raw = pd.DataFrame({"open": [1, 1, 2], "high": [2, 2, 1], "low": [0.5, 0.5, 3],
                        "close": [1.5, 1.6, 2.5], "volume": [1, 1, 1]}, index=idx)
    out = _normalize(raw)
    assert len(out) == 1                      # 중복 1개 제거 + 고가<저가 봉 제거
    assert out["close"].iloc[0] == 1.6        # 중복은 마지막 값 유지


def test_normalize_requires_ohlc():
    with pytest.raises(DataError):
        _normalize(pd.DataFrame({"close": [1, 2]}, index=pd.to_datetime(["2024-01-01", "2024-01-02"])))


def test_normalize_fills_missing_volume():
    idx = pd.to_datetime(["2024-01-01", "2024-01-02"])
    out = _normalize(pd.DataFrame({"open": [1, 2], "high": [2, 3], "low": [0.5, 1],
                                   "close": [1.5, 2.5]}, index=idx))
    assert out["volume"].isna().all()


def test_true_range_and_atr_are_positive():
    df = _ohlc()
    assert (true_range(df).dropna() >= 0).all()
    a = atr(df, 14).dropna()
    assert len(a) > 0 and (a > 0).all()


def test_rsi_bounds():
    r = rsi(_ohlc()["close"], 14).dropna()
    assert r.min() >= 0 and r.max() <= 100


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 100, dtype=float))
    assert rsi(up, 14).iloc[-1] > 99
    assert rsi(up[::-1].reset_index(drop=True), 14).iloc[-1] < 1


def test_features_have_no_lookahead():
    """앞부분 피처는 뒤쪽 데이터를 바꿔도 변하지 않아야 한다."""
    df = _ohlc(400)
    full = build_features(df)
    truncated = build_features(df.iloc[:300])
    common = full.iloc[:300].dropna(axis=1, how="all").columns
    for col in common:
        a, b = full[col].iloc[:300], truncated[col]
        assert np.allclose(a.to_numpy(), b.to_numpy(), equal_nan=True), col


def test_features_are_finite_or_nan():
    f = build_features(_ohlc())
    assert not np.isinf(f.to_numpy(dtype=float)).any()


def test_extras_are_optional():
    df = _ohlc()
    assert "x_vix" not in build_features(df).columns
    with_extra = build_features(df, extras={"^VIX": _ohlc(seed=3)})
    assert "x_vix" in with_extra.columns


def test_intraday_seasonality_uses_hour():
    df = synthetic(bars=300, interval="1h")
    hourly = build_features(df, intraday=True)["seasonal"]
    assert hourly.max() <= 23
    daily = build_features(df, intraday=False)["seasonal"]
    assert daily.max() <= 6


@pytest.mark.parametrize("interval,seconds", [("1d", 86400), ("1h", 3600), ("15m", 900)])
def test_interval_seconds(interval, seconds):
    assert interval_seconds(interval) == seconds


def test_unknown_interval_rejected():
    with pytest.raises(ValueError):
        interval_seconds("3y")


def test_last_closed_index_excludes_in_progress_bar():
    """진행 중인 봉은 신호 대상에서 빠져야 한다."""
    idx = pd.date_range("2026-08-28 09:00", periods=4, freq="1h", tz="UTC")
    now = pd.Timestamp("2026-08-28 12:30", tz="UTC").to_pydatetime()
    assert last_closed_index(idx, "1h", now) == 2      # 12:00 봉은 아직 진행 중


def test_last_closed_index_all_open():
    idx = pd.date_range("2026-08-28 12:00", periods=2, freq="1h", tz="UTC")
    now = pd.Timestamp("2026-08-28 12:10", tz="UTC").to_pydatetime()
    assert last_closed_index(idx, "1h", now) == -1
