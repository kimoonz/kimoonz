"""오픈 시각 계산과 정밀 대기.

로컬 PC 시계는 몇 초씩 틀어져 있는 경우가 많다. 1일 09:00:00 같은 오픈 타이밍에서는
그 몇 초가 성패를 가르므로, 사이트 응답의 ``Date`` 헤더로 오프셋을 재서 보정한다.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger(__name__)

def _korea_timezone() -> tzinfo:
    """한국 시간대.

    Windows 에는 시간대 데이터베이스가 없어서 ZoneInfo("Asia/Seoul") 이 그냥 실패한다
    (tzdata 패키지를 따로 깔아야 한다). 그것 하나 때문에 프로그램이 아예 뜨지 못하면
    안 되므로, 없으면 고정 UTC+9 로 물러선다. 한국은 서머타임이 없어 결과가 같다.
    """
    try:
        return ZoneInfo("Asia/Seoul")
    except Exception as exc:  # ZoneInfoNotFoundError 등
        log.warning(
            "시간대 데이터(tzdata)를 찾지 못해 고정 UTC+9 로 진행합니다. "
            "한국은 서머타임이 없어 계산 결과는 같습니다. (%s)",
            exc,
        )
        return timezone(timedelta(hours=9), "KST")


KST = _korea_timezone()


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def next_open_datetime(
    now: datetime,
    day_of_month: int = 1,
    hour: int = 9,
    minute: int = 0,
) -> datetime:
    """``now`` 이후 가장 가까운 오픈 시각을 돌려준다.

    이번 달 오픈 시각이 아직 안 지났으면 그 시각, 지났으면 다음 달 같은 시각.
    """
    tz = now.tzinfo or KST
    candidate = now.replace(
        day=1, hour=hour, minute=minute, second=0, microsecond=0, tzinfo=tz
    )
    candidate = _with_day(candidate, day_of_month)
    if candidate > now:
        return candidate
    # 다음 달로 넘긴다.
    first_of_next = (candidate.replace(day=1) + timedelta(days=32)).replace(day=1)
    return _with_day(first_of_next, day_of_month)


def _with_day(dt: datetime, day: int) -> datetime:
    """해당 달에 ``day`` 일이 없으면 그 달의 마지막 날로 맞춘다."""
    next_month = (dt.replace(day=1) + timedelta(days=32)).replace(day=1)
    last_day = (next_month - timedelta(days=1)).day
    return dt.replace(day=min(day, last_day))


def open_datetime_for_stay(
    stay_date: date,
    day_of_month: int = 1,
    hour: int = 9,
    minute: int = 0,
    tz: tzinfo = KST,
) -> datetime:
    """그 날짜의 예약이 열렸던(열릴) 시각.

    캐빈파크는 매달 1일에 '다음 달' 예약을 연다. 즉 9월 투숙분은 8월 1일에 열린다.
    이 시각이 이미 지났다면 오픈런으로는 잡을 수 없고 취소표를 노리는 수밖에 없다.
    """
    stay_first = datetime(stay_date.year, stay_date.month, 1, tzinfo=tz)
    open_month = stay_first - timedelta(days=1)  # 전달 말일
    base = open_month.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return _with_day(base.replace(day=1), day_of_month)


def target_stay_month(open_dt: datetime) -> tuple[int, int]:
    """오픈 시각에 열리는 '다음 달' (연, 월)."""
    nxt = (open_dt.replace(day=1) + timedelta(days=32)).replace(day=1)
    return nxt.year, nxt.month


@dataclass(slots=True)
class ClockSync:
    """로컬 시계 대비 서버 시계의 오프셋(초). 양수면 서버가 앞서 있다."""

    offset_seconds: float = 0.0
    measured: bool = False
    detail: str = "보정하지 않음(로컬 시계 사용)"

    def server_now(self) -> datetime:
        return now_kst() + timedelta(seconds=self.offset_seconds)


def sync_with_server(url: str, samples: int = 3, timeout: float = 5.0) -> ClockSync:
    """HTTP ``Date`` 헤더로 서버 시계와의 오프셋을 잰다.

    Date 헤더는 초 단위 해상도라 오차가 ±1초 정도 남는다. 여러 번 재서 중앙값을 쓴다.
    실패해도 예외를 올리지 않고 '보정 없음' 상태로 돌려준다.
    """
    offsets: list[float] = []
    for _ in range(max(1, samples)):
        try:
            sent = datetime.now(tz=timezone.utc)
            resp = requests.head(url, timeout=timeout, allow_redirects=True)
            received = datetime.now(tz=timezone.utc)
            header = resp.headers.get("Date")
            if not header:
                continue
            server_dt = parsedate_to_datetime(header)
            if server_dt.tzinfo is None:
                server_dt = server_dt.replace(tzinfo=timezone.utc)
            # 왕복 지연의 절반을 서버 시각에 더해 응답 시점 기준으로 맞춘다.
            half_rtt = (received - sent).total_seconds() / 2
            local_mid = sent + timedelta(seconds=half_rtt)
            offsets.append((server_dt - local_mid).total_seconds())
        except Exception as exc:  # 네트워크 문제로 전체 실행을 막지 않는다.
            log.debug("시계 동기화 시도 실패: %s", exc)

    if not offsets:
        return ClockSync()

    offsets.sort()
    median = offsets[len(offsets) // 2]
    return ClockSync(
        offset_seconds=median,
        measured=True,
        detail=f"{len(offsets)}회 측정, 오프셋 {median:+.2f}s",
    )


async def sleep_until(target: datetime, clock: ClockSync | None = None) -> None:
    """서버 시계 기준 ``target`` 까지 잔다.

    먼 구간은 성기게 자고, 마지막 0.3초는 촘촘히 돌아 타이밍 오차를 줄인다.
    """
    clock = clock or ClockSync()
    while True:
        remaining = (target - clock.server_now()).total_seconds()
        if remaining <= 0:
            return
        if remaining > 0.3:
            await asyncio.sleep(min(remaining - 0.2, 5.0))
        else:
            await asyncio.sleep(0.005)


def humanize(delta_seconds: float) -> str:
    """남은 시간을 사람이 읽는 문자열로."""
    if delta_seconds < 0:
        return "지남"
    total = int(delta_seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}일")
    if hours:
        parts.append(f"{hours}시간")
    if minutes:
        parts.append(f"{minutes}분")
    parts.append(f"{seconds}초")
    return " ".join(parts)
