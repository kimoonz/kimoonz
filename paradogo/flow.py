"""예약 플로우.

로그인 → 캐빈파크 예약 페이지 → 월 이동 → 날짜 선택 → 캐빈 선택 → 예약자 정보 →
**결제 페이지 진입까지**. 결제 버튼은 절대 누르지 않는다. 결제는 사람이 한다.

각 단계는 selectors.yaml 의 논리 키에만 의존하므로, 사이트 개편 시 코드가 아니라
YAML만 고치면 된다.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .browser import BrowserSession
from .config import Config
from .errors import LoginFailed, SelectorNotFound
from .selectors import (
    SelectorMap,
    click,
    fill,
    first_nonempty,
    first_visible,
    is_present,
)

log = logging.getLogger(__name__)

_MONTH_LABEL_RE = re.compile(r"(?:(\d{4})\s*[.\-년/]?\s*)?(\d{1,2})\s*월?")


@dataclass(slots=True)
class Offer:
    """예약 가능한 캐빈 한 건."""

    index: int
    name: str
    price: str = ""
    stay_date: str = ""

    def __str__(self) -> str:
        price = f" / {self.price}" if self.price else ""
        return f"{self.stay_date} {self.name}{price}"


@dataclass(slots=True)
class BookingResult:
    ok: bool
    stage: str  # payment_ready | dry_run | no_availability | failed
    message: str
    stay_date: str | None = None
    cabin: str | None = None
    url: str | None = None
    screenshot: Path | None = None

    @property
    def reached_payment(self) -> bool:
        return self.stage == "payment_ready"


def parse_month_label(text: str) -> tuple[int | None, int]:
    """'2026년 10월', '2026.10', '10월' 등에서 (연, 월)을 뽑는다."""
    match = _MONTH_LABEL_RE.search(text.strip())
    if not match:
        raise ValueError(f"달력 라벨에서 월을 못 읽었습니다: {text!r}")
    year = int(match.group(1)) if match.group(1) else None
    return year, int(match.group(2))


def month_distance(current: tuple[int | None, int], target: tuple[int, int]) -> int:
    """현재 달에서 목표 달까지 '다음달' 버튼을 몇 번 눌러야 하는지."""
    cur_year, cur_month = current
    tgt_year, tgt_month = target
    if cur_year is None:
        # 연도를 못 읽으면 월만 보고 0~11 범위로 추정한다.
        return (tgt_month - cur_month) % 12
    return (tgt_year - cur_year) * 12 + (tgt_month - cur_month)


class BookingFlow:
    def __init__(self, session: BrowserSession, smap: SelectorMap, cfg: Config) -> None:
        self.session = session
        self.smap = smap
        self.cfg = cfg

    @property
    def page(self):
        assert self.session.page is not None, "브라우저 세션이 아직 열리지 않았습니다."
        return self.session.page

    # ---------------------------------------------------------------- 공통

    async def dismiss_popups(self) -> int:
        """한국 예약 사이트 특유의 레이어 팝업/오늘 하루 안 보기 배너를 닫는다."""
        closed = 0
        for _ in range(4):
            closer = await first_visible(
                self.page, self.smap, "common.popup_close", timeout_ms=700, required=False
            )
            if closer is None:
                break
            try:
                await closer.click()
                closed += 1
                await self.page.wait_for_timeout(150)
            except Exception:
                break
        if closed:
            log.debug("팝업 %d개를 닫았습니다.", closed)
        return closed

    # ---------------------------------------------------------------- 로그인

    async def is_logged_in(self) -> bool:
        return await is_present(self.page, self.smap, "login.success_marker", timeout_ms=2500)

    async def login(self) -> None:
        """저장된 세션이 없거나 만료됐을 때 실제로 로그인한다."""
        log.info("로그인 페이지로 이동: %s", self.cfg.site.login_url)
        await self.page.goto(self.cfg.site.login_url, wait_until="domcontentloaded")
        await self.dismiss_popups()

        await fill(self.page, self.smap, "login.id_input", self.cfg.account.login_id)
        await fill(self.page, self.smap, "login.pw_input", self.cfg.account.password)
        await click(self.page, self.smap, "login.submit")

        try:
            await self.page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        await self.dismiss_popups()

        if not await self.is_logged_in():
            shot = await self.session.screenshot("login-failed")
            raise LoginFailed(
                "로그인 후에도 login.success_marker 를 찾지 못했습니다. "
                "아이디/비밀번호, 또는 캡차·본인확인 단계 여부를 확인하세요."
                + (f" 스크린샷: {shot}" if shot else "")
            )
        log.info("로그인 완료.")

    async def login_manually(self, timeout_minutes: int = 10) -> None:
        """브라우저를 띄워 두고 사람이 직접 로그인하기를 기다린다.

        캡차·본인확인·간편로그인이 걸린 사이트에서는 자동 입력이 통하지 않는다.
        그럴 때는 한 번만 손으로 로그인하고 세션을 저장해 두면, 이후 실행은
        그 세션을 그대로 재사용한다.
        """
        await self.page.goto(self.cfg.site.login_url, wait_until="domcontentloaded")
        await self.dismiss_popups()
        print("\n" + "=" * 68)
        print("열린 브라우저 창에서 직접 로그인하세요.")
        print("(캡차·본인확인·간편로그인 모두 직접 하시면 됩니다)")
        print("로그인이 끝나면 이 터미널에서 Enter 를 누르세요.")
        print("=" * 68)
        await asyncio.get_event_loop().run_in_executor(None, input)

        await self.dismiss_popups()
        if not await self.is_logged_in():
            shot = await self.session.screenshot("manual-login-check")
            raise LoginFailed(
                "로그인 완료 표식(login.success_marker)을 찾지 못했습니다. "
                "정말 로그인됐다면 selectors.yaml 의 login.success_marker 를 "
                "'로그인 상태에서만 보이는 요소'로 고쳐 주세요."
                + (f" 스크린샷: {shot}" if shot else "")
            )
        log.info("수동 로그인 확인 완료.")

    async def ensure_logged_in(self) -> None:
        """이미 로그인 상태면 건너뛴다(오픈 시각에 몇 초를 아끼는 지점).

        로그인 여부는 '현재 열린 페이지'의 표식으로 판단하므로, 빈 탭이면 먼저
        예약 페이지를 띄운 뒤 확인한다. 이 단계를 빠뜨리면 저장된 세션이 있어도
        매번 다시 로그인하게 된다.
        """
        if self.page.url in ("", "about:blank"):
            await self.page.goto(self.cfg.site.booking_url, wait_until="domcontentloaded")
        await self.dismiss_popups()
        if await self.is_logged_in():
            log.info("기존 세션으로 이미 로그인된 상태입니다.")
            return
        await self.login()
        await self.session.save_state()

    # ------------------------------------------------------------ 예약 페이지

    async def goto_booking(self) -> None:
        log.info("예약 페이지로 이동: %s", self.cfg.site.booking_url)
        await self.page.goto(self.cfg.site.booking_url, wait_until="domcontentloaded")
        await self.dismiss_popups()

    async def ensure_month(self, year: int, month: int, max_clicks: int = 14) -> None:
        """달력을 목표 연·월로 옮긴다."""
        label = await first_visible(
            self.page, self.smap, "booking.month_label", timeout_ms=6000, required=False
        )
        if label is None:
            log.debug("booking.month_label 이 없어 달 이동을 건너뜁니다.")
            return

        for _ in range(max_clicks):
            text = (await label.inner_text()).strip()
            try:
                current = parse_month_label(text)
            except ValueError:
                log.warning("달력 라벨 파싱 실패(%r). 달 이동을 건너뜁니다.", text)
                return
            distance = month_distance(current, (year, month))
            if distance == 0:
                log.info("달력이 %d년 %d월에 맞춰졌습니다.", year, month)
                return
            key = "booking.next_month" if distance > 0 else "booking.prev_month"
            button = await first_visible(self.page, self.smap, key, timeout_ms=3000, required=False)
            if button is None:
                log.warning("'%s' 버튼이 없어 더 이동할 수 없습니다(현재 %r).", key, text)
                return
            await button.click()
            await self.page.wait_for_timeout(350)

        log.warning("달 이동 상한(%d회)에 도달했습니다.", max_clicks)

    async def select_date(self, stay_date: date) -> bool:
        """해당 날짜 칸을 누른다. 매진/비활성이면 False."""
        fmt = {
            "date": stay_date.isoformat(),
            "year": stay_date.year,
            "month": f"{stay_date.month:02d}",
            "day": f"{stay_date.day:02d}",
            "day_int": stay_date.day,
            "compact": stay_date.strftime("%Y%m%d"),
        }
        cell = await first_visible(
            self.page, self.smap, "booking.day_cell", timeout_ms=5000, required=False, **fmt
        )
        if cell is None:
            log.info("%s 날짜 칸을 찾지 못했습니다(아직 오픈 전일 수 있음).", stay_date)
            return False

        if await is_present(cell, self.smap, "booking.day_soldout_marker", timeout_ms=400):
            log.info("%s 은(는) 매진 표시가 있습니다.", stay_date)
            return False

        try:
            await cell.click()
        except Exception as exc:
            log.info("%s 날짜 클릭 실패(비활성 추정): %s", stay_date, exc)
            return False

        await self.page.wait_for_timeout(400)
        search = await first_visible(
            self.page, self.smap, "booking.search_button", timeout_ms=1500, required=False
        )
        if search is not None:
            await search.click()
            await self.page.wait_for_timeout(600)
        return True

    async def list_offers(self, stay_date: date) -> list[Offer]:
        """현재 화면에서 예약 가능한 캐빈 목록을 읽는다."""
        cards = await first_nonempty(self.page, self.smap, "booking.room_card", required=False)
        if cards is None:
            return []

        offers: list[Offer] = []
        total = await cards.count()
        for i in range(total):
            card = cards.nth(i)
            if await is_present(card, self.smap, "booking.room_soldout_marker", timeout_ms=250):
                continue
            # 예약 버튼이 없으면 예약 가능한 카드가 아니다.
            if not await is_present(card, self.smap, "booking.room_reserve_button", timeout_ms=250):
                continue
            name_loc = await first_visible(
                card, self.smap, "booking.room_name", timeout_ms=500, required=False
            )
            price_loc = await first_visible(
                card, self.smap, "booking.room_price", timeout_ms=300, required=False
            )
            name = (await name_loc.inner_text()).strip() if name_loc else f"캐빈 #{i + 1}"
            price = (await price_loc.inner_text()).strip() if price_loc else ""
            offers.append(
                Offer(
                    index=i,
                    name=" ".join(name.split()),
                    price=" ".join(price.split()),
                    stay_date=stay_date.isoformat(),
                )
            )
        return offers

    def pick_offer(self, offers: list[Offer]) -> Offer | None:
        """설정한 캐빈 우선순위대로 고른다. 목록이 비어 있으면 아무거나 첫 번째."""
        if not offers:
            return None
        wanted = self.cfg.target.cabin_types
        if not wanted:
            return offers[0]
        for keyword in wanted:
            for offer in offers:
                if keyword.strip() and keyword.strip() in offer.name:
                    return offer
        log.info(
            "원하는 캐빈(%s)은 없고 %s 만 남아 있습니다.",
            ", ".join(wanted),
            ", ".join(o.name for o in offers),
        )
        return None

    # ------------------------------------------------------------ 예약 진행

    async def reserve(self, offer: Offer) -> BookingResult:
        """선택한 캐빈으로 결제 직전까지 진행한다."""
        cards = await first_nonempty(self.page, self.smap, "booking.room_card")
        card = cards.nth(offer.index)
        await click(card, self.smap, "booking.room_reserve_button")
        try:
            await self.page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        await self.dismiss_popups()

        booker = self.cfg.account.booker
        await fill(self.page, self.smap, "guest.name", booker.name)
        await fill(self.page, self.smap, "guest.phone", booker.phone)
        await fill(self.page, self.smap, "guest.email", booker.email)
        await fill(self.page, self.smap, "guest.adults", str(self.cfg.target.adults))
        if self.cfg.target.children:
            await fill(self.page, self.smap, "guest.children", str(self.cfg.target.children))

        agree = await first_visible(
            self.page, self.smap, "guest.agree_all", timeout_ms=2500, required=False
        )
        if agree is not None:
            try:
                await agree.check()
            except Exception:
                await agree.click()

        to_payment = await first_visible(
            self.page, self.smap, "guest.to_payment", timeout_ms=4000, required=False
        )
        if to_payment is not None:
            await to_payment.click()
            try:
                await self.page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            await self.dismiss_popups()

        reached = await is_present(self.page, self.smap, "payment.marker", timeout_ms=8000)
        shot = await self.session.screenshot("payment-page" if reached else "before-payment")
        if reached:
            return BookingResult(
                ok=True,
                stage="payment_ready",
                message=(
                    "결제 페이지까지 진입했습니다. 브라우저 창에서 직접 결제를 완료하세요.\n"
                    "※ 결제 전까지는 예약이 확정되지 않습니다."
                ),
                stay_date=offer.stay_date,
                cabin=offer.name,
                url=self.page.url,
                screenshot=shot,
            )
        return BookingResult(
            ok=False,
            stage="failed",
            message=(
                "예약자 정보까지는 넘어갔지만 결제 페이지 표식(payment.marker)을 찾지 못했습니다. "
                "브라우저 창을 직접 확인하세요."
            ),
            stay_date=offer.stay_date,
            cabin=offer.name,
            url=self.page.url,
            screenshot=shot,
        )

    # ------------------------------------------------------------ 한 번의 시도

    async def attempt(self, stay_dates: list[date] | None = None) -> BookingResult:
        """설정된 날짜들을 순서대로 훑어 첫 성공에서 멈춘다."""
        dates = stay_dates or self.cfg.target.check_in_dates
        seen: list[str] = []

        for stay_date in dates:
            await self.ensure_month(stay_date.year, stay_date.month)
            if not await self.select_date(stay_date):
                continue
            offers = await self.list_offers(stay_date)
            if not offers:
                log.info("%s: 예약 가능한 캐빈이 없습니다.", stay_date)
                continue
            seen.extend(str(o) for o in offers)
            chosen = self.pick_offer(offers)
            if chosen is None:
                continue

            log.info("빈자리 발견: %s", chosen)
            if self.cfg.run.dry_run:
                shot = await self.session.screenshot("dry-run-found")
                return BookingResult(
                    ok=True,
                    stage="dry_run",
                    message=(
                        f"[모의 실행] 빈자리를 찾았지만 dry_run=true 라 예약을 진행하지 않았습니다.\n"
                        f"발견 목록:\n- " + "\n- ".join(seen)
                    ),
                    stay_date=chosen.stay_date,
                    cabin=chosen.name,
                    url=self.page.url,
                    screenshot=shot,
                )
            try:
                return await self.reserve(chosen)
            except SelectorNotFound as exc:
                shot = await self.session.screenshot("reserve-selector-missing")
                return BookingResult(
                    ok=False,
                    stage="failed",
                    message=f"예약 진행 중 셀렉터 문제: {exc}",
                    stay_date=chosen.stay_date,
                    cabin=chosen.name,
                    url=self.page.url,
                    screenshot=shot,
                )

        return BookingResult(
            ok=False,
            stage="no_availability",
            message="설정한 날짜 중 예약 가능한 캐빈이 없습니다."
            + (f"\n화면에서 본 항목: {', '.join(seen)}" if seen else ""),
        )

    async def keep_open(self, minutes: int) -> None:
        """결제할 수 있게 브라우저를 열어 둔다."""
        if minutes <= 0:
            return
        log.info("결제를 위해 브라우저를 %d분간 열어 둡니다. (Ctrl+C 로 즉시 종료)", minutes)
        try:
            await asyncio.sleep(minutes * 60)
        except asyncio.CancelledError:
            pass
