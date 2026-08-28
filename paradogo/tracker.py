"""실시간 재고 추적 — 취소가 나오는 순간을 잡는다.

한 번의 순환은 이렇다.

1. 그 달(그리고 다음 몇 달) 전체 재고를 한 장의 스냅샷으로 읽는다.
2. 직전 스냅샷과 diff 해서 **마감 → 예약가능** 전환을 찾는다. 그게 방금 나온 취소다.
3. 전환을 DB에 남기고 알린다.
4. 내가 노리는 날짜/캐빈이면 곧바로 예약 플로우로 넘어가 결제 직전까지 진행한다.

``watch`` 와의 차이: watch 는 매번 "빈자리 있나?"만 새로 묻는다. 추적기는 상태를
기억하므로 '원래 비어 있던 자리'와 '방금 풀린 자리'를 구분하고, 이력이 쌓여
어느 날짜가 언제 잘 풀리는지도 볼 수 있다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta

from .browser import BrowserSession
from .clock import now_kst
from .config import Config
from .dashboard import draw, render_board, supports_color
from .flow import BookingFlow, BookingResult
from .inventory import Change, ChangeKind, Snapshot, TargetFilter, diff
from .notify import Notifier
from .selectors import SelectorMap
from .sources import ApiSource, DomSource, SourceError
from .store import TrackerStore
from .zones import ZonePreference

log = logging.getLogger(__name__)

# API 모드에서는 브라우저 페이지가 놀고 있어 세션이 조용히 만료될 수 있다.
SESSION_REFRESH_MINUTES = 15

# 저장된 상태가 이보다 오래됐으면 비교 대상으로 쓰지 않는다.
# 재시작 직후(몇 분)라면 그 사이의 취소까지 잡아내므로 비교하는 게 이득이지만,
# 반나절 만에 켰다면 그동안의 모든 변화가 '방금 취소'로 쏟아진다.
STALE_STATE_MINUTES = 60


def upcoming_months(start: datetime, count: int) -> list[tuple[int, int]]:
    """이번 달부터 count 개월치 (연, 월) 목록."""
    months: list[tuple[int, int]] = []
    year, month = start.year, start.month
    for _ in range(max(1, count)):
        months.append((year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def months_to_track(
    now: datetime, target_dates: Iterable[date], months_ahead: int
) -> list[tuple[int, int]]:
    """실제로 조회할 (연, 월) 목록.

    '오늘부터 N개월'만 보면 목표 날짜가 그 범위 밖일 때 통째로 놓친다
    (8월에 10월 예약을 노리는 경우). 목표 날짜가 든 달은 무조건 포함한다.
    """
    months = set(upcoming_months(now, months_ahead))
    months.update((d.year, d.month) for d in target_dates)
    return sorted(months)


def summarize(changes: list[Change], limit: int = 12) -> str:
    lines = [str(change) for change in changes[:limit]]
    if len(changes) > limit:
        lines.append(f"… 외 {len(changes) - limit}건")
    return "\n".join(lines)


class Tracker:
    def __init__(
        self,
        cfg: Config,
        smap: SelectorMap,
        notifier: Notifier,
        store: TrackerStore,
    ) -> None:
        self.cfg = cfg
        self.smap = smap
        self.notifier = notifier
        self.store = store
        self.targets = TargetFilter(
            dates=frozenset(d.isoformat() for d in cfg.target.check_in_dates),
            cabin_keywords=tuple(cfg.target.cabin_types),
            zones=ZonePreference.build(
                cfg.target.zones, cfg.target.exclude_zones, cfg.target.zone_strict
            ),
        )
        self._cooldown: dict[tuple[str, str], datetime] = {}
        self._recent: list[Change] = []
        self.round_no = 0

    # -------------------------------------------------------------- 쿨다운

    def can_attempt(self, key: tuple[str, str]) -> bool:
        """같은 칸을 계속 두드리지 않도록 최소 간격을 둔다."""
        minutes = self.cfg.run.track.reserve_cooldown_minutes
        if minutes <= 0:
            return True
        last = self._cooldown.get(key)
        return last is None or (now_kst() - last) >= timedelta(minutes=minutes)

    def mark_attempt(self, key: tuple[str, str]) -> None:
        self._cooldown[key] = now_kst()

    # -------------------------------------------------------------- 알림

    def announce(self, changes: list[Change], bookable: list[Change]) -> None:
        track = self.cfg.run.track
        if bookable:
            self.notifier.send(
                f"🚨 취소 발생 — {len(bookable)}건",
                summarize(bookable)
                + ("\n\n예약을 시도합니다…" if track.auto_reserve else ""),
            )
            return
        if not track.notify_all_changes:
            return
        interesting = [c for c in changes if c.kind is not ChangeKind.VANISHED]
        if interesting:
            self.notifier.send(
                f"ℹ️ 재고 변화 {len(interesting)}건", summarize(interesting)
            )

    def announce_result(self, result: BookingResult) -> None:
        if result.reached_payment:
            title = "✅ 취소표 확보 — 결제 페이지입니다. 지금 결제하세요!"
            if os.environ.get("PARADOGO_HEADLESS") == "1" or self.cfg.run.headless:
                # 창이 없는 상태라 화면을 보여줄 수 없다. 어디서 결제해야 하는지 알려준다.
                title = "✅ 취소표 확보 — 홈페이지에서 결제해 주세요!"
        elif result.stage == "dry_run":
            title = "🔎 [모의 실행] 취소표 발견"
        else:
            title = "⚠️ 취소표를 잡지 못했습니다"
        body = [result.message]
        if result.stay_date:
            nights = f" ({result.nights}박)" if result.nights else ""
            body.append(f"날짜: {result.stay_date}{nights}")
        if result.cabin:
            zone = f"[{result.zone}] " if result.zone else ""
            body.append(f"캐빈: {zone}{result.cabin}")
        if result.reached_payment and (
            os.environ.get("PARADOGO_HEADLESS") == "1" or self.cfg.run.headless
        ):
            body.append(
                "\n창이 없는 상태(백그라운드)로 잡았습니다. 직접 홈페이지에 로그인해서 "
                "'예약확인 / 결제대기'에서 결제를 마쳐 주세요. 시간이 지나면 풀립니다."
            )
        self.notifier.send(
            title, "\n".join(body), screenshot=result.screenshot, url=result.url
        )

    # -------------------------------------------------------------- 본체

    async def run(
        self,
        session: BrowserSession,
        on_round: "Callable[[dict[str, object]], None] | None" = None,
    ) -> BookingResult | None:
        cfg = self.cfg
        track = cfg.run.track
        flow = BookingFlow(session, self.smap, cfg)
        await flow.ensure_logged_in()

        if cfg.api.usable:
            source = ApiSource(cfg.api, session.context.request, cfg.target.zone_patterns)
            log.info("재고 조회 경로: API (%s)", cfg.api.url_template)
        else:
            source = DomSource(flow, self.smap)
            log.info("재고 조회 경로: 달력 DOM (API 설정이 없어 폴백). "
                     "`sniff` 로 재고 API를 찾으면 훨씬 빨라집니다.")

        interval = track.effective_interval(source.name)
        months = months_to_track(now_kst(), cfg.target.check_in_dates, track.months_ahead)
        previous: Snapshot | None = self.store.load_state(source.name)
        if previous is not None:
            age = (now_kst() - previous.taken_at).total_seconds() / 60
            if age > STALE_STATE_MINUTES:
                log.info(
                    "저장된 상태가 %.0f분 전 것이라 기준선으로만 씁니다"
                    "(그 사이 변화를 '방금 취소'로 알리지 않기 위해).",
                    age,
                )
                previous = None
            else:
                log.info(
                    "직전 상태 %d칸(%.0f분 전)을 이어받아 그 사이 변화까지 확인합니다.",
                    len(previous.slots),
                    age,
                )

        deadline = (
            now_kst() + timedelta(minutes=track.max_duration_minutes)
            if track.max_duration_minutes
            else None
        )
        self.notifier.send(
            "📡 재고 추적 시작",
            "\n".join(
                [
                    f"대상 날짜: {', '.join(sorted(self.targets.dates)) or '전체'}",
                    f"희망 캐빈: {', '.join(self.targets.cabin_keywords) or '전체'}",
                    f"희망 구역: {', '.join(self.targets.zones.wanted) or '전체'}"
                    + (f" (제외 {', '.join(sorted(self.targets.zones.excluded))})"
                       if self.targets.zones.excluded else ""),
                    f"박수: {', '.join(f'{n}박' for n in cfg.target.nights_options)}",
                    f"조회 경로: {source.name} · 주기 {interval:.0f}초",
                    f"추적 범위: {', '.join(f'{y}-{m:02d}' for y, m in months)}",
                    f"취소 감지 시 자동 진행: {'예(결제 직전까지)' if track.auto_reserve else '아니오(알림만)'}",
                ]
            ),
        )

        color = supports_color()
        consecutive_errors = 0
        last_refresh = now_kst()

        while deadline is None or now_kst() < deadline:
            self.round_no += 1
            started = time.monotonic()
            try:
                snapshot = await source.fetch(months)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self.store.record_poll(source.name, True, len(snapshot.slots), elapsed_ms)
                consecutive_errors = 0
            except Exception as exc:  # SourceError 포함 — 한 번의 실패로 추적을 멈추지 않는다
                elapsed_ms = int((time.monotonic() - started) * 1000)
                consecutive_errors += 1
                self.store.record_poll(source.name, False, 0, elapsed_ms, str(exc))
                log.warning("%d회차 조회 실패(%d연속): %s", self.round_no, consecutive_errors, exc)
                if consecutive_errors >= 5:
                    self.notifier.send(
                        "⚠️ 재고 추적 중단 — 조회 5회 연속 실패",
                        f"마지막 오류: {exc}",
                    )
                    # None 을 돌려주면 '정상적으로 한 바퀴 돌았다'와 구분되지 않는다.
                    # 상시 감시 쪽에서 재시작으로 세려면 실패라고 말해야 한다.
                    return BookingResult(
                        ok=False,
                        stage="failed",
                        message=f"재고 조회가 5회 연속 실패했습니다: {exc}",
                    )
                await asyncio.sleep(interval)
                continue

            changes = diff(previous, snapshot)
            if changes:
                self.store.record_events(changes)
                self._recent = changes + self._recent
                del self._recent[40:]
            self.store.save_state(snapshot, [c.slot.key for c in changes])
            previous = snapshot

            bookable = self.targets.bookable(changes)
            if changes:
                log.info("변화 %d건: %s", len(changes), summarize(changes, 5).replace("\n", " | "))
            self.announce(changes, bookable)

            if bookable and track.auto_reserve:
                result = await self.grab(flow, bookable)
                # 성공이든 실패든 알린다. 취소를 잡으려다 놓친 것을 조용히 넘기면
                # 사용자는 추적기가 놀고 있는 줄 안다.
                if result is not None:
                    self.announce_result(result)
                    if result.reached_payment:
                        await flow.keep_open(self.cfg.run.keep_open_minutes)
                        return result

            # API 모드에서는 페이지가 놀고 있으므로 주기적으로 세션을 되살린다.
            if source.name == "api" and (now_kst() - last_refresh) >= timedelta(
                minutes=SESSION_REFRESH_MINUTES
            ):
                last_refresh = now_kst()
                try:
                    await flow.goto_booking()
                    if not await flow.is_logged_in():
                        log.info("세션이 풀려 다시 로그인합니다.")
                        await flow.login()
                except Exception as exc:
                    log.warning("세션 갱신 실패(계속 진행): %s", exc)

            if on_round is not None:
                # 밖에서 '살아 있음'을 확인할 수 있게 매 회차 상태를 넘긴다.
                on_round(
                    {
                        "round": self.round_no,
                        "slots": len(snapshot.slots),
                        "available": snapshot.available_count,
                        "opened_total": self.store.counts()["opened"],
                        "source": source.name,
                    }
                )

            wait = max(3.0, interval + random.uniform(-track.jitter_seconds, track.jitter_seconds))
            if track.dashboard:
                draw(
                    render_board(
                        snapshot,
                        set(self.targets.dates),
                        self._recent,
                        self.store.poll_health(),
                        wait,
                        self.round_no,
                        self.store.counts()["opened"],
                        color=color,
                    )
                )
            await asyncio.sleep(wait)

        self.notifier.send(
            "⏹️ 재고 추적 종료",
            f"{self.round_no}회 확인, 누적 취소 감지 {self.store.counts()['opened']}건.",
        )
        return None

    async def grab(self, flow: BookingFlow, bookable: list[Change]) -> BookingResult | None:
        """감지된 취소표를 실제로 잡으러 간다.

        성공하면 즉시 그 결과를, 전부 실패하면 마지막 실패 결과를 돌려준다.
        None 은 '쿨다운 때문에 아무것도 시도하지 않았다'는 뜻이다.
        """
        last: BookingResult | None = None
        for change in bookable:
            key = change.slot.key
            if not self.can_attempt(key):
                log.info("%s 는 쿨다운 중이라 건너뜁니다.", change.slot)
                continue
            self.mark_attempt(key)
            log.info("취소표 확보 시도: %s", change.slot)
            try:
                await flow.goto_booking()
                result = await flow.attempt([date.fromisoformat(change.slot.stay_date)])
            except Exception as exc:
                log.warning("확보 시도 중 오류: %s", exc)
                self.store.record_attempt(
                    change.stay_date, change.cabin, "error", str(exc), change.slot.zone
                )
                last = BookingResult(
                    ok=False,
                    stage="failed",
                    message=f"취소표를 잡으러 갔지만 오류가 났습니다: {exc}",
                    stay_date=change.stay_date,
                    cabin=change.cabin,
                    zone=change.slot.zone,
                )
                continue
            self.store.record_attempt(
                change.stay_date, change.cabin, result.stage, result.message, change.slot.zone
            )
            last = result
            if result.ok:
                return result
            log.warning("확보 실패(%s): %s", result.stage, result.message)
        return last


async def run_track(
    cfg: Config,
    smap: SelectorMap,
    notifier: Notifier,
    on_round: "Callable[[dict[str, object]], None] | None" = None,
) -> BookingResult | None:
    with TrackerStore(cfg.run.track.db_path) as store:
        tracker = Tracker(cfg, smap, notifier, store)
        async with BrowserSession(cfg) as session:
            return await tracker.run(session, on_round=on_round)
