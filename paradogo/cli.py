"""커맨드라인 진입점."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime

from . import __version__
from .clock import (
    KST,
    humanize,
    next_open_datetime,
    now_kst,
    sync_with_server,
    target_stay_month,
)
from .config import Config
from .errors import ParadogoError
from .notify import Notifier
from .selectors import SelectorMap

log = logging.getLogger("paradogo")

DEFAULT_CONFIG = "config/config.yaml"
DEFAULT_SELECTORS = "config/selectors.yaml"

REQUIRED_SELECTORS = [
    "login.id_input",
    "login.pw_input",
    "login.submit",
    "login.success_marker",
    "booking.day_cell",
    "booking.room_card",
    "booking.room_reserve_button",
    "payment.marker",
]


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m paradogo",
        description="파라다이스 스파 도고 캐빈파크 예약 보조 도구 "
        "(결제 직전까지만 자동화합니다)",
    )
    parser.add_argument("--version", action="version", version=f"paradogo {__version__}")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG, help="설정 YAML 경로")
    parser.add_argument("-s", "--selectors", default=DEFAULT_SELECTORS, help="셀렉터 YAML 경로")
    parser.add_argument("-v", "--verbose", action="store_true", help="디버그 로그")
    parser.add_argument("--headless", action="store_true", help="브라우저 창 없이 실행")
    parser.add_argument("--headful", action="store_true", help="브라우저 창을 띄워 실행")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="빈자리만 확인하고 예약 클릭은 하지 않음")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                        help="실제로 결제 직전까지 진행")
    parser.set_defaults(dry_run=None)

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="설정·셀렉터·알림 점검")
    sub.add_parser("next-open", help="다음 예약 오픈 시각 계산")
    sub.add_parser("login", help="로그인해서 세션 파일 저장")
    sub.add_parser("notify-test", help="알림 채널 테스트 발송")
    sub.add_parser("watch", help="취소표 감시")

    p_discover = sub.add_parser("discover", help="실제 화면에서 셀렉터 후보 수집")
    p_discover.add_argument("--url", help="분석할 URL (기본: site.booking_path)")
    p_discover.add_argument("--interactive", action="store_true",
                            help="브라우저를 띄우고 Enter 를 누를 때까지 대기")

    p_snipe = sub.add_parser("snipe", help="오픈 시각에 맞춰 예약 시도")
    p_snipe.add_argument("--at", help="오픈 시각 직접 지정 (예: 2026-09-01T09:00:00)")
    p_snipe.add_argument("--now", action="store_true", help="기다리지 않고 즉시 1회 시도")

    return parser


def load_all(args: argparse.Namespace, need_selectors: bool = True):
    cfg = Config.load(args.config)
    if args.headless:
        cfg.run.headless = True
    if args.headful:
        cfg.run.headless = False
    if args.dry_run is not None:
        cfg.run.dry_run = args.dry_run
    smap = SelectorMap.load(args.selectors) if need_selectors else SelectorMap()
    return cfg, smap


# ------------------------------------------------------------------ 명령들


def cmd_next_open(args: argparse.Namespace) -> int:
    cfg, _ = load_all(args, need_selectors=False)
    open_cfg = cfg.run.open_time
    clock = sync_with_server(cfg.site.base_url) if open_cfg.sync_clock else None
    now = clock.server_now() if clock else now_kst()
    nxt = next_open_datetime(now, open_cfg.day_of_month, open_cfg.hour, open_cfg.minute)
    stay_year, stay_month = target_stay_month(nxt)
    print(f"현재(서버 기준) : {now.strftime('%Y-%m-%d %H:%M:%S')} KST")
    if clock:
        print(f"시계 보정      : {clock.detail}")
    print(f"다음 오픈      : {nxt.strftime('%Y-%m-%d %H:%M:%S')} KST")
    print(f"남은 시간      : {humanize((nxt - now).total_seconds())}")
    print(f"열리는 투숙 월 : {stay_year}년 {stay_month}월")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    problems: list[str] = []

    try:
        cfg = Config.load(args.config)
    except ParadogoError as exc:
        print(f"[설정] ✗ {exc}")
        return 1
    print(f"[설정] ✓ {args.config}")
    print(f"       사이트     : {cfg.site.base_url}")
    print(f"       로그인 URL : {cfg.site.login_url}")
    print(f"       예약 URL   : {cfg.site.booking_url}")
    print(f"       대상 날짜  : {', '.join(d.isoformat() for d in cfg.target.check_in_dates) or '(없음)'}")
    print(f"       희망 캐빈  : {', '.join(cfg.target.cabin_types) or '전체'}")
    print(f"       모의 실행  : {'예' if cfg.run.dry_run else '아니오 (실제 진행)'}")
    problems += cfg.validate_for_booking()

    try:
        smap = SelectorMap.load(args.selectors)
        missing = smap.missing(REQUIRED_SELECTORS)
        if missing:
            print(f"[셀렉터] ✗ 비어 있는 필수 키 {len(missing)}개")
            for key in missing:
                print(f"          - {key}")
            problems.append("필수 셀렉터가 비어 있습니다. `discover` 로 채우세요.")
        else:
            print(f"[셀렉터] ✓ {args.selectors} (필수 {len(REQUIRED_SELECTORS)}개 모두 있음)")
    except ParadogoError as exc:
        print(f"[셀렉터] ✗ {exc}")
        problems.append("셀렉터 파일을 읽지 못했습니다.")

    notifier = Notifier(cfg.notify)
    if notifier.active:
        print(f"[알림] ✓ 활성 채널: {', '.join(notifier.active)}")
    else:
        print("[알림] ✗ 활성화된 채널 없음")

    try:
        from playwright.async_api import async_playwright  # noqa: F401
        print("[Playwright] ✓ 파이썬 패키지 설치됨")
    except ImportError:
        print("[Playwright] ✗ 미설치 — `pip install -r requirements.txt` 후 "
              "`python -m playwright install chromium`")
        problems.append("Playwright 미설치")

    open_cfg = cfg.run.open_time
    nxt = next_open_datetime(now_kst(), open_cfg.day_of_month, open_cfg.hour, open_cfg.minute)
    print(f"[오픈] 다음 오픈 {nxt.strftime('%Y-%m-%d %H:%M')} "
          f"(남은 시간 {humanize((nxt - now_kst()).total_seconds())})")

    if problems:
        print("\n확인이 필요한 항목:")
        for item in problems:
            print(f"  · {item}")
        return 1
    print("\n모두 정상입니다.")
    return 0


def cmd_notify_test(args: argparse.Namespace) -> int:
    cfg, _ = load_all(args, need_selectors=False)
    notifier = Notifier(cfg.notify)
    if not notifier.active:
        print("활성화된 알림 채널이 없습니다. config.yaml 의 notify 항목을 확인하세요.")
        return 1
    results = notifier.send(
        "🔔 paradogo 알림 테스트",
        f"이 메시지가 보이면 알림 설정이 정상입니다.\n보낸 시각: "
        f"{now_kst().strftime('%Y-%m-%d %H:%M:%S')} KST",
    )
    for name, ok in results.items():
        print(f"  {name}: {'성공' if ok else '실패'}")
    return 0 if all(results.values()) else 1


def cmd_login(args: argparse.Namespace) -> int:
    from .browser import BrowserSession
    from .flow import BookingFlow

    cfg, smap = load_all(args)

    async def main() -> int:
        async with BrowserSession(cfg, reuse_state=False) as session:
            flow = BookingFlow(session, smap, cfg)
            await flow.login()
            path = await session.save_state()
            print(f"세션을 저장했습니다: {path}")
        return 0

    return asyncio.run(main())


def cmd_discover(args: argparse.Namespace) -> int:
    from .discover import run_discover

    cfg, _ = load_all(args, need_selectors=False)
    # 사람이 화면을 보며 이동해야 하므로 명시적으로 --headless 를 준 경우가 아니면 창을 띄운다.
    if not args.headless:
        cfg.run.headless = False
    path = asyncio.run(run_discover(cfg, args.url, args.interactive))
    print(f"\n셀렉터 초안: {path}")
    print("내용을 확인·수정한 뒤 config/selectors.yaml 로 복사하세요.")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from .watcher import run_watch

    cfg, smap = load_all(args)
    notifier = Notifier(cfg.notify)
    result = asyncio.run(run_watch(cfg, smap, notifier))
    print(result.message)
    return 0 if result.ok else 2


def cmd_snipe(args: argparse.Namespace) -> int:
    from .sniper import run_snipe

    cfg, smap = load_all(args)
    notifier = Notifier(cfg.notify)

    open_at: datetime | None = None
    if args.now:
        open_at = now_kst()
        cfg.run.open_time.lead_seconds = 5
    elif args.at:
        try:
            parsed = datetime.fromisoformat(args.at)
        except ValueError:
            print(f"--at 형식이 잘못됐습니다: {args.at} (예: 2026-09-01T09:00:00)")
            return 1
        open_at = parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)

    result = asyncio.run(run_snipe(cfg, smap, notifier, open_at=open_at))
    print(result.message)
    return 0 if result.ok else 2


COMMANDS = {
    "doctor": cmd_doctor,
    "next-open": cmd_next_open,
    "login": cmd_login,
    "notify-test": cmd_notify_test,
    "discover": cmd_discover,
    "watch": cmd_watch,
    "snipe": cmd_snipe,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        return COMMANDS[args.command](args)
    except ParadogoError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        print("\n중단했습니다.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
