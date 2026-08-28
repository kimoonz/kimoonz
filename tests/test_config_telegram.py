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
    cfg.alerts.show_position_size = True
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
    cfg.alerts.show_position_size = True
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


def test_sizing_block_is_off_by_default():
    """계약 수 계산은 기본적으로 안 보여준다 — 타이밍이 핵심."""
    import re

    import pandas as pd

    from bayesfutures.instruments import GOLD
    from bayesfutures.message import format_signal
    from bayesfutures.model import Prediction
    from bayesfutures.signals import build_signal

    cfg = Config()
    pred = Prediction(GOLD, "daily", "1d", pd.Timestamp("2026-08-27", tz="UTC"),
                      4600.0, 4600.0, 68.85, 0.70, 0.0, [], None, [], 1500, 0.5, 0.5)
    text = re.sub(r"<[^>]+>", "", format_signal(cfg, build_signal(cfg, pred)))
    assert "계약 수" not in text
    assert "청산" in text and "익절" in text and "손절" in text


# ---------------------------------------------------------------- chat_id 해석
def test_chat_id_prefers_futures_specific_variable(monkeypatch, tmp_path):
    """봇 하나로 여러 자동화를 돌릴 때 선물 전용 방이 우선이어야 한다."""
    cfg = Config()
    cfg.state_dir = str(tmp_path)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "공용방")
    assert cfg.telegram_chat_id == "공용방"
    monkeypatch.setenv("TELEGRAM_CHAT_ID_FUTURES", "선물방")
    assert cfg.telegram_chat_id == "선물방"


def test_chat_id_falls_back_to_saved_file(monkeypatch, tmp_path):
    cfg = Config()
    cfg.state_dir = str(tmp_path)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID_FUTURES", raising=False)
    assert cfg.telegram_chat_id is None
    cfg.save_chat_id("-5305089060")
    assert cfg.telegram_chat_id == "-5305089060"


def test_saved_chat_id_file_never_holds_a_token(tmp_path):
    """토큰이 파일로 새면 안 된다 — 저장되는 건 chat_id 뿐."""
    import json

    cfg = Config()
    cfg.state_dir = str(tmp_path)
    cfg.save_chat_id("-123")
    stored = json.loads(cfg.chat_id_file.read_text(encoding="utf-8"))
    assert stored == {"chat_id": "-123"}


def test_corrupt_chat_id_file_is_ignored(monkeypatch, tmp_path):
    cfg = Config()
    cfg.state_dir = str(tmp_path)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID_FUTURES", raising=False)
    cfg.chat_id_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.chat_id_file.write_text("{깨진", encoding="utf-8")
    assert cfg.telegram_chat_id is None


# ---------------------------------------------------------------- 발송 견고성
class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_supergroup_migration_is_followed(monkeypatch):
    """그룹이 슈퍼그룹이 되면 chat_id 가 바뀐다. 새 id 는 에러에 딱 한 번 실려 온다."""
    from bayesfutures import telegram as tg_mod

    calls = []
    saved = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json["chat_id"])
        if json["chat_id"] == "-100":
            return _FakeResponse(400, {"ok": False, "description": "group upgraded",
                                       "parameters": {"migrate_to_chat_id": -1009999}})
        return _FakeResponse(200, {"ok": True})

    monkeypatch.setattr(tg_mod._SESSION, "post", fake_post)
    tg = tg_mod.Telegram("토큰", "-100", on_chat_migrated=saved.append)
    assert tg.send("테스트")
    assert calls == ["-100", "-1009999"]
    assert saved == ["-1009999"]
    assert tg.chat_id == "-1009999"


def test_rate_limit_waits_then_succeeds(monkeypatch):
    from bayesfutures import telegram as tg_mod

    attempts = []

    def fake_post(url, json=None, timeout=None):
        attempts.append(1)
        if len(attempts) == 1:
            return _FakeResponse(429, {"ok": False, "parameters": {"retry_after": 0}})
        return _FakeResponse(200, {"ok": True})

    monkeypatch.setattr(tg_mod._SESSION, "post", fake_post)
    monkeypatch.setattr(tg_mod.time, "sleep", lambda _s: None)
    assert tg_mod.Telegram("토큰", "-100").send("테스트")
    assert len(attempts) == 2


def test_bad_token_does_not_retry(monkeypatch):
    """401 은 재시도해도 소용없다 — 바로 포기해야 한다."""
    from bayesfutures import telegram as tg_mod

    attempts = []

    def fake_post(url, json=None, timeout=None):
        attempts.append(1)
        return _FakeResponse(401, {"ok": False, "description": "Unauthorized"})

    monkeypatch.setattr(tg_mod._SESSION, "post", fake_post)
    assert not tg_mod.Telegram("나쁜토큰", "-100").send("테스트")
    assert len(attempts) == 1


def test_network_error_retries_then_gives_up(monkeypatch):
    import requests

    from bayesfutures import telegram as tg_mod

    attempts = []

    def fake_post(url, json=None, timeout=None):
        attempts.append(1)
        raise requests.ConnectionError("끊김")

    monkeypatch.setattr(tg_mod._SESSION, "post", fake_post)
    monkeypatch.setattr(tg_mod.time, "sleep", lambda _s: None)
    assert not tg_mod.Telegram("토큰", "-100", retries=3).send("테스트")
    assert len(attempts) == 3


def test_source_diagnostics_never_expose_the_token(monkeypatch, tmp_path):
    """진단 출력에 토큰 본문이 들어가면 안 된다 — 로그에 남는다."""
    secret = "123456789:AAHsecretsecretsecretsecretsecret"
    cfg = Config()
    cfg.state_dir = str(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret)
    monkeypatch.setenv("TELEGRAM_CHAT_ID_FUTURES", "-5305089060")

    src = cfg.telegram_sources()
    blob = " ".join(src.values())
    assert "AAHsecret" not in blob
    assert secret not in blob
    assert src["token"] == "환경변수 TELEGRAM_BOT_TOKEN"
    assert src["chat_id"] == "환경변수 TELEGRAM_CHAT_ID_FUTURES"
    assert "123456789" in src["token_hint"]      # 봇 id 는 공개 정보


def test_source_diagnostics_report_missing(monkeypatch, tmp_path):
    cfg = Config()
    cfg.state_dir = str(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID_FUTURES", raising=False)
    src = cfg.telegram_sources()
    assert src["token"] == "없음" and src["chat_id"] == "없음"


def test_source_diagnostics_report_file_origin(monkeypatch, tmp_path):
    cfg = Config()
    cfg.state_dir = str(tmp_path)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID_FUTURES", raising=False)
    cfg.save_chat_id("-123")
    assert "파일" in cfg.telegram_sources()["chat_id"]
