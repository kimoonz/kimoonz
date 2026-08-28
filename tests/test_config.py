import os
from datetime import date
from pathlib import Path

import pytest
import yaml

from paradogo.config import (
    MIN_ATTEMPT_INTERVAL_MS,
    MIN_WATCH_INTERVAL_SEC,
    Config,
)
from paradogo.errors import ConfigError

BASE = {
    "site": {"base_url": "https://example.test/", "login_path": "/login", "booking_path": "/book"},
    "account": {"login_id": "u", "password": "p"},
    "target": {"check_in_dates": ["2026-10-03", "2026-10-04"]},
}


def test_urls_join_without_double_slash():
    cfg = Config.from_dict(BASE)
    assert cfg.site.login_url == "https://example.test/login"
    assert cfg.site.booking_url == "https://example.test/book"


def test_env_interpolation(monkeypatch):
    monkeypatch.setenv("MY_PW", "s3cret")
    raw = {**BASE, "account": {"login_id": "u", "password": "${MY_PW}"}}
    cfg = Config.from_dict(raw)
    assert cfg.account.password == "s3cret"


def test_env_interpolation_default_when_unset(monkeypatch):
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    raw = {**BASE, "account": {"login_id": "${NOT_SET_ANYWHERE:-fallback}", "password": "p"}}
    assert Config.from_dict(raw).account.login_id == "fallback"


def test_missing_env_becomes_empty_string(monkeypatch):
    monkeypatch.delenv("ALSO_NOT_SET", raising=False)
    raw = {**BASE, "account": {"login_id": "${ALSO_NOT_SET}", "password": "p"}}
    cfg = Config.from_dict(raw)
    assert cfg.account.login_id == ""
    assert "account.login_id" in " ".join(cfg.validate_for_booking())


def test_dates_parsed_sorted_and_deduped():
    raw = {**BASE, "target": {"check_in_dates": ["2026-10-04", "2026-10-03", "2026-10-04"]}}
    assert Config.from_dict(raw).target.check_in_dates == [
        date(2026, 10, 3),
        date(2026, 10, 4),
    ]


def test_single_date_string_is_accepted():
    raw = {**BASE, "target": {"check_in_dates": "2026-10-03"}}
    assert Config.from_dict(raw).target.check_in_dates == [date(2026, 10, 3)]


def test_bad_date_format_raises():
    raw = {**BASE, "target": {"check_in_dates": ["10/03/2026"]}}
    with pytest.raises(ConfigError):
        Config.from_dict(raw)


def test_empty_dates_raises():
    raw = {**BASE, "target": {"check_in_dates": []}}
    with pytest.raises(ConfigError):
        Config.from_dict(raw)


def test_polling_floors_cannot_be_lowered():
    raw = {
        **BASE,
        "run": {
            "watch": {"interval_seconds": 0.1},
            "open_time": {"attempt_interval_ms": 1, "max_attempts": 99999},
        },
    }
    cfg = Config.from_dict(raw)
    assert cfg.run.watch.interval_seconds == MIN_WATCH_INTERVAL_SEC
    assert cfg.run.open_time.attempt_interval_ms == MIN_ATTEMPT_INTERVAL_MS
    assert cfg.run.open_time.max_attempts <= 120


def test_validate_flags_missing_notification_channel():
    cfg = Config.from_dict(BASE)
    problems = " ".join(cfg.validate_for_booking())
    assert "알림" in problems


def test_example_config_loads(monkeypatch, tmp_path):
    monkeypatch.setenv("PARADOGO_ID", "id")
    monkeypatch.setenv("PARADOGO_PW", "pw")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("SMTP_USER", "u@example.com")
    monkeypatch.setenv("SMTP_PASS", "x")
    cfg = Config.load(Path("config/config.example.yaml"))
    assert cfg.account.login_id == "id"
    assert cfg.notify.telegram.usable
    assert cfg.notify.email.usable
    assert cfg.run.dry_run is True  # 예시 설정은 안전한 기본값이어야 한다


def test_site_defaults_are_real_values_not_slot_descriptors():
    # slots=True dataclass 에서 cls.<field> 를 기본값으로 쓰면 member_descriptor 가 새어나온다.
    cfg = Config.from_dict({"account": {}, "target": {"check_in_dates": ["2026-10-03"]}})
    assert cfg.site.timezone == "Asia/Seoul"
    assert isinstance(cfg.site.base_url, str)
    assert cfg.site.base_url.startswith("https://")


def test_partial_site_block_keeps_other_defaults():
    raw = {**BASE, "site": {"login_path": "/only-login"}}
    cfg = Config.from_dict(raw)
    assert cfg.site.login_path == "/only-login"
    assert cfg.site.timezone == "Asia/Seoul"
    assert cfg.site.base_url == "https://www.paradisespa.co.kr"


def test_cli_style_nights_override_dedupes(monkeypatch):
    # --nights 2,1,2 처럼 중복이 들어와도 우선순위 순서는 유지된다.
    raw = {**BASE, "target": {"check_in_dates": ["2026-09-19"], "nights_options": [2, 1, 2]}}
    assert Config.from_dict(raw).target.nights_options == [2, 1]


def test_nights_zero_or_negative_is_rejected():
    for bad in ([0], [-1]):
        raw = {**BASE, "target": {"check_in_dates": ["2026-09-19"], "nights_options": bad}}
        with pytest.raises(ConfigError):
            Config.from_dict(raw)


def test_non_numeric_nights_is_rejected():
    raw = {**BASE, "target": {"check_in_dates": ["2026-09-19"], "nights_options": ["이틀"]}}
    with pytest.raises(ConfigError):
        Config.from_dict(raw)


def test_console_output_is_made_safe_for_legacy_codepages(monkeypatch, capsys):
    # 한국어 Windows 콘솔(CP949)에 이모지를 찍으면 UnicodeEncodeError 로 죽는다.
    # 알림 문구 하나 때문에 감시가 멈추면 안 된다.
    import io
    import sys

    from paradogo.cli import make_output_safe

    calls = []

    class Stream(io.StringIO):
        def reconfigure(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(sys, "stdout", Stream())
    monkeypatch.setattr(sys, "stderr", Stream())
    make_output_safe()
    assert calls == [
        {"encoding": "utf-8", "errors": "replace"},
        {"encoding": "utf-8", "errors": "replace"},
    ]


def test_output_safety_falls_back_when_encoding_cannot_change(monkeypatch):
    import io
    import sys

    from paradogo.cli import make_output_safe

    calls = []

    class Stubborn(io.StringIO):
        def reconfigure(self, **kwargs):
            if "encoding" in kwargs:
                raise ValueError("이 스트림은 인코딩을 바꿀 수 없음")
            calls.append(kwargs)

    monkeypatch.setattr(sys, "stdout", Stubborn())
    monkeypatch.setattr(sys, "stderr", Stubborn())
    make_output_safe()  # 죽지 않아야 한다
    assert calls == [{"errors": "replace"}, {"errors": "replace"}]


def test_output_safety_ignores_streams_without_reconfigure(monkeypatch):
    import sys

    from paradogo.cli import make_output_safe

    class Old:
        pass

    monkeypatch.setattr(sys, "stdout", Old())
    monkeypatch.setattr(sys, "stderr", Old())
    make_output_safe()  # 옛 파이썬/리다이렉트 상황에서도 죽지 않아야 한다
