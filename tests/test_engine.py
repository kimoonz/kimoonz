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
