"""YAML 설정 로딩.

값 안에 ``${ENV}`` / ``${ENV:-기본값}`` 을 쓰면 환경변수로 치환된다.
비밀번호나 토큰은 YAML에 직접 적지 말고 이 방식으로 .env / 셸 환경변수에 두는 것을 권장.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")

# 사이트에 부담을 주지 않기 위한 하한선. 설정으로 더 짧게 낮출 수 없다.
MIN_WATCH_INTERVAL_SEC = 10.0
MIN_ATTEMPT_INTERVAL_MS = 300
MAX_OPEN_ATTEMPTS = 120


def _expand(value: Any) -> Any:
    """문자열 안의 ``${ENV}`` 치환을 재귀적으로 적용한다."""
    if isinstance(value, str):
        def sub(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            got = os.environ.get(name)
            if got is not None:
                return got
            if default is not None:
                return default
            return ""
        return _ENV_PATTERN.sub(sub, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping or mapping[key] in (None, "", []):
        raise ConfigError(f"설정 '{where}.{key}' 가 비어 있습니다.")
    return mapping[key]


# slots=True 인 dataclass 는 클래스 속성으로 기본값을 남기지 않으므로
# (cls.base_url 이 member_descriptor 가 된다) 기본값은 여기에 따로 둔다.
SITE_DEFAULTS = {
    "base_url": "https://www.paradisespa.co.kr",
    "login_path": "/",
    "booking_path": "/",
    "timezone": "Asia/Seoul",
}


@dataclass(slots=True)
class SiteConfig:
    base_url: str = SITE_DEFAULTS["base_url"]
    login_path: str = SITE_DEFAULTS["login_path"]
    booking_path: str = SITE_DEFAULTS["booking_path"]
    timezone: str = SITE_DEFAULTS["timezone"]

    @property
    def login_url(self) -> str:
        return self.base_url.rstrip("/") + "/" + self.login_path.lstrip("/")

    @property
    def booking_url(self) -> str:
        return self.base_url.rstrip("/") + "/" + self.booking_path.lstrip("/")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SiteConfig":
        return cls(
            base_url=str(raw.get("base_url") or SITE_DEFAULTS["base_url"]).rstrip("/"),
            login_path=str(raw.get("login_path") or SITE_DEFAULTS["login_path"]),
            booking_path=str(raw.get("booking_path") or SITE_DEFAULTS["booking_path"]),
            timezone=str(raw.get("timezone") or SITE_DEFAULTS["timezone"]),
        )


@dataclass(slots=True)
class BookerInfo:
    """예약자 정보. 로그인 계정에 이미 저장돼 있으면 비워둬도 된다."""

    name: str = ""
    phone: str = ""
    email: str = ""


@dataclass(slots=True)
class AccountConfig:
    login_id: str = ""
    password: str = ""
    booker: BookerInfo = field(default_factory=BookerInfo)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AccountConfig":
        booker_raw = raw.get("booker") or {}
        return cls(
            login_id=str(raw.get("login_id") or ""),
            password=str(raw.get("password") or ""),
            booker=BookerInfo(
                name=str(booker_raw.get("name") or ""),
                phone=str(booker_raw.get("phone") or ""),
                email=str(booker_raw.get("email") or ""),
            ),
        )


@dataclass(slots=True)
class TargetConfig:
    """무엇을 잡고 싶은지."""

    check_in_dates: list[date] = field(default_factory=list)
    nights: int = 1
    cabin_types: list[str] = field(default_factory=list)  # 우선순위 순서. 비우면 아무 캐빈이나.
    adults: int = 2
    children: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TargetConfig":
        raw_dates = _require(raw, "check_in_dates", "target")
        if isinstance(raw_dates, (str, date)):
            raw_dates = [raw_dates]
        parsed: list[date] = []
        for item in raw_dates:
            if isinstance(item, date):
                parsed.append(item)
                continue
            try:
                parsed.append(date.fromisoformat(str(item)))
            except ValueError as exc:
                raise ConfigError(
                    f"target.check_in_dates 의 '{item}' 은 YYYY-MM-DD 형식이 아닙니다."
                ) from exc
        cabins = raw.get("cabin_types") or []
        if isinstance(cabins, str):
            cabins = [cabins]
        return cls(
            check_in_dates=sorted(set(parsed)),
            nights=int(raw.get("nights", 1)),
            cabin_types=[str(c) for c in cabins],
            adults=int(raw.get("adults", 2)),
            children=int(raw.get("children", 0)),
        )


@dataclass(slots=True)
class OpenTimeConfig:
    """예약 오픈 시각. 캐빈파크는 매달 1일 09:00(KST)에 다음 달 예약이 열린다."""

    day_of_month: int = 1
    hour: int = 9
    minute: int = 0
    lead_seconds: int = 30          # 오픈 몇 초 전부터 대기 자세로 들어갈지
    max_attempts: int = 40          # 오픈 후 재시도 횟수 상한
    attempt_interval_ms: int = 700  # 재시도 간격
    sync_clock: bool = True         # 서버 Date 헤더로 시계 보정

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "OpenTimeConfig":
        return cls(
            day_of_month=int(raw.get("day_of_month", 1)),
            hour=int(raw.get("hour", 9)),
            minute=int(raw.get("minute", 0)),
            lead_seconds=max(5, int(raw.get("lead_seconds", 30))),
            max_attempts=min(MAX_OPEN_ATTEMPTS, max(1, int(raw.get("max_attempts", 40)))),
            attempt_interval_ms=max(
                MIN_ATTEMPT_INTERVAL_MS, int(raw.get("attempt_interval_ms", 700))
            ),
            sync_clock=bool(raw.get("sync_clock", True)),
        )


@dataclass(slots=True)
class WatchConfig:
    """취소표 감시 주기."""

    interval_seconds: float = 30.0
    jitter_seconds: float = 10.0
    max_duration_minutes: int = 720
    auto_reserve: bool = True  # 빈자리를 찾으면 결제 직전까지 진행할지

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WatchConfig":
        return cls(
            interval_seconds=max(
                MIN_WATCH_INTERVAL_SEC, float(raw.get("interval_seconds", 30))
            ),
            jitter_seconds=max(0.0, float(raw.get("jitter_seconds", 10))),
            max_duration_minutes=int(raw.get("max_duration_minutes", 720)),
            auto_reserve=bool(raw.get("auto_reserve", True)),
        )


@dataclass(slots=True)
class RunConfig:
    headless: bool = False
    slow_mo_ms: int = 0
    dry_run: bool = True
    keep_open_minutes: int = 30
    storage_state: Path = Path(".state/session.json")
    artifacts_dir: Path = Path(".artifacts")
    open_time: OpenTimeConfig = field(default_factory=OpenTimeConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RunConfig":
        return cls(
            headless=bool(raw.get("headless", False)),
            slow_mo_ms=int(raw.get("slow_mo_ms", 0)),
            dry_run=bool(raw.get("dry_run", True)),
            keep_open_minutes=int(raw.get("keep_open_minutes", 30)),
            storage_state=Path(raw.get("storage_state", ".state/session.json")),
            artifacts_dir=Path(raw.get("artifacts_dir", ".artifacts")),
            open_time=OpenTimeConfig.from_dict(raw.get("open_time") or {}),
            watch=WatchConfig.from_dict(raw.get("watch") or {}),
        )


@dataclass(slots=True)
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""

    @property
    def usable(self) -> bool:
        return self.enabled and bool(self.bot_token) and bool(self.chat_id)


@dataclass(slots=True)
class EmailConfig:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    use_tls: bool = True
    username: str = ""
    password: str = ""
    sender: str = ""
    recipients: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.enabled and bool(self.smtp_host) and bool(self.recipients)


@dataclass(slots=True)
class NotifyConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    email: EmailConfig = field(default_factory=EmailConfig)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "NotifyConfig":
        tg_raw = raw.get("telegram") or {}
        em_raw = raw.get("email") or {}
        recipients = em_raw.get("recipients") or []
        if isinstance(recipients, str):
            recipients = [recipients]
        return cls(
            telegram=TelegramConfig(
                enabled=bool(tg_raw.get("enabled", False)),
                bot_token=str(tg_raw.get("bot_token") or ""),
                chat_id=str(tg_raw.get("chat_id") or ""),
            ),
            email=EmailConfig(
                enabled=bool(em_raw.get("enabled", False)),
                smtp_host=str(em_raw.get("smtp_host") or ""),
                smtp_port=int(em_raw.get("smtp_port", 587)),
                use_tls=bool(em_raw.get("use_tls", True)),
                username=str(em_raw.get("username") or ""),
                password=str(em_raw.get("password") or ""),
                sender=str(em_raw.get("sender") or em_raw.get("username") or ""),
                recipients=[str(r) for r in recipients if str(r).strip()],
            ),
        )


@dataclass(slots=True)
class Config:
    site: SiteConfig
    account: AccountConfig
    target: TargetConfig
    run: RunConfig
    notify: NotifyConfig
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any], source: Path | None = None) -> "Config":
        raw = _expand(raw)
        if not isinstance(raw, dict):
            raise ConfigError("설정 파일의 최상위는 매핑(key: value)이어야 합니다.")
        return cls(
            site=SiteConfig.from_dict(raw.get("site") or {}),
            account=AccountConfig.from_dict(raw.get("account") or {}),
            target=TargetConfig.from_dict(raw.get("target") or {}),
            run=RunConfig.from_dict(raw.get("run") or {}),
            notify=NotifyConfig.from_dict(raw.get("notify") or {}),
            source_path=source,
        )

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        p = Path(path)
        if not p.exists():
            raise ConfigError(
                f"설정 파일이 없습니다: {p}\n"
                "→ `cp config/config.example.yaml config/config.yaml` 후 값을 채워 주세요."
            )
        with p.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls.from_dict(raw, source=p)

    def validate_for_booking(self) -> list[str]:
        """예약을 실제로 진행하기 전에 부족한 값을 목록으로 돌려준다(예외를 던지지 않음)."""
        problems: list[str] = []
        if not self.account.login_id:
            problems.append("account.login_id 가 비어 있습니다.")
        if not self.account.password:
            problems.append("account.password 가 비어 있습니다(환경변수 치환을 확인하세요).")
        if not self.target.check_in_dates:
            problems.append("target.check_in_dates 가 비어 있습니다.")
        if not (self.notify.telegram.usable or self.notify.email.usable):
            problems.append(
                "알림 채널이 하나도 활성화되지 않았습니다. 결제 직전 단계를 놓칠 수 있습니다."
            )
        return problems
