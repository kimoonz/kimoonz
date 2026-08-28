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
from .dashboard import supports_color
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
    p_login = sub.add_parser("login", help="로그인해서 세션 파일 저장")
    p_login.add_argument(
        "--manual", action="store_true",
        help="자동 입력 대신 브라우저를 띄워 직접 로그인 (캡차·본인확인이 있으면 이쪽)",
    )

    p_scan = sub.add_parser(
        "scan", help="지금 예약 가능한 날짜를 한 번만 조회 (읽기 전용, 아무것도 클릭하지 않음)"
    )
    p_scan.add_argument("--months", type=int, help="조회할 개월 수")
    sub.add_parser("notify-test", help="알림 채널 테스트 발송")
    sub.add_parser("watch", help="취소표 감시 (단순 반복 확인)")

    p_track = sub.add_parser(
        "track", help="실시간 재고 추적 — 마감→예약가능 전환(취소)을 감지해 즉시 확보"
    )
    p_track.add_argument("--months", type=int, help="추적할 개월 수 (기본: 설정값)")
    p_track.add_argument("--no-dashboard", action="store_true", help="대시보드 없이 로그만")
    p_track.add_argument("--alert-only", action="store_true",
                         help="취소를 감지해도 예약은 하지 않고 알리기만")
    p_track.add_argument("--minutes", type=int, help="이 시간(분) 뒤 종료 (기본: 무제한)")

    p_stats = sub.add_parser("stats", help="추적 이력 통계 — 언제/어느 날짜에 취소가 나왔나")
    p_stats.add_argument("--limit", type=int, default=15, help="표시할 항목 수")

    p_sniff = sub.add_parser("sniff", help="사이트의 재고 조회 API 찾기 (추적 속도 향상)")
    p_sniff.add_argument("--url", help="시작할 URL (기본: site.booking_path)")
    p_sniff.add_argument("--auto", action="store_true",
                         help="Enter 를 기다리지 않고 5초만 엿듣기")

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
    if cfg.api.usable:
        print(f"       재고 조회  : API — {cfg.api.url_template}")
        print(f"                    주기 {cfg.run.track.effective_interval('api'):.0f}초")
    else:
        print("       재고 조회  : 달력 DOM (API 미설정 — `sniff` 로 찾으면 더 빠릅니다)")
        print(f"                    주기 {cfg.run.track.effective_interval('dom'):.0f}초")
    state = cfg.run.storage_state
    print(f"       로그인 세션: {'있음' if state.exists() else '없음 — `login --manual` 먼저'}"
          f" ({state})")
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
    if args.manual and not args.headless:
        cfg.run.headless = False  # 사람이 봐야 로그인할 수 있다

    async def main() -> int:
        async with BrowserSession(cfg, reuse_state=False) as session:
            flow = BookingFlow(session, smap, cfg)
            if args.manual:
                await flow.login_manually()
            else:
                await flow.login()
            path = await session.save_state()
            print(f"세션을 저장했습니다: {path}")
            print("이제 scan / track / snipe 은 이 세션을 재사용합니다.")
        return 0

    return asyncio.run(main())


def cmd_scan(args: argparse.Namespace) -> int:
    from .scan import render_scan, run_scan

    cfg, smap = load_all(args)
    snapshot = asyncio.run(run_scan(cfg, smap, args.months))
    targets = {d.isoformat() for d in cfg.target.check_in_dates}
    print()
    print(render_scan(snapshot, targets, color=supports_color()))
    return 0 if snapshot.slots else 2


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


def cmd_track(args: argparse.Namespace) -> int:
    from .tracker import run_track

    cfg, smap = load_all(args)
    if args.months:
        cfg.run.track.months_ahead = args.months
    if args.no_dashboard:
        cfg.run.track.dashboard = False
    if args.alert_only:
        cfg.run.track.auto_reserve = False
    if args.minutes:
        cfg.run.track.max_duration_minutes = args.minutes

    notifier = Notifier(cfg.notify)
    result = asyncio.run(run_track(cfg, smap, notifier))
    if result is None:
        return 0
    print(result.message)
    return 0 if result.ok else 2


def cmd_sniff(args: argparse.Namespace) -> int:
    from .sniff import run_sniff

    cfg, _ = load_all(args, need_selectors=False)
    if not args.headless:
        cfg.run.headless = False  # 사람이 달력을 눌러 봐야 요청이 나온다
    path = asyncio.run(run_sniff(cfg, args.url, interactive=not args.auto))
    print(f"\napi 블록 초안: {path}")
    print("확인 후 config/config.yaml 의 최상위 `api:` 로 옮기세요.")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from .store import TrackerStore

    cfg, _ = load_all(args, need_selectors=False)
    db = cfg.run.track.db_path
    if not db.exists():
        print(f"추적 DB가 아직 없습니다: {db}\n→ `python -m paradogo track` 를 먼저 돌리세요.")
        return 1

    with TrackerStore(db) as store:
        counts = store.counts()
        health = store.poll_health(200)
        print(f"DB: {db}")
        print(f"현재 추적 중인 칸 : {counts['state']}개")
        print(f"기록된 변화       : {counts['events']}건 (그중 취소 {counts['opened']}건)")
        print(f"폴링              : {counts['polls']}회, 최근 성공률 "
              f"{health['success_rate'] * 100:.0f}%, 평균 {health['avg_ms']:.0f}ms")

        by_hour = store.cancellation_by_hour()
        if by_hour:
            print("\n시간대별 취소 발생")
            peak = max(n for _, n in by_hour)
            for hour, count in by_hour:
                bar = "█" * max(1, round(count / peak * 32))
                print(f"  {hour:02d}시 {bar} {count}")

        by_date = store.cancellation_by_date(args.limit)
        if by_date:
            print("\n날짜별 취소 발생 (많은 순)")
            for stay_date, count in by_date:
                print(f"  {stay_date}  {count}건")

        survival = store.survival_times(args.limit)
        if survival:
            print("\n취소표가 살아 있던 시간 (짧은 순)")
            for stay_date, cabin, seconds in survival:
                print(f"  {stay_date} {cabin}  {seconds:.0f}초")
            fastest = survival[0][2]
            print(f"\n  → 가장 빨리 사라진 게 {fastest:.0f}초. "
                  f"폴링 주기를 그보다 짧게 두어야 잡을 수 있습니다.")

        recent = store.recent_events(args.limit)
        if recent:
            print("\n최근 변화")
            for row in recent:
                print(f"  {row['ts'][:19].replace('T', ' ')}  [{row['kind']}]  "
                      f"{row['stay_date']} {row['cabin']}"
                      + (f"  {row['note']}" if row["note"] else ""))
    return 0


COMMANDS = {
    "doctor": cmd_doctor,
    "next-open": cmd_next_open,
    "login": cmd_login,
    "scan": cmd_scan,
    "notify-test": cmd_notify_test,
    "discover": cmd_discover,
    "watch": cmd_watch,
    "track": cmd_track,
    "stats": cmd_stats,
    "sniff": cmd_sniff,
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
