"""설정 로딩 · 텔레그램 발송 검증."""

import os

import pytest

from bayesfutures.config import Config, load_config
from bayesfutures.telegram import Telegram, _split


def test_defaults_cover_four_instruments():
    cfg = Config()
    assert cfg.instruments == ["gold", "silver", "crude", "nasdaq"]
    assert set(cfg.timeframes) == {"daily", "hourly"}


def test_repo_config_parses():
    cfg = load_config("config.yaml")
    assert "silver" in cfg.instruments
    assert cfg.timeframes["hourly"].interval == "1h"


def test_missing_file_falls_back_to_defaults(tmp_path):
    cfg = load_config(tmp_path / "없는파일.yaml")
    assert cfg.instruments == Config().instruments


def test_yaml_overrides_nested_values(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("""
instruments: [gold]
account: {equity_usd: 50000, risk_per_trade_pct: 2.0}
timeframes:
  daily:
    label: {horizon: 25}
    signal: {long_threshold: 0.65}
""", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.instruments == ["gold"]
    assert cfg.account.equity_usd == 50000
    assert cfg.timeframes["daily"].label.horizon == 25
    assert cfg.timeframes["daily"].signal.long_threshold == 0.65
    assert cfg.timeframes["daily"].label.atr_window == 14      # 미지정 값은 기본 유지


def test_unknown_key_is_rejected(tmp_path):
    """오타를 조용히 무시하면 안 된다."""
    p = tmp_path / "c.yaml"
    p.write_text("account: {equity_used: 50000}\n", encoding="utf-8")
    with pytest.raises(KeyError):
        load_config(p)


def test_unknown_nested_key_is_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("timeframes:\n  daily:\n    signal: {long_treshold: 0.6}\n", encoding="utf-8")
    with pytest.raises(KeyError):
        load_config(p)


def test_secrets_come_from_environment(monkeypatch):
    cfg = Config()
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert cfg.telegram_token is None
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    assert cfg.telegram_token == "abc"


def test_split_respects_limit():
    assert [len(c) for c in _split("가" * 10000)] == [4096, 4096, 1808]
    assert _split("짧다") == ["짧다"]


def test_split_keeps_line_structure():
    text = "\n".join(f"줄 {i}" for i in range(2000))
    chunks = _split(text)
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_dry_run_does_not_call_network(capsys):
    tg = Telegram("토큰", "123", dry_run=True)
    assert tg.send("테스트 메시지")
    assert "테스트 메시지" in capsys.readouterr().out


def test_unconfigured_falls_back_to_console(capsys):
    tg = Telegram(None, None)
    assert not tg.configured
    assert tg.send("설정 없음")
    assert "설정 없음" in capsys.readouterr().out


def test_undersized_account_warning_states_requirement(tmp_path):
    """0계약일 때 '얼마가 필요한지'를 알려줘야 한다."""
    import re

    import pandas as pd

    from bayesfutures.instruments import GOLD
    from bayesfutures.message import format_signal
    from bayesfutures.model import Prediction
    from bayesfutures.signals import build_signal

    cfg = Config()
    cfg.account.equity_usd = 30_000
    pred = Prediction(GOLD, "daily", "1d", pd.Timestamp("2026-08-27", tz="UTC"),
                      4600.0, 4600.0, 68.85, 0.90, 0.0, [], None, [], 1500, 0.5, 0.5)
    text = re.sub(r"<[^>]+>", "", format_signal(cfg, build_signal(cfg, pred)))
    assert "1계약도 리스크 한도를 넘습니다" in text
    assert "$68,850" in text        # 68.85 ATR × $10/pt ÷ 1%


def test_confidence_blocked_size_says_so_instead(tmp_path):
    """리스크 한도로는 잡히는데 신뢰도 스케일링 때문에 0이면, 그렇게 말해야 한다."""
    import re

    import pandas as pd

    from bayesfutures.instruments import GOLD
    from bayesfutures.message import format_signal
    from bayesfutures.model import Prediction
    from bayesfutures.signals import build_signal

    cfg = Config()
    cfg.account.equity_usd = 80_000          # 한도 $800 > 1계약 리스크 $688
    pred = Prediction(GOLD, "daily", "1d", pd.Timestamp("2026-08-27", tz="UTC"),
                      4600.0, 4600.0, 68.85, 0.634, 0.0, [], None, [], 1500, 0.5, 0.55)
    sig = build_signal(cfg, pred)
    assert sig.sizing[0].blocked_by_confidence
    assert sig.sizing[0].full_budget_contracts == 1
    text = re.sub(r"<[^>]+>", "", format_signal(cfg, sig))
    assert "신뢰도 스케일링으로 0계약" in text
    assert "한도를 다 쓰면 MGC 1계약" in text
    assert "1계약도 리스크 한도를 넘습니다" not in text
