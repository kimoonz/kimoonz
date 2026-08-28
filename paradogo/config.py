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
# 추적 폴링 하한. API 한 번(JSON 한 건)은 페이지 전체 로딩보다 가벼우므로 더 짧게 허용한다.
MIN_TRACK_INTERVAL_API_SEC = 5.0
MIN_TRACK_INTERVAL_DOM_SEC = 15.0
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
    nights_options: list[int] = field(default_factory=lambda: [1])  # 우선순위 순서
    cabin_types: list[str] = field(default_factory=list)  # 우선순위 순서. 비우면 아무 캐빈이나.
    zones: list[str] = field(default_factory=list)         # 구역 A~H, 우선순위 순서
    exclude_zones: list[str] = field(default_factory=list)
    zone_pattern: str = ""       # 구역 표기가 특이할 때 쓰는 정규식(캡처 그룹 1번)
    zone_strict: bool = True     # 구역을 못 읽은 캐빈은 예약 후보에서 뺀다
    adults: int = 2
    children: int = 0

    @property
    def nights(self) -> int:
        """1순위 박수. 기존 설정과의 호환을 위해 남겨 둔다."""
        return self.nights_options[0] if self.nights_options else 1

    @property
    def zone_patterns(self) -> tuple[str, ...] | None:
        return (self.zone_pattern,) if self.zone_pattern else None

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

        # nights_options 가 있으면 그것을, 없으면 기존 nights 한 값을 쓴다.
        raw_nights = raw.get("nights_options")
        if raw_nights is None:
            raw_nights = [raw.get("nights", 1)]
        elif isinstance(raw_nights, (int, str)):
            raw_nights = [raw_nights]
        nights: list[int] = []
        for value in raw_nights:
            try:
                count = int(value)
            except (TypeError, ValueError):
                raise ConfigError(f"target.nights_options 의 '{value}' 는 숫자가 아닙니다.")
            if count < 1:
                raise ConfigError("target.nights_options 는 1 이상이어야 합니다.")
            if count not in nights:
                nights.append(count)
        if not nights:
            nights = [1]

        zones = raw.get("zones") or []
        if isinstance(zones, str):
            zones = [zones]
        excluded = raw.get("exclude_zones") or []
        if isinstance(excluded, str):
            excluded = [excluded]

        return cls(
            check_in_dates=sorted(set(parsed)),
            nights_options=nights,
            cabin_types=[str(c) for c in cabins],
            zones=[str(z) for z in zones],
            exclude_zones=[str(z) for z in excluded],
            zone_pattern=str(raw.get("zone_pattern") or ""),
            zone_strict=bool(raw.get("zone_strict", True)),
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
class ApiConfig:
    """사이트가 쓰는 재고 조회 API. `sniff` 명령으로 찾아서 채운다.

    비어 있으면 추적기는 달력 DOM을 읽는 방식으로 자동 폴백한다(느리고 날짜 단위).
    """

    enabled: bool = False
    url_template: str = ""   # {year} {month} {month02} {ym} 사용 가능
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body_template: str = ""  # POST 일 때 보낼 JSON 문자열 (같은 placeholder 사용)
    items_path: str = ""     # 응답에서 목록이 있는 경로. 'data.list' 처럼 점으로 구분
    date_field: str = "date"     # 필드명, 또는 '{y}-{m}-{d}' 같은 템플릿
    cabin_field: str = ""        # 비우면 날짜 단위로만 추적
    zone_field: str = ""         # 구역을 따로 주는 API 라면 그 필드명
    remaining_field: str = ""
    price_field: str = ""
    status_field: str = ""
    status_available_values: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.enabled and bool(self.url_template) and bool(self.date_field)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ApiConfig":
        values = raw.get("status_available_values") or []
        if isinstance(values, str):
            values = [values]
        headers = raw.get("headers") or {}
        return cls(
            enabled=bool(raw.get("enabled", False)),
            url_template=str(raw.get("url") or raw.get("url_template") or ""),
            method=str(raw.get("method", "GET")).upper(),
            headers={str(k): str(v) for k, v in headers.items()},
            body_template=str(raw.get("body") or raw.get("body_template") or ""),
            items_path=str(raw.get("items_path") or ""),
            date_field=str(raw.get("date_field") or "date"),
            cabin_field=str(raw.get("cabin_field") or ""),
            zone_field=str(raw.get("zone_field") or ""),
            remaining_field=str(raw.get("remaining_field") or ""),
            price_field=str(raw.get("price_field") or ""),
            status_field=str(raw.get("status_field") or ""),
            status_available_values=[str(v) for v in values],
        )


@dataclass(slots=True)
class TrackConfig:
    """실시간 재고 추적."""

    interval_seconds: float = 20.0
    jitter_seconds: float = 3.0
    months_ahead: int = 2          # 이번 달 포함 몇 개월치를 추적할지
    auto_reserve: bool = True      # 취소 감지 시 결제 직전까지 자동 진행
    notify_all_changes: bool = False  # 대상 외 날짜의 전환도 알릴지
    reserve_cooldown_minutes: int = 10  # 같은 칸 재시도 최소 간격
    max_duration_minutes: int = 0  # 0 이면 무제한
    dashboard: bool = True
    db_path: Path = Path(".state/tracker.db")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TrackConfig":
        return cls(
            interval_seconds=float(raw.get("interval_seconds", 20)),
            jitter_seconds=max(0.0, float(raw.get("jitter_seconds", 3))),
            months_ahead=max(1, int(raw.get("months_ahead", 2))),
            auto_reserve=bool(raw.get("auto_reserve", True)),
            notify_all_changes=bool(raw.get("notify_all_changes", False)),
            reserve_cooldown_minutes=max(0, int(raw.get("reserve_cooldown_minutes", 10))),
            max_duration_minutes=max(0, int(raw.get("max_duration_minutes", 0))),
            dashboard=bool(raw.get("dashboard", True)),
            db_path=Path(raw.get("db_path", ".state/tracker.db")),
        )

    def effective_interval(self, source: str) -> float:
        """소스별 하한선을 적용한 실제 폴링 주기."""
        floor = (
            MIN_TRACK_INTERVAL_API_SEC if source == "api" else MIN_TRACK_INTERVAL_DOM_SEC
        )
        return max(floor, self.interval_seconds)


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
    track: TrackConfig = field(default_factory=TrackConfig)

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
            track=TrackConfig.from_dict(raw.get("track") or {}),
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
    api: ApiConfig = field(default_factory=ApiConfig)
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
            api=ApiConfig.from_dict(raw.get("api") or {}),
            source_path=source,
        )

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        p = Path(path)
        if not p.exists():
            raise ConfigError(
                f"설정 파일이 없습니다: {p}\n"
                "→ 처음이시면 `python -m paradogo start` 를 실행하세요. "
                "(브라우저 안내를 따라가면 설정이 자동으로 만들어집니다)"
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
