"""취소표 감시.

오픈런에서 놓쳤을 때 쓴다. 일정 주기로 예약 페이지를 확인하다가 빈자리가 보이면
알림을 보내고(설정에 따라) 결제 직전까지 진행한다.

주기에는 하한선(10초)과 무작위 지터가 걸려 있다. 사이트에 부담을 주지 않기 위한
장치이므로 그대로 두는 것을 권한다.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import timedelta

from .browser import BrowserSession
from .clock import humanize, now_kst
from .config import Config
from .flow import BookingFlow, BookingResult
from .notify import Notifier
from .selectors import SelectorMap

log = logging.getLogger(__name__)


async def run_watch(cfg: Config, smap: SelectorMap, notifier: Notifier) -> BookingResult:
    watch = cfg.run.watch
    deadline = now_kst() + timedelta(minutes=watch.max_duration_minutes)

    notifier.send(
        "👀 캐빈 취소표 감시 시작",
        "\n".join(
            [
                f"대상 날짜: {', '.join(d.isoformat() for d in cfg.target.check_in_dates)}",
                f"희망 캐빈: {', '.join(cfg.target.cabin_types) or '전체'}",
                f"확인 주기: {watch.interval_seconds:.0f}초 (±{watch.jitter_seconds:.0f}초)",
                f"종료 예정: {deadline.strftime('%Y-%m-%d %H:%M')} "
                f"({humanize(watch.max_duration_minutes * 60)} 후)",
                f"빈자리 발견 시 자동 진행: {'예(결제 직전까지)' if watch.auto_reserve else '아니오(알림만)'}",
            ]
        ),
    )

    # auto_reserve 가 꺼져 있으면 발견만 하고 클릭하지 않는다.
    original_dry_run = cfg.run.dry_run
    if not watch.auto_reserve:
        cfg.run.dry_run = True

    round_no = 0
    consecutive_errors = 0
    try:
        async with BrowserSession(cfg) as session:
            flow = BookingFlow(session, smap, cfg)
            await flow.ensure_logged_in()

            while now_kst() < deadline:
                round_no += 1
                try:
                    await flow.goto_booking()
                    result = await flow.attempt()
                    consecutive_errors = 0
                except Exception as exc:
                    consecutive_errors += 1
                    log.warning("%d회차 확인 중 오류(%d연속): %s", round_no, consecutive_errors, exc)
                    if consecutive_errors >= 5:
                        shot = await session.screenshot("watch-repeated-error")
                        notifier.send(
                            "⚠️ 감시 중단 — 오류 5회 연속",
                            f"마지막 오류: {exc}",
                            screenshot=shot,
                        )
                        return BookingResult(ok=False, stage="failed", message=str(exc))
                    await asyncio.sleep(watch.interval_seconds)
                    continue

                if result.ok:
                    title = (
                        "✅ 결제 페이지 도달 — 지금 결제하세요!"
                        if result.reached_payment
                        else "🔎 빈자리 발견"
                    )
                    body = [result.message]
                    if result.stay_date:
                        body.append(f"날짜: {result.stay_date}")
                    if result.cabin:
                        body.append(f"캐빈: {result.cabin}")
                    notifier.send(
                        title,
                        "\n".join(body),
                        screenshot=result.screenshot,
                        url=result.url,
                    )
                    if result.reached_payment:
                        await flow.keep_open(cfg.run.keep_open_minutes)
                        return result
                    # 알림만 모드: 계속 감시한다.

                log.info(
                    "%d회차: 빈자리 없음. 다음 확인까지 대기합니다.", round_no
                )
                jitter = random.uniform(-watch.jitter_seconds, watch.jitter_seconds)
                await asyncio.sleep(max(5.0, watch.interval_seconds + jitter))
    finally:
        cfg.run.dry_run = original_dry_run

    notifier.send(
        "⏹️ 감시 종료",
        f"{round_no}회 확인했지만 조건에 맞는 빈자리를 찾지 못했습니다.",
    )
    return BookingResult(
        ok=False,
        stage="no_availability",
        message=f"{round_no}회 확인, 빈자리 없음.",
    )
