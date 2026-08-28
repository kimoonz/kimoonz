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
from datetime import date, timedelta
from pathlib import Path

from .browser import BrowserSession
from .config import Config
from .credentials import resolve as resolve_credentials
from .errors import LoginFailed, SelectorNotFound
from .zones import ZonePreference, extract_zone
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
    zone: str = ""
    nights: int = 1

    def __str__(self) -> str:
        price = f" / {self.price}" if self.price else ""
        zone = f"[{self.zone}] " if self.zone else ""
        return f"{self.stay_date} {self.nights}박 {zone}{self.name}{price}"


@dataclass(slots=True)
class BookingResult:
    ok: bool
    stage: str  # payment_ready | dry_run | no_availability | failed
    message: str
    stay_date: str | None = None
    cabin: str | None = None
    zone: str | None = None
    nights: int | None = None
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
        self.zones = ZonePreference.build(
            cfg.target.zones, cfg.target.exclude_zones, cfg.target.zone_strict
        )

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
        creds = resolve_credentials(
            self.cfg.account.login_id,
            self.cfg.account.password,
            self.cfg.run.storage_state.parent,
        )
        if not creds.usable:
            # 자동 로그인에 쓸 정보가 없다. 무엇을 해야 하는지 바로 말한다.
            raise LoginFailed(
                "로그인 세션이 만료됐는데 자동 로그인에 쓸 아이디/비밀번호가 없습니다.\n"
                "→ `python -m paradogo login --save` 로 한 번 저장해 두면, "
                "다음부터는 알아서 다시 로그인합니다.\n"
                "→ 캡차·본인확인이 걸린 사이트라면 `python -m paradogo login --manual` 로 "
                "직접 로그인해 주세요."
            )
        log.info("로그인 페이지로 이동: %s (%s)", self.cfg.site.login_url, creds.masked())
        await self.page.goto(self.cfg.site.login_url, wait_until="domcontentloaded")
        await self.dismiss_popups()

        await fill(self.page, self.smap, "login.id_input", creds.login_id)
        await fill(self.page, self.smap, "login.pw_input", creds.password)
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

    def _date_placeholders(self, stay_date: date) -> dict[str, object]:
        return {
            "date": stay_date.isoformat(),
            "year": stay_date.year,
            "month": f"{stay_date.month:02d}",
            "day": f"{stay_date.day:02d}",
            "day_int": stay_date.day,
            "compact": stay_date.strftime("%Y%m%d"),
        }

    async def _click_day(self, key: str, stay_date: date, check_soldout: bool) -> bool:
        """달력에서 해당 날짜 칸을 누른다. 없거나 매진이면 False."""
        fmt = self._date_placeholders(stay_date)
        cell = await first_visible(
            self.page, self.smap, key, timeout_ms=5000, required=False, **fmt
        )
        if cell is None:
            log.info("%s 날짜 칸을 찾지 못했습니다(아직 오픈 전일 수 있음).", stay_date)
            return False
        if check_soldout and await is_present(
            cell, self.smap, "booking.day_soldout_marker", timeout_ms=400
        ):
            log.info("%s 은(는) 매진 표시가 있습니다.", stay_date)
            return False
        try:
            await cell.click()
        except Exception as exc:
            log.info("%s 날짜 클릭 실패(비활성 추정): %s", stay_date, exc)
            return False
        await self.page.wait_for_timeout(300)
        return True

    async def _apply_nights(self, check_in: date, nights: int) -> bool:
        """박수를 지정한다. 사이트마다 방식이 달라 세 가지를 순서대로 시도한다.

        1) 박수 선택 박스(select)  2) '{nights}박' 버튼  3) 체크아웃 날짜 칸 클릭
        """
        selector = await first_visible(
            self.page, self.smap, "booking.nights_select", timeout_ms=1500, required=False
        )
        if selector is not None:
            for attempt_fn in (
                lambda: selector.select_option(label=f"{nights}박"),
                lambda: selector.select_option(value=str(nights)),
                lambda: selector.select_option(str(nights)),
            ):
                try:
                    await attempt_fn()
                    log.info("박수 선택 박스에서 %d박을 골랐습니다.", nights)
                    return True
                except Exception:
                    continue
            log.info("박수 선택 박스는 있으나 %d박 옵션을 고르지 못했습니다.", nights)

        button = await first_visible(
            self.page, self.smap, "booking.nights_button", timeout_ms=1200,
            required=False, nights=nights,
        )
        if button is not None:
            await button.click()
            await self.page.wait_for_timeout(300)
            log.info("%d박 버튼을 눌렀습니다.", nights)
            return True

        # 대부분의 국내 예약 달력은 체크인·체크아웃 두 날짜를 찍는 방식이다.
        check_out = check_in + timedelta(days=nights)
        key = (
            "booking.checkout_cell"
            if self.smap.has("booking.checkout_cell")
            else "booking.day_cell"
        )
        if await self._click_day(key, check_out, check_soldout=False):
            log.info("체크아웃 %s 를 눌러 %d박으로 맞췄습니다.", check_out, nights)
            return True

        if nights == 1:
            # 1박이 기본값인 사이트가 많다. 별도 조작이 없어도 정상일 수 있다.
            log.debug("박수 조작 없이 1박 기본값으로 진행합니다.")
            return True
        log.warning("%d박을 지정할 방법을 찾지 못했습니다(체크아웃 칸/선택 박스 없음).", nights)
        return False

    async def select_stay(self, check_in: date, nights: int = 1) -> bool:
        """체크인 날짜와 박수를 골라 캐빈 목록이 뜨는 상태까지 만든다."""
        if not await self._click_day("booking.day_cell", check_in, check_soldout=True):
            return False
        if not await self._apply_nights(check_in, nights):
            return False

        search = await first_visible(
            self.page, self.smap, "booking.search_button", timeout_ms=1500, required=False
        )
        if search is not None:
            await search.click()
        await self.page.wait_for_timeout(600)
        return True

    async def list_offers(self, stay_date: date, nights: int = 1) -> list[Offer]:
        """현재 화면에서 예약 가능한 캐빈 목록을 읽는다."""
        cards = await first_nonempty(self.page, self.smap, "booking.room_card", required=False)
        if cards is None:
            return []

        patterns = self.cfg.target.zone_patterns
        offers: list[Offer] = []
        for i in range(await cards.count()):
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
            zone_loc = await first_visible(
                card, self.smap, "booking.room_zone", timeout_ms=300, required=False
            )
            name = (await name_loc.inner_text()).strip() if name_loc else f"캐빈 #{i + 1}"
            price = (await price_loc.inner_text()).strip() if price_loc else ""
            name = " ".join(name.split())
            # 구역 전용 요소가 있으면 그쪽이 정확하다. 없으면 캐빈 이름에서 뽑는다.
            if zone_loc is not None:
                zone = extract_zone(" ".join((await zone_loc.inner_text()).split()), patterns)
            else:
                zone = extract_zone(name, patterns)
            offers.append(
                Offer(
                    index=i,
                    name=name,
                    price=" ".join(price.split()),
                    stay_date=stay_date.isoformat(),
                    zone=zone,
                    nights=nights,
                )
            )
        return offers

    def pick_offer(self, offers: list[Offer]) -> Offer | None:
        """구역 우선순위 → 캐빈 우선순위 순으로 고른다.

        구역을 못 읽은 캐빈은 zone_strict 가 켜져 있고 원하는 구역이 지정돼 있으면
        고르지 않는다. 엉뚱한 구역을 잡아 결제 화면까지 가는 것보다 낫다.
        """
        if not offers:
            return None

        allowed = [o for o in offers if self.zones.selectable(o.zone)]
        if not allowed:
            log.info(
                "구역 조건(%s%s)에 맞는 캐빈이 없습니다. 화면에 있던 것: %s",
                "원함 " + ",".join(self.zones.wanted) if self.zones.wanted else "",
                " / 제외 " + ",".join(sorted(self.zones.excluded)) if self.zones.excluded else "",
                ", ".join(f"{o.name}({o.zone or '구역미상'})" for o in offers),
            )
            return None

        wanted = [k.strip() for k in self.cfg.target.cabin_types if k.strip()]

        def cabin_rank(offer: Offer) -> int:
            for index, keyword in enumerate(wanted):
                if keyword in offer.name:
                    return index
            return len(wanted)

        if wanted and all(cabin_rank(o) == len(wanted) for o in allowed):
            log.info(
                "원하는 캐빈(%s)은 없고 %s 만 남아 있습니다.",
                ", ".join(wanted),
                ", ".join(o.name for o in allowed),
            )
            return None

        return min(
            allowed, key=lambda o: (self.zones.rank(o.zone), cabin_rank(o), o.index)
        )

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
        common = dict(
            stay_date=offer.stay_date,
            cabin=offer.name,
            zone=offer.zone,
            nights=offer.nights,
            url=self.page.url,
            screenshot=shot,
        )
        if reached:
            return BookingResult(
                ok=True,
                stage="payment_ready",
                message=(
                    "결제 페이지까지 진입했습니다. 브라우저 창에서 직접 결제를 완료하세요.\n"
                    "※ 결제 전까지는 예약이 확정되지 않습니다."
                ),
                **common,
            )
        return BookingResult(
            ok=False,
            stage="failed",
            message=(
                "예약자 정보까지는 넘어갔지만 결제 페이지 표식(payment.marker)을 찾지 못했습니다. "
                "브라우저 창을 직접 확인하세요."
            ),
            **common,
        )

    # ------------------------------------------------------------ 한 번의 시도

    async def attempt(
        self,
        stay_dates: list[date] | None = None,
        nights_options: list[int] | None = None,
    ) -> BookingResult:
        """설정된 날짜 × 박수를 우선순위대로 훑어 첫 성공에서 멈춘다."""
        dates = stay_dates or self.cfg.target.check_in_dates
        nights_list = nights_options or self.cfg.target.nights_options or [1]
        seen: list[str] = []

        for stay_date in dates:
            for nights in nights_list:
                await self.ensure_month(stay_date.year, stay_date.month)
                if not await self.select_stay(stay_date, nights):
                    continue
                offers = await self.list_offers(stay_date, nights)
                if not offers:
                    log.info("%s %d박: 예약 가능한 캐빈이 없습니다.", stay_date, nights)
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
                            "[모의 실행] 빈자리를 찾았지만 dry_run=true 라 예약을 진행하지 "
                            "않았습니다.\n발견 목록:\n- " + "\n- ".join(seen)
                        ),
                        stay_date=chosen.stay_date,
                        cabin=chosen.name,
                        zone=chosen.zone,
                        nights=chosen.nights,
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
                        zone=chosen.zone,
                        nights=chosen.nights,
                        url=self.page.url,
                        screenshot=shot,
                    )

        return BookingResult(
            ok=False,
            stage="no_availability",
            message="설정한 날짜·박수 중 예약 가능한 캐빈이 없습니다."
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
