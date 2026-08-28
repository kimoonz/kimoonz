"""엔진 · 장시간 · 브리핑 스케줄 검증."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from bayesfutures.config import Config
from bayesfutures.engine import Engine, is_market_open
from bayesfutures.state import AlertState
from bayesfutures.telegram import Telegram

NY = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")


@pytest.mark.parametrize("when,expected", [
    (datetime(2026, 8, 26, 10, 0, tzinfo=NY), True),    # 수 오전
    (datetime(2026, 8, 26, 17, 30, tzinfo=NY), False),  # 일일 정비
    (datetime(2026, 8, 26, 18, 30, tzinfo=NY), True),   # 정비 후 재개
    (datetime(2026, 8, 28, 18, 0, tzinfo=NY), False),   # 금 마감 후
    (datetime(2026, 8, 29, 12, 0, tzinfo=NY), False),   # 토
    (datetime(2026, 8, 30, 12, 0, tzinfo=NY), False),   # 일 개장 전
    (datetime(2026, 8, 30, 19, 0, tzinfo=NY), True),    # 일 개장 후
])
def test_market_hours(when, expected):
    assert is_market_open(when) is expected


def _engine(tmp_path) -> Engine:
    cfg = Config()
    cfg.state_dir = str(tmp_path)
    cfg.data.cache_dir = str(tmp_path / "cache")
    return Engine(cfg=cfg, telegram=Telegram(None, None, dry_run=True),
                  state=AlertState.load(tmp_path))


def test_briefing_due_only_after_target_time(tmp_path):
    eng = _engine(tmp_path)
    eng.cfg.alerts.briefing_time_kst = "08:00"
    assert not eng._briefing_due(datetime(2026, 8, 28, 7, 59, tzinfo=KST))
    assert eng._briefing_due(datetime(2026, 8, 28, 8, 1, tzinfo=KST))


def test_briefing_not_resent_same_day(tmp_path):
    eng = _engine(tmp_path)
    now = datetime(2026, 8, 28, 8, 30, tzinfo=KST)
    assert eng._briefing_due(now)
    eng.state.record_briefing(now.date())
    assert not eng._briefing_due(now)


def test_late_restart_skips_stale_briefing(tmp_path):
    """한참 지난 뒤 PC가 켜졌으면 지난 브리핑을 뒤늦게 쏘지 않는다."""
    eng = _engine(tmp_path)
    eng.cfg.alerts.briefing_time_kst = "08:00"
    assert not eng._briefing_due(datetime(2026, 8, 28, 15, 0, tzinfo=KST))


def test_invalid_briefing_time_is_safe(tmp_path):
    eng = _engine(tmp_path)
    eng.cfg.alerts.briefing_time_kst = "오전 8시"
    assert not eng._briefing_due(datetime(2026, 8, 28, 9, 0, tzinfo=KST))


def test_active_timeframes_respects_enabled(tmp_path):
    eng = _engine(tmp_path)
    eng.cfg.timeframes["hourly"].enabled = False
    assert eng.active_timeframes() == ["daily"]


# ---------------------------------------------------------------- 포지션 생애주기
def _signal(cfg, prob, tf="daily", price=100.0, atr=5.0, asof="2025-06-02"):
    import pandas as pd

    from bayesfutures.instruments import GOLD
    from bayesfutures.model import Prediction
    from bayesfutures.signals import build_signal

    pred = Prediction(GOLD, tf, cfg.timeframes[tf].interval,
                      pd.Timestamp(asof, tz="UTC"), price, price, atr,
                      prob, 0.0, [], None, [], 1500, 0.5, 0.5)
    return build_signal(cfg, pred)


class _StubModel:
    """엔진이 기대하는 최소한의 모델 — 시세만 들고 있다."""

    def __init__(self, df):
        self.df = df

    def load(self, force=False):
        pass


def _with_bars(engine, highs, lows, start="2025-06-02"):
    import pandas as pd

    n = len(highs)
    idx = pd.date_range(start, periods=n, freq="1D", tz="UTC")
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    df = pd.DataFrame({"open": closes, "high": highs, "low": lows,
                       "close": closes, "volume": [1.0] * n}, index=idx)
    engine.models[("gold", "daily")] = _StubModel(df)
    return df


def test_signal_opens_a_tracked_position(tmp_path):
    eng = _engine(tmp_path)
    sig = _signal(eng.cfg, 0.70)
    assert eng.send_signals({"gold": {"daily": sig}}) == 1
    pos = eng.state.get_position("gold", "daily")
    assert pos is not None and pos.side == "LONG"
    assert pos.entry == sig.entry and pos.target == sig.target


def test_no_reentry_while_holding_same_direction(tmp_path):
    eng = _engine(tmp_path)
    eng.send_signals({"gold": {"daily": _signal(eng.cfg, 0.70)}})
    again = _signal(eng.cfg, 0.72, asof="2025-06-20")     # 쿨다운은 지난 시점
    assert eng.send_signals({"gold": {"daily": again}}) == 0


def test_opposite_signal_closes_then_reverses(tmp_path):
    eng = _engine(tmp_path)
    eng.send_signals({"gold": {"daily": _signal(eng.cfg, 0.70)}})
    flip = _signal(eng.cfg, 0.25, asof="2025-06-20")
    assert eng.send_signals({"gold": {"daily": flip}}) == 1
    pos = eng.state.get_position("gold", "daily")
    assert pos.side == "SHORT"


def test_check_exits_closes_on_target(tmp_path):
    eng = _engine(tmp_path)
    sig = _signal(eng.cfg, 0.70, price=100.0, atr=5.0)   # 목표 105, 손절 95
    eng.send_signals({"gold": {"daily": sig}})
    _with_bars(eng, highs=[100, 101, 106], lows=[99, 98, 100])
    assert eng.check_exits() == 1
    assert eng.state.get_position("gold", "daily") is None


def test_check_exits_leaves_open_position_alone(tmp_path):
    eng = _engine(tmp_path)
    eng.send_signals({"gold": {"daily": _signal(eng.cfg, 0.70)}})
    _with_bars(eng, highs=[100, 101, 102], lows=[99, 98, 97])
    assert eng.check_exits() == 0
    assert eng.state.get_position("gold", "daily") is not None


def test_positions_survive_restart(tmp_path):
    """PC 재시작 후에도 보유 포지션 감시가 이어져야 한다."""
    eng = _engine(tmp_path)
    eng.send_signals({"gold": {"daily": _signal(eng.cfg, 0.70)}})

    revived = _engine(tmp_path)                          # 새 프로세스 흉내
    assert revived.state.get_position("gold", "daily") is not None
    _with_bars(revived, highs=[100, 101, 106], lows=[99, 98, 100])
    assert revived.check_exits() == 1


def test_exit_alerts_can_be_disabled_but_position_still_closes(tmp_path):
    eng = _engine(tmp_path)
    eng.cfg.alerts.exit_alerts = False
    eng.send_signals({"gold": {"daily": _signal(eng.cfg, 0.70)}})
    _with_bars(eng, highs=[100, 101, 106], lows=[99, 98, 100])
    assert eng.check_exits() == 1
    assert eng.state.get_position("gold", "daily") is None


def test_stale_position_for_unknown_instrument_is_dropped(tmp_path):
    from bayesfutures.positions import OpenPosition

    eng = _engine(tmp_path)
    eng.state.open_position(OpenPosition("없는종목", "daily", "LONG", 1, 0.9, 1.1,
                                         "2025-06-02T00:00:00+00:00", 10, 0.6, 0.1))
    eng.check_exits()
    assert eng.state.get_position("없는종목", "daily") is None
