"""추세·변동성·분산 배분 전략 검증."""

import numpy as np
import pandas as pd
import pytest

from bayesfutures.instruments import CRUDE, GOLD, INSTRUMENTS, NASDAQ
from bayesfutures.strategy import (AllocationChange, StrategyParams, affordable_instruments,
                                   allocation_changes, current_targets, evaluate,
                                   performance, realized_vol, suggest_target_vol,
                                   target_weights, trend_signals)


def _series(values, start="2015-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="1D", tz="UTC")
    return pd.Series(values, index=idx, dtype=float)


def _rising(n=800, slope=0.001, seed=0, noise=0.002):
    rng = np.random.default_rng(seed)
    return _series(100 * np.exp(np.cumsum(np.full(n, slope) + rng.normal(0, noise, n))))


def _falling(n=800, seed=1):
    return _rising(n, slope=-0.001, seed=seed)


def _frame(series: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"open": series, "high": series * 1.005,
                         "low": series * 0.995, "close": series,
                         "volume": 1.0}, index=series.index)


# --------------------------------------------------------------- 추세 신호
def test_trend_on_in_uptrend():
    sig = trend_signals(_rising(), 126, 252)
    assert sig["on"].iloc[-1] == 1.0
    assert sig["fast_ok"].iloc[-1] and sig["slow_ok"].iloc[-1]


def test_trend_off_in_downtrend():
    sig = trend_signals(_falling(), 126, 252)
    assert sig["on"].iloc[-1] == 0.0


def test_trend_needs_both_windows():
    """최근에 반등했지만 장기로는 아직 아래면 보유하지 않는다."""
    # 고점 유지 → 급락 → 소폭 반등: 126일로는 올랐지만 252일로는 아직 아래
    close = _series(list(np.full(300, 200.0))
                    + list(np.linspace(200, 100, 200))
                    + list(np.linspace(100, 130, 100)))
    sig = trend_signals(close, 126, 252)
    assert sig["fast_ok"].iloc[-1]          # 126일 전보다는 높다
    assert not sig["slow_ok"].iloc[-1]      # 252일 전보다는 낮다
    assert sig["on"].iloc[-1] == 0.0


def test_trend_warmup_is_off():
    sig = trend_signals(_rising(300), 126, 252)
    assert sig["on"].iloc[100] == 0.0       # 252봉이 안 쌓인 구간


# --------------------------------------------------------------- 변동성 조절
def test_realized_vol_is_annualized():
    rng = np.random.default_rng(0)
    daily_sd = 0.01
    close = _series(100 * np.exp(np.cumsum(rng.normal(0, daily_sd, 2000))))
    rv = realized_vol(close, 60).iloc[-1]
    assert rv == pytest.approx(daily_sd * np.sqrt(252), rel=0.25)


def test_higher_volatility_gets_smaller_weight():
    calm = _rising(800, noise=0.002, seed=2)
    wild = _rising(800, noise=0.02, seed=3)
    px = pd.DataFrame({"calm": calm, "wild": wild})
    w = target_weights(px, StrategyParams())
    assert w["calm"].iloc[-1] > w["wild"].iloc[-1] * 3


def test_weight_is_capped():
    """변동성이 0에 가까워도 비중이 폭주하면 안 된다."""
    flat = _series(100 * np.exp(np.cumsum(np.full(800, 0.0005))))
    w = target_weights(pd.DataFrame({"a": flat}), StrategyParams(max_scale=3.0))
    assert w["a"].max() <= 3.0 + 1e-9


def test_downtrend_gets_zero_weight():
    px = pd.DataFrame({"up": _rising(), "down": _falling()})
    w = target_weights(px, StrategyParams())
    assert w["down"].iloc[-1] == 0.0
    assert w["up"].iloc[-1] > 0.0


# --------------------------------------------------------------- 성과 계산
def test_trend_filter_cuts_exposure_in_a_crash():
    """폭락이 진행되면 노출이 0으로 끊겨야 한다."""
    rise = list(np.linspace(100, 200, 600))
    crash = list(np.linspace(200, 80, 300))
    px = pd.DataFrame({"a": _series(rise + crash)})
    w = target_weights(px, StrategyParams())

    assert w["a"].iloc[599] > 0          # 상승 끝에서는 보유
    assert w["a"].iloc[-1] == 0.0        # 폭락 뒤에는 완전 청산
    assert (w["a"].iloc[-100:] == 0).all()


def test_trend_filter_reduces_drawdown():
    """같은 자산을 계속 들고 있는 것보다 낙폭이 작아야 한다."""
    rng = np.random.default_rng(4)
    rise = list(np.linspace(100, 200, 700))
    crash = list(np.linspace(200, 90, 200))
    recover = list(np.linspace(90, 130, 300))
    close = _series(np.array(rise + crash + recover) * np.exp(rng.normal(0, 0.004, 1200)))
    px = pd.DataFrame({"a": close})

    filtered = evaluate(px, StrategyParams(target_vol=0.15), 1e9, {"a": 1.0},
                        integer_contracts=False)
    held = px["a"].pct_change()
    assert performance(filtered)["최대낙폭"] > performance(held)["최대낙폭"]


def test_performance_metrics_shape():
    r = pd.Series(np.random.default_rng(0).normal(0.0004, 0.01, 1000),
                  index=pd.date_range("2020-01-01", periods=1000, freq="1D", tz="UTC"))
    m = performance(r)
    assert set(m) >= {"연수익", "변동성", "샤프", "최대낙폭", "칼마"}
    assert m["최대낙폭"] <= 0


def test_performance_needs_enough_data():
    assert performance(pd.Series([0.01, 0.02])) == {}


# --------------------------------------------------------------- 계좌 규모
def test_affordable_excludes_oversized_contracts():
    px = pd.DataFrame({"gold": _series([4600.0] * 300),
                       "crude": _series([80.0] * 300)})
    pv = {"gold": 10.0, "crude": 100.0}       # 금 $46,000 / 오일 $8,000
    assert affordable_instruments(px, pv, 20_000) == ["crude"]
    assert set(affordable_instruments(px, pv, 200_000)) == {"gold", "crude"}


def test_suggest_target_vol_rises_for_small_accounts():
    px = pd.DataFrame({"a": _rising(1200), "b": _rising(1200, seed=5)})
    pv = {"a": 100.0, "b": 100.0}
    small = suggest_target_vol(px, pv, 20_000, StrategyParams())
    large = suggest_target_vol(px, pv, 2_000_000, StrategyParams())
    assert small >= large


def test_suggest_target_vol_respects_cap():
    px = pd.DataFrame({"a": _rising(1200)})
    assert suggest_target_vol(px, {"a": 1000.0}, 1_000, StrategyParams(), cap=0.4) == 0.4


def test_integer_rounding_hurts_small_accounts():
    """정수 계약 반올림 오차는 계좌가 작을수록 커진다."""
    px = pd.DataFrame({k: _rising(900, seed=i) for i, k in enumerate(["a", "b"])})
    pv = {"a": 100.0, "b": 100.0}
    ideal = target_weights(px, StrategyParams())
    notional = px * pd.Series(pv)

    def tracking_error(equity: float) -> float:
        contracts = np.floor((ideal * equity) / notional).clip(lower=0)
        return float((contracts * notional / equity - ideal).abs().mean().mean())

    small, medium, large = (tracking_error(e) for e in (20_000, 200_000, 5_000_000))
    assert small > medium > large
    assert large < 0.01


# --------------------------------------------------------------- 목표/변경 신호
def _targets(equity=500_000, target_vol=0.15):
    data = {"gold": _frame(_rising(900, seed=1)),
            "crude": _frame(_falling(900, seed=2))}
    instruments = {"gold": GOLD, "crude": CRUDE}
    return current_targets(data, instruments, StrategyParams(target_vol=target_vol), equity)


def test_current_targets_reflects_trend():
    by_key = {t.instrument.key: t for t in _targets()}
    assert by_key["gold"].trend_on and by_key["gold"].target_contracts > 0
    assert not by_key["crude"].trend_on and by_key["crude"].target_contracts == 0


def test_exit_level_is_the_higher_reference():
    t = next(t for t in _targets() if t.instrument.key == "gold")
    assert t.exit_level == max(t.ma_fast, t.ma_slow)
    assert t.distance_to_exit > 0


def test_enter_signal_when_flat():
    changes = {c.target.instrument.key: c for c in allocation_changes(_targets(), {})}
    assert changes["gold"].action == "enter"
    assert changes["gold"].delta > 0
    assert "추세 진입" in changes["gold"].reason


def test_exit_signal_when_trend_dies():
    held = {"crude": 3}
    change = next(c for c in allocation_changes(_targets(), held)
                  if c.target.instrument.key == "crude")
    assert change.action == "exit"
    assert change.delta == -3
    assert "추세 이탈" in change.reason


def test_no_signal_when_target_matches_holding():
    targets = _targets()
    held = {t.instrument.key: t.target_contracts for t in targets}
    assert allocation_changes(targets, held) == []


def test_min_delta_filters_small_rebalances():
    targets = _targets()
    gold = next(t for t in targets if t.instrument.key == "gold")
    held = {"gold": gold.target_contracts - 1}
    assert any(c.action == "increase" for c in allocation_changes(targets, held, 1))
    assert not any(c.action == "increase" for c in allocation_changes(targets, held, 3))


def test_entry_and_exit_are_never_filtered():
    """진입·청산은 잔떨림 필터와 무관하게 항상 알린다."""
    targets = _targets()
    changes = allocation_changes(targets, {"crude": 1}, min_delta=99)
    assert any(c.action == "exit" for c in changes)
    assert any(c.action == "enter" for c in changes)


def test_granularity_block_is_detected():
    """추세는 켜졌는데 1계약이 목표 비중을 넘으면 표시되어야 한다."""
    data = {"nasdaq": _frame(_rising(900) * 300)}     # 가격을 크게
    targets = current_targets(data, {"nasdaq": NASDAQ},
                              StrategyParams(target_vol=0.15), 20_000)
    t = targets[0]
    assert t.trend_on and t.target_contracts == 0
    assert t.blocked_by_granularity
    assert t.equity_needed > 20_000


def test_portfolio_view_separates_three_states():
    """보유 / 진입 대상 / 대기가 구분되어야 한다."""
    import re

    from bayesfutures.message import format_portfolio

    targets = _targets(equity=500_000)
    text = re.sub(r"<[^>]+>", "", format_portfolio(targets, {}, 500_000, 0.15))
    assert "진입 대상" in text          # 금: 추세 ON, 미보유
    assert "대기" in text               # 오일: 추세 OFF
    assert "보유 중" not in text

    held = {"gold": next(t for t in targets if t.instrument.key == "gold").target_contracts}
    text2 = re.sub(r"<[^>]+>", "", format_portfolio(targets, held, 500_000, 0.15))
    assert "보유 중" in text2
    assert "진입 대상" not in text2
