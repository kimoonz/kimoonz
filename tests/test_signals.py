"""신호 결정 · 포지션 사이징 검증."""

import numpy as np
import pandas as pd
import pytest

from bayesfutures.config import Config
from bayesfutures.instruments import GOLD, NASDAQ
from bayesfutures.model import Prediction
from bayesfutures.signals import Side, build_signal, combine


def _pred(prob, inst=GOLD, atr=20.0, price=2000.0, tf="daily"):
    return Prediction(
        instrument=inst, timeframe=tf, interval="1d",
        asof=pd.Timestamp("2026-01-02", tz="UTC"), price=price, last_price=price,
        atr=atr, prob_up=prob, raw_logodds=0.0, contributions=[],
        regime_probs=None, regime_names=[], n_train=1000, calib_a=0.5, base_rate=0.5,
    )


def test_long_short_flat_thresholds():
    cfg = Config()
    assert build_signal(cfg, _pred(0.70)).side is Side.LONG
    assert build_signal(cfg, _pred(0.30)).side is Side.SHORT
    assert build_signal(cfg, _pred(0.52)).side is Side.FLAT


def test_stop_and_target_placement():
    cfg = Config()
    long = build_signal(cfg, _pred(0.70, atr=20.0, price=2000.0))
    assert long.stop == pytest.approx(1980.0, abs=0.1)
    assert long.target == pytest.approx(2020.0, abs=0.1)
    short = build_signal(cfg, _pred(0.30, atr=20.0, price=2000.0))
    assert short.stop == pytest.approx(2020.0, abs=0.1)
    assert short.target == pytest.approx(1980.0, abs=0.1)


def test_expected_value_accounts_for_cost():
    """비용을 뺀 기대값이어야 한다 — 확률만으로 진입하지 않는다."""
    cfg = Config()
    sig = build_signal(cfg, _pred(0.70, atr=20.0))
    assert sig.expected_r == pytest.approx(0.70 - 0.30 - sig.cost_r, abs=1e-9)
    assert sig.cost_r > 0


def test_marginal_edge_is_rejected():
    """임계치는 넘지만 비용 차감 후 기대값이 안 나오면 관망."""
    cfg = Config()
    cfg.timeframes["daily"].signal.long_threshold = 0.51
    cfg.timeframes["daily"].signal.min_edge_r = 0.05
    sig = build_signal(cfg, _pred(0.515, atr=20.0))
    assert sig.side is Side.FLAT


def test_position_size_respects_risk_budget():
    """계약 수 × 1계약 리스크 <= 계좌 리스크 한도."""
    cfg = Config()
    cfg.account.equity_usd = 100_000
    cfg.account.risk_per_trade_pct = 1.0
    sig = build_signal(cfg, _pred(0.75, inst=NASDAQ, atr=200.0, price=20_000.0))
    budget = 1000.0
    for s in sig.sizing:
        assert s.contracts * sig.stop_distance * s.spec.point_value <= budget + 1e-6


def test_micro_allows_more_contracts_than_full():
    cfg = Config()
    cfg.account.equity_usd = 200_000
    sig = build_signal(cfg, _pred(0.75, inst=GOLD, atr=20.0))
    micro, full = sig.sizing
    assert micro.spec.code == "MGC" and full.spec.code == "GC"
    assert micro.contracts >= full.contracts


def test_small_account_gets_zero_contracts():
    """계좌가 작으면 0계약 — 억지로 1계약을 만들어내지 않는다."""
    cfg = Config()
    cfg.account.equity_usd = 500
    sig = build_signal(cfg, _pred(0.80, inst=NASDAQ, atr=300.0, price=20_000.0))
    assert all(s.contracts == 0 for s in sig.sizing)


def test_higher_probability_gets_bigger_size():
    """확률이 높을수록 계약 수가 커져야 한다 (신뢰도 스케일링)."""
    cfg = Config()
    cfg.account.equity_usd = 500_000
    cfg.account.max_contracts = 1000
    marginal = build_signal(cfg, _pred(0.585, atr=20.0)).sizing[0].contracts
    confident = build_signal(cfg, _pred(0.75, atr=20.0)).sizing[0].contracts
    assert 0 < marginal < confident


def test_kelly_caps_size_at_thin_edge():
    """엣지가 아주 얇으면 켈리 상한이 리스크 한도보다 먼저 걸린다."""
    cfg = Config()
    cfg.account.equity_usd = 500_000
    cfg.account.max_contracts = 10_000
    sc = cfg.timeframes["daily"].signal
    sc.long_threshold = 0.505
    sc.min_edge_r = -1.0
    sc.min_prob_over_base = 0.0
    sc.confidence_scaling = False
    sig = build_signal(cfg, _pred(0.506, atr=20.0))
    assert sig.side is Side.LONG
    by_risk = cfg.account.equity_usd * 0.01 / (sig.stop_distance * GOLD.micro.point_value)
    assert sig.sizing[0].contracts < by_risk


def test_size_factor_never_exceeds_risk_budget():
    cfg = Config()
    cfg.account.equity_usd = 100_000
    sig = build_signal(cfg, _pred(0.95, atr=20.0))
    budget = 1000.0
    for s in sig.sizing:
        assert s.risk_usd <= budget + 1e-6


def test_max_contracts_cap():
    cfg = Config()
    cfg.account.equity_usd = 10_000_000
    cfg.account.max_contracts = 3
    sig = build_signal(cfg, _pred(0.90, atr=20.0))
    assert all(s.contracts <= 3 for s in sig.sizing)


def test_short_probability_is_reported_for_short_side():
    """매도 신호의 '확률'은 하락 확률이어야 한다."""
    sig = build_signal(Config(), _pred(0.25))
    assert sig.side is Side.SHORT
    assert sig.prob == pytest.approx(0.75)


def test_combine_detects_agreement_and_conflict():
    cfg = Config()
    long_d = build_signal(cfg, _pred(0.70, tf="daily"))
    long_h = build_signal(cfg, _pred(0.70, tf="hourly"))
    short_h = build_signal(cfg, _pred(0.25, tf="hourly"))
    assert "모두" in combine({"daily": long_d, "hourly": long_h})
    assert "충돌" in combine({"daily": long_d, "hourly": short_h})
    assert combine({"daily": long_d}) is None


def test_short_risk_reward_is_inverted_for_asymmetric_barriers():
    """목표 2ATR / 손절 1ATR 이면 매도의 손익비는 뒤집혀 1:0.5 가 되어야 한다."""
    cfg = Config()
    lab = cfg.timeframes["daily"].label
    lab.up_mult, lab.down_mult = 2.0, 1.0
    sc = cfg.timeframes["daily"].signal
    sc.min_edge_r = -10.0

    long = build_signal(cfg, _pred(0.70, atr=20.0, price=2000.0))
    assert long.side is Side.LONG
    assert long.stop_distance == pytest.approx(20.0)     # 하단 1 ATR
    assert long.risk_reward == pytest.approx(2.0)

    short = build_signal(cfg, _pred(0.20, atr=20.0, price=2000.0))
    assert short.side is Side.SHORT
    assert short.stop_distance == pytest.approx(40.0)    # 상단 2 ATR 이 손절
    assert short.risk_reward == pytest.approx(0.5)


def test_short_expected_value_uses_inverted_payoff():
    """매도 기대값이 매수와 같은 보상을 쓰면 안 된다."""
    cfg = Config()
    lab = cfg.timeframes["daily"].label
    lab.up_mult, lab.down_mult = 2.0, 1.0
    cfg.timeframes["daily"].signal.min_edge_r = -10.0
    short = build_signal(cfg, _pred(0.20, atr=20.0))
    assert short.expected_r == pytest.approx(0.80 * 0.5 - 0.20 - short.cost_r, abs=1e-9)


def test_signal_barriers_match_label_barriers():
    """확률이 의미를 가지려면 주문의 손절·목표가 라벨 배리어와 같아야 한다."""
    cfg = Config()
    cfg.timeframes["daily"].label.up_mult = 1.5
    cfg.timeframes["daily"].label.down_mult = 0.75
    sig = build_signal(cfg, _pred(0.70, atr=20.0, price=2000.0))
    assert sig.target == pytest.approx(2000.0 + 1.5 * 20.0, abs=0.1)
    assert sig.stop == pytest.approx(2000.0 - 0.75 * 20.0, abs=0.1)


def _pred_with_base(prob, base, **kw):
    p = _pred(prob, **kw)
    p.base_rate = base
    return p


def test_drift_alone_is_not_a_signal():
    """기준확률이 이미 높아서 임계치를 넘은 것뿐이면 신호가 아니다."""
    cfg = Config()
    sig = build_signal(cfg, _pred_with_base(0.59, base=0.585))
    assert sig.side is Side.FLAT


def test_evidence_above_base_rate_is_a_signal():
    cfg = Config()
    sig = build_signal(cfg, _pred_with_base(0.62, base=0.55))
    assert sig.side is Side.LONG
    assert sig.lift == pytest.approx(0.07)


def test_low_base_rate_does_not_force_shorts():
    """기준확률이 낮은 종목(비대칭 배리어)에서 모든 봉이 매도가 되면 안 된다."""
    cfg = Config()
    sig = build_signal(cfg, _pred_with_base(0.40, base=0.40))
    assert sig.side is Side.FLAT


def test_short_needs_evidence_below_base_rate():
    cfg = Config()
    sig = build_signal(cfg, _pred_with_base(0.35, base=0.45))
    assert sig.side is Side.SHORT
    assert sig.lift == pytest.approx(-0.10)
