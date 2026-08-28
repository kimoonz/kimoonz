"""오픈런: 매달 1일 09:00(KST) 오픈 시각에 맞춰 예약을 시도한다.

핵심은 '오픈 순간에 할 일을 최소로 줄이는 것'이다. 로그인·페이지 진입·달력 이동을
모두 미리 끝내 놓고, 오픈 시각에는 새로고침 후 날짜 클릭부터 시작한다.
"""

from __future__ import annotations

import logging
from datetime import datetime

from .browser import BrowserSession
from .clock import (
    ClockSync,
    humanize,
    next_open_datetime,
    now_kst,
    sleep_until,
    sync_with_server,
    target_stay_month,
)
from .config import Config
from .flow import BookingFlow, BookingResult
from .notify import Notifier
from .selectors import SelectorMap

log = logging.getLogger(__name__)


async def run_snipe(
    cfg: Config,
    smap: SelectorMap,
    notifier: Notifier,
    open_at: datetime | None = None,
) -> BookingResult:
    open_cfg = cfg.run.open_time
    clock = ClockSync()
    if open_cfg.sync_clock:
        clock = sync_with_server(cfg.site.base_url)
        log.info("시계 보정: %s", clock.detail)

    target_open = open_at or next_open_datetime(
        clock.server_now(), open_cfg.day_of_month, open_cfg.hour, open_cfg.minute
    )
    stay_year, stay_month = target_stay_month(target_open)
    wait_seconds = (target_open - clock.server_now()).total_seconds()

    log.info(
        "오픈 예정: %s (남은 시간 %s) — 이때 열리는 투숙 월: %d년 %d월",
        target_open.strftime("%Y-%m-%d %H:%M:%S %Z"),
        humanize(wait_seconds),
        stay_year,
        stay_month,
    )

    dates = cfg.target.check_in_dates
    off_month = [d for d in dates if (d.year, d.month) != (stay_year, stay_month)]
    if off_month:
        log.warning(
            "이번 오픈에 열리지 않는 날짜가 섞여 있습니다: %s",
            ", ".join(d.isoformat() for d in off_month),
        )

    async with BrowserSession(cfg) as session:
        flow = BookingFlow(session, smap, cfg)

        # 1) 사전 준비 — 오픈 전에 끝내야 하는 것들
        await flow.ensure_logged_in()
        await flow.goto_booking()
        await flow.ensure_month(stay_year, stay_month)

        notifier.send(
            "🏕️ 캐빈 오픈런 대기 시작",
            "\n".join(
                [
                    f"오픈 시각: {target_open.strftime('%Y-%m-%d %H:%M:%S')} (KST)",
                    f"남은 시간: {humanize((target_open - clock.server_now()).total_seconds())}",
                    f"대상 날짜: {', '.join(d.isoformat() for d in dates)}",
                    f"희망 캐빈: {', '.join(cfg.target.cabin_types) or '전체'}",
                    f"시계 보정: {clock.detail}",
                    f"모의 실행(dry_run): {'예' if cfg.run.dry_run else '아니오'}",
                ]
            ),
        )

        # 2) 오픈 lead_seconds 전에 세션을 한 번 되살린다.
        prep_at = target_open.timestamp() - open_cfg.lead_seconds
        prep_dt = datetime.fromtimestamp(prep_at, tz=target_open.tzinfo)
        if prep_dt > clock.server_now():
            log.info("오픈 %d초 전까지 대기합니다…", open_cfg.lead_seconds)
            await sleep_until(prep_dt, clock)
        log.info("사전 새로고침 — 세션과 달력을 최신 상태로 맞춥니다.")
        await flow.goto_booking()
        if not await flow.is_logged_in():
            log.warning("세션이 풀렸습니다. 다시 로그인합니다.")
            await flow.login()
            await flow.goto_booking()
        await flow.ensure_month(stay_year, stay_month)

        # 3) 오픈 시각 정각까지 정밀 대기
        await sleep_until(target_open, clock)
        log.info("오픈! 예약 시도를 시작합니다.")

        last: BookingResult | None = None
        for attempt_no in range(1, open_cfg.max_attempts + 1):
            try:
                if attempt_no > 1:
                    await flow.goto_booking()
                    await flow.ensure_month(stay_year, stay_month)
                result = await flow.attempt(dates)
                last = result
                if result.ok:
                    break
                log.info("%d/%d 시도: %s", attempt_no, open_cfg.max_attempts, result.message)
            except Exception as exc:
                log.warning("%d회차 시도 중 오류: %s", attempt_no, exc)
                last = BookingResult(ok=False, stage="failed", message=str(exc))
            await session.page.wait_for_timeout(open_cfg.attempt_interval_ms)

        result = last or BookingResult(
            ok=False, stage="failed", message="시도를 한 번도 수행하지 못했습니다."
        )
        await _announce(notifier, result, cfg)
        if result.reached_payment:
            await flow.keep_open(cfg.run.keep_open_minutes)
        return result


async def _announce(notifier: Notifier, result: BookingResult, cfg: Config) -> None:
    if result.reached_payment:
        title = "✅ 결제 페이지 도달 — 지금 결제하세요!"
    elif result.stage == "dry_run":
        title = "🔎 [모의 실행] 빈자리 발견"
    elif result.stage == "no_availability":
        title = "❌ 오픈런 실패 — 빈자리 없음"
    else:
        title = "⚠️ 오픈런 중단 — 오류"

    body_lines = [result.message]
    if result.stay_date:
        body_lines.append(f"날짜: {result.stay_date}")
    if result.cabin:
        body_lines.append(f"캐빈: {result.cabin}")
    if result.reached_payment:
        body_lines.append(f"브라우저는 {cfg.run.keep_open_minutes}분간 열려 있습니다.")
    notifier.send(title, "\n".join(body_lines), screenshot=result.screenshot, url=result.url)
