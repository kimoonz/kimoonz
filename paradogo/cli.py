"""커맨드라인 진입점."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta

from . import __version__
from .clock import (
    KST,
    humanize,
    next_open_datetime,
    now_kst,
    open_datetime_for_stay,
    sync_with_server,
    target_stay_month,
)
from urllib.parse import urlparse

from pathlib import Path

from .config import Config, SiteConfig
from .dashboard import supports_color
from .errors import ConfigError, ParadogoError
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
    parser.add_argument("--date", action="append", metavar="YYYY-MM-DD",
                        help="대상 날짜를 설정 대신 직접 지정 (여러 번 쓸 수 있음)")
    parser.add_argument("--nights", metavar="N[,N…]",
                        help="박수 우선순위 (예: --nights 1 또는 --nights 2,1)")
    parser.add_argument("--zones", metavar="A,B…", help="희망 구역 우선순위")
    parser.add_argument("--exclude-zones", dest="exclude_zones", metavar="A,B…",
                        help="제외할 구역")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("gui", help="★ 창으로 쓰기 — 버튼과 로그가 있는 화면")
    sub.add_parser(
        "start", help="터미널에서 설정부터 감시 시작까지 한 번에"
    )
    sub.add_parser("doctor", help="설정·셀렉터·알림 점검")
    sub.add_parser("next-open", help="다음 예약 오픈 시각 계산")
    p_login = sub.add_parser("login", help="로그인해서 세션 파일 저장")
    p_login.add_argument(
        "--manual", action="store_true",
        help="자동 입력 대신 브라우저를 띄워 직접 로그인 (캡차·본인확인이 있으면 이쪽)",
    )
    p_login.add_argument(
        "--save", action="store_true",
        help="아이디·비밀번호를 저장해 두고 앞으로 자동 로그인 (OS 키체인 우선)",
    )
    p_login.add_argument(
        "--forget", action="store_true", help="저장해 둔 로그인 정보를 지웁니다",
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
    p_track.add_argument("--forever", action="store_true",
                         help="계속 켜두기 — 멈추면 알아서 다시 띄우고 상태를 남깁니다")
    p_track.add_argument("--stop-on-success", dest="stop_on_success", action="store_true",
                         help="--forever 중 결제 화면까지 가면 감시를 멈춤 (기본: 계속)")

    sub.add_parser("status", help="감시가 지금 돌고 있는지 확인")

    p_service = sub.add_parser(
        "service", help="PC 를 켜면 감시가 자동으로 뜨도록 등록"
    )
    p_service.add_argument("--install", action="store_true",
                           help="등록 파일을 실제로 만듭니다 (기본: 내용만 보여줌)")
    p_service.add_argument("--os", dest="os_name",
                           choices=["linux", "macos", "windows"],
                           help="직접 지정 (기본: 지금 OS)")

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
    p_snipe.add_argument("--force", action="store_true",
                         help="대상 날짜의 오픈이 이미 지났어도 강행")

    return parser


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.replace(" ", ",").split(",") if part.strip()]


def apply_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    """명령줄 인자로 설정을 덮어쓴다.

    doctor 를 포함한 모든 명령이 같은 오버라이드를 보도록 한곳에 모아 둔다.
    (doctor 만 따로 설정을 읽으면 `--date` 를 준 것과 다른 값을 점검하게 된다.)
    """
    if args.headless:
        cfg.run.headless = True
    if args.headful:
        cfg.run.headless = False
    if args.dry_run is not None:
        cfg.run.dry_run = args.dry_run

    # 명령줄 오버라이드 — YAML 을 고치지 않고 바로 다른 날짜를 노릴 수 있게.
    if args.date:
        try:
            cfg.target.check_in_dates = sorted({date.fromisoformat(d) for d in args.date})
        except ValueError as exc:
            raise ConfigError(f"--date 형식이 잘못됐습니다(YYYY-MM-DD): {exc}") from exc
    if args.nights:
        try:
            nights = [int(n) for n in _split(args.nights)]
        except ValueError as exc:
            raise ConfigError(f"--nights 는 숫자여야 합니다: {args.nights}") from exc
        if any(n < 1 for n in nights):
            raise ConfigError("--nights 는 1 이상이어야 합니다.")
        cfg.target.nights_options = list(dict.fromkeys(nights)) or [1]
    if args.zones:
        cfg.target.zones = _split(args.zones)
    if args.exclude_zones:
        cfg.target.exclude_zones = _split(args.exclude_zones)
    return cfg


def load_all(args: argparse.Namespace, need_selectors: bool = True):
    cfg = apply_overrides(Config.load(args.config), args)
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


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("창 화면을 쓰려면 tkinter 가 필요한데 설치돼 있지 않습니다.")
        print("  Windows/macOS : python.org 설치본에는 기본 포함입니다.")
        print("                  다시 설치하거나 python.org 버전을 쓰세요.")
        print("  Ubuntu/Debian : sudo apt install python3-tk")
        print("\n창 없이 쓰시려면: python -m paradogo start")
        return 1

    from .gui import run_gui

    return run_gui(Path(args.config), Path(args.selectors))


def cmd_start(args: argparse.Namespace) -> int:
    """처음 쓰는 사람이 이 하나만 알면 되도록."""
    from .scan import render_scan, run_scan
    from .tracker import run_track
    from .wizard import ask, ask_yes, run_wizard

    config_path = Path(args.config)
    selectors_path = Path(args.selectors)

    print("=" * 66)
    print(" 파라다이스 도고 캐빈 예약 도우미 — 처음 설정")
    print("=" * 66)
    print("브라우저를 띄워 드릴 테니 안내대로 클릭만 하시면 됩니다.")
    print("그 사이에 필요한 설정을 알아서 만들어 둡니다. (2~3분)")
    print()
    print("※ 결제는 자동화하지 않습니다. 취소표를 잡으면 결제 화면까지 열어 드리고,")
    print("  결제 버튼은 직접 누르셔야 합니다.")

    # 무엇을 잡을지 — 인자로 준 게 있으면 묻지 않는다.
    if args.date:
        dates = sorted({date.fromisoformat(d) for d in args.date})
    else:
        raw = ask("\n체크인 날짜 (YYYY-MM-DD, 여러 개면 쉼표로)", "")
        if not raw:
            print("날짜를 입력해야 합니다.")
            return 1
        try:
            dates = sorted({date.fromisoformat(d) for d in _split(raw)})
        except ValueError as exc:
            print(f"날짜 형식이 잘못됐습니다(YYYY-MM-DD): {exc}")
            return 1

    if args.nights:
        nights = [int(n) for n in _split(args.nights)]
    else:
        raw = ask("몇 박? (2박 우선하고 안되면 1박이면 '2,1')", "1")
        try:
            nights = [int(n) for n in _split(raw)]
        except ValueError:
            print(f"박수는 숫자여야 합니다: {raw}")
            return 1
    nights = [n for n in dict.fromkeys(nights) if n >= 1] or [1]

    zones = _split(args.zones) if args.zones else _split(
        ask("희망 구역 (A~H, 우선순위 순. 없으면 그냥 Enter)", "")
    )
    exclude = _split(args.exclude_zones) if args.exclude_zones else _split(
        ask("피하고 싶은 구역 (없으면 그냥 Enter)", "")
    )

    checkout = [d + timedelta(days=nights[0]) for d in dates]
    print(f"\n→ {', '.join(d.isoformat() for d in dates)} 체크인, "
          f"{nights[0]}박 (체크아웃 {checkout[0].isoformat()})")
    if zones:
        print(f"→ 구역 {', '.join(zones)} 우선"
              + (f" / {', '.join(exclude)} 제외" if exclude else ""))

    # 오픈이 지났는지 미리 알려준다 — 어떤 방식으로 잡을지가 여기서 갈린다.
    base = Config.load(config_path) if config_path.exists() else Config.from_dict(
        {"account": {}, "target": {"check_in_dates": [d.isoformat() for d in dates]}}
    )
    open_cfg = base.run.open_time
    now = now_kst()
    not_open_yet = [
        d for d in dates
        if open_datetime_for_stay(d, open_cfg.day_of_month, open_cfg.hour, open_cfg.minute) > now
    ]
    if not_open_yet:
        print("\n이 날짜들은 아직 예약이 열리지 않았습니다 → 오픈 시각에 잡습니다(오픈런).")
    else:
        print("\n이 날짜는 예약 오픈이 이미 지났습니다 → 취소가 나오기를 기다려 잡습니다.")

    base.target.check_in_dates = dates
    # 사람이 직접 클릭해야 하므로 창을 띄운다. 다만 --headless 를 명시했다면 그 뜻을 따른다.
    base.run.headless = bool(args.headless)
    try:
        asyncio.run(run_wizard(base, config_path, selectors_path, dates, nights, zones, exclude))
    except ParadogoError as exc:
        print(f"\n설정 중 문제가 생겼습니다: {exc}")
        return 1

    # 만들어진 설정으로 바로 검증한다.
    cfg = apply_overrides(Config.load(config_path), args)
    smap = SelectorMap.load(selectors_path)
    print("\n제대로 읽히는지 확인해 보겠습니다…")
    try:
        snapshot = asyncio.run(run_scan(cfg, smap))
    except ParadogoError as exc:
        print(f"\n확인 실패: {exc}")
        print("→ `python -m paradogo start` 를 다시 돌려 주세요.")
        return 1
    print()
    print(render_scan(snapshot, {d.isoformat() for d in dates}, color=supports_color()))

    if not snapshot.slots:
        print("\n재고를 하나도 읽지 못했습니다. `start` 를 다시 돌리면서")
        print("2단계에서 '예약 달력'이 실제로 보이는 화면까지 들어가 주세요.")
        return 2

    print("\n" + "=" * 66)
    print("설정 끝났습니다. 이제 감시를 시작하면,")
    print("  · 취소가 나오는 즉시 잡아서 결제 화면까지 열어 드립니다")
    print("  · 그때 직접 결제하시면 됩니다 (결제 전엔 확정 아님)")
    print("  · 알림을 받으시려면 config.yaml 의 notify 를 켜세요")
    print("=" * 66)
    if not ask_yes("\n지금 감시를 시작할까요?"):
        print("\n나중에 시작하시려면:  python -m paradogo track")
        return 0

    notifier = Notifier(cfg.notify)
    result = asyncio.run(run_track(cfg, smap, notifier))
    if result is not None:
        print(result.message)
    return 0


def check_environment() -> list[str]:
    """설정이 있기 전에도 확인할 수 있는 것들. 처음 막혔을 때 여기부터 본다."""
    problems: list[str] = []
    print("[환경]")
    print(f"  Python      : {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < (3, 10):
        problems.append("Python 3.10 이상이 필요합니다.")
        print("              ✗ 3.10 이상이 필요합니다")

    print(f"  실행 폴더    : {Path.cwd()}")
    if not (Path.cwd() / "paradogo").is_dir():
        print("              ✗ 이 폴더에 paradogo 가 없습니다 — 저장소 폴더에서 실행하세요")
        problems.append(
            "저장소 폴더(cd kimoonz) 안에서 실행해야 합니다. "
            "'No module named paradogo' 오류가 나면 대부분 이것입니다."
        )

    try:
        import playwright  # noqa: F401

        print("  Playwright  : ✓ 설치됨")
    except ImportError:
        print("  Playwright  : ✗ 없음 → pip install -r requirements.txt")
        problems.append("Playwright 미설치: pip install -r requirements.txt")
        return problems

    # 브라우저가 실제로 받아졌는지는 설치 폴더만 보면 된다.
    # (드라이버를 띄워 확인하면 확인만 하고도 오류 로그가 남는다)
    found = next((d for d in _browser_dirs() if d.is_dir()), None)
    if found:
        print(f"  크롬(브라우저): ✓ {found}")
    else:
        print("  크롬(브라우저): ✗ 없음 → python -m playwright install chromium")
        problems.append("브라우저 미설치: python -m playwright install chromium")

    return problems


def _browser_dirs() -> list[Path]:
    """Playwright 가 브라우저를 받아 두는 위치들."""
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    roots = [Path(override)] if override else [
        Path.home() / ".cache" / "ms-playwright",                       # Linux
        Path.home() / "Library" / "Caches" / "ms-playwright",           # macOS
        Path.home() / "AppData" / "Local" / "ms-playwright",            # Windows
    ]
    dirs: list[Path] = []
    for root in roots:
        try:
            dirs.extend(sorted(root.glob("chromium*")))
        except OSError:
            continue
    return dirs


def check_network(base_url: str) -> list[str]:
    import requests

    print("\n[연결]")
    try:
        resp = requests.head(base_url, timeout=10, allow_redirects=True)
        print(f"  {base_url} → {resp.status_code}")
        return []
    except Exception as exc:
        print(f"  {base_url} → 접속 실패 ({type(exc).__name__})")
        return [f"파라다이스 홈페이지에 접속하지 못했습니다: {exc}"]


def cmd_doctor(args: argparse.Namespace) -> int:
    problems = check_environment()

    if not Path(args.config).exists():
        problems += check_network(SiteConfig().base_url)
        print(f"\n[설정] 아직 설정하지 않았습니다 ({args.config} 없음)")
        print("\n다음을 실행하세요:")
        print("  python -m paradogo --date 2026-09-19 --nights 1 start")
        if problems:
            print("\n먼저 해결해야 할 것:")
            for item in problems:
                print(f"  · {item}")
        return 1

    try:
        cfg = apply_overrides(Config.load(args.config), args)
    except ParadogoError as exc:
        print(f"\n[설정] ✗ {exc}")
        return 1
    problems += check_network(cfg.site.base_url)
    print(f"\n[설정] ✓ {args.config}")
    print(f"       사이트     : {cfg.site.base_url}")
    print(f"       로그인 URL : {cfg.site.login_url}")
    print(f"       예약 URL   : {cfg.site.booking_url}")
    print(f"       대상 날짜  : {', '.join(d.isoformat() for d in cfg.target.check_in_dates) or '(없음)'}")
    print(f"       희망 캐빈  : {', '.join(cfg.target.cabin_types) or '전체'}")
    zones = ", ".join(cfg.target.zones) or "전체"
    if cfg.target.exclude_zones:
        zones += f" (제외 {', '.join(cfg.target.exclude_zones)})"
    print(f"       희망 구역  : {zones}")
    print(f"       박수       : {', '.join(f'{n}박' for n in cfg.target.nights_options)}")
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

    host = urlparse(cfg.site.base_url).hostname or ""
    if not host.endswith("paradise.co.kr") and not host.endswith("paradisespa.co.kr"):
        print(f"       ⚠ base_url 이 파라다이스 공식 도메인이 아닙니다: {host or cfg.site.base_url}")
        problems.append(
            "예약은 파라다이스 공식 홈페이지(paradisespa.co.kr)에서 해야 합니다. "
            "site.base_url 을 확인하세요."
        )

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
    now = now_kst()
    nxt = next_open_datetime(now, open_cfg.day_of_month, open_cfg.hour, open_cfg.minute)
    print(f"\n[오픈] 다음 오픈 {nxt.strftime('%Y-%m-%d %H:%M')} "
          f"(남은 시간 {humanize((nxt - now).total_seconds())})")

    passed = []
    for stay in cfg.target.check_in_dates:
        opened = open_datetime_for_stay(
            stay, open_cfg.day_of_month, open_cfg.hour, open_cfg.minute
        )
        if opened <= now:
            passed.append(stay)
            print(f"       {stay} · 오픈 {opened:%Y-%m-%d %H:%M} — 이미 지남 → 취소표(track)만 가능")
        else:
            print(f"       {stay} · 오픈 {opened:%Y-%m-%d %H:%M} "
                  f"({humanize((opened - now).total_seconds())} 후) → snipe 대상")
    if passed and len(passed) == len(cfg.target.check_in_dates):
        print("       ⚠ 모든 대상 날짜의 오픈이 지났습니다. `snipe` 대신 `track` 을 쓰세요.")

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
    import getpass

    from . import credentials
    from .browser import BrowserSession
    from .flow import BookingFlow

    cfg, smap = load_all(args)
    state_dir = cfg.run.storage_state.parent

    if args.forget:
        removed = credentials.clear(state_dir)
        print("지웠습니다: " + (", ".join(removed) if removed else "(저장된 것 없음)"))
        return 0

    if args.save:
        print("자동 로그인에 쓸 아이디·비밀번호를 저장합니다.")
        print(f"저장 위치: {credentials.backend_name()}")
        if credentials.backend_name() == "로컬 파일":
            print("  ※ 이 PC 를 쓸 수 있는 사람은 읽을 수 있습니다(암호화가 아닙니다).")
            print("     더 안전하게 하려면: pip install keyring")
        login_id = input("아이디: ").strip()
        password = getpass.getpass("비밀번호(화면에 안 보입니다): ")
        if not login_id or not password:
            print("아이디와 비밀번호가 모두 필요합니다.")
            return 1
        where = credentials.save(login_id, password, state_dir)
        print(f"저장했습니다 → {where}")
        print("이제 실제로 로그인이 되는지 확인합니다…")
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

    try:
        code = asyncio.run(main())
    except ParadogoError as exc:
        print(f"\n로그인 실패: {exc}")
        if args.save:
            print("\n저장한 정보는 그대로 두었습니다. 아이디·비밀번호를 다시 확인하시고")
            print("`python -m paradogo login --save` 로 다시 저장하거나,")
            print("캡차·본인확인이 있으면 `python -m paradogo login --manual` 을 쓰세요.")
        return 1
    if args.save:
        print("자동 로그인이 확인됐습니다. 세션이 풀려도 알아서 다시 로그인합니다.")
    return code


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

    result = asyncio.run(run_snipe(cfg, smap, notifier, open_at=open_at, force=args.force))
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
    if args.forever:
        from .supervisor import run_forever

        result = asyncio.run(run_forever(cfg, smap, notifier, args.stop_on_success))
    else:
        result = asyncio.run(run_track(cfg, smap, notifier))
    if result is None:
        return 0
    print(result.message)
    return 0 if result.ok else 2


def cmd_status(args: argparse.Namespace) -> int:
    from .supervisor import describe, heartbeat_path, read_heartbeat

    cfg, _ = load_all(args, need_selectors=False)
    beat = read_heartbeat(heartbeat_path(cfg))
    print(describe(beat))
    if beat is None or not beat.alive:
        return 1
    return 0


def cmd_service(args: argparse.Namespace) -> int:
    from .service import build_plan, describe, install

    # 등록될 명령에 지금 준 대상 조건을 그대로 실어 준다.
    parts: list[str] = []
    if args.date:
        parts += [f"--date {d}" for d in args.date]
    if args.nights:
        parts.append(f"--nights {args.nights}")
    if args.zones:
        parts.append(f"--zones {args.zones}")
    if args.exclude_zones:
        parts.append(f"--exclude-zones {args.exclude_zones}")
    if args.config != DEFAULT_CONFIG:
        parts.append(f"-c {args.config}")
    if args.selectors != DEFAULT_SELECTORS:
        parts.append(f"-s {args.selectors}")

    plan = build_plan(Path.cwd(), args.os_name, " ".join(parts))
    if args.install:
        install(plan)
    else:
        print("─" * 60)
        print(plan.content.rstrip())
        print("─" * 60)
        print()
    print(describe(plan, installed=args.install))
    if not args.install:
        print("\n실제로 만들려면: python -m paradogo service --install")
    return 0


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

        by_zone = store.cancellation_by_zone()
        if by_zone:
            print("\n구역별 취소 발생")
            for zone, count in by_zone:
                label = zone if zone == "미상" else f"{zone}구역"
                print(f"  {label}  {count}건")

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
    "gui": cmd_gui,
    "start": cmd_start,
    "doctor": cmd_doctor,
    "next-open": cmd_next_open,
    "login": cmd_login,
    "scan": cmd_scan,
    "notify-test": cmd_notify_test,
    "discover": cmd_discover,
    "watch": cmd_watch,
    "track": cmd_track,
    "stats": cmd_stats,
    "status": cmd_status,
    "service": cmd_service,
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
    except EOFError:
        # 입력을 받을 수 없는 환경(더블클릭·파이프·서비스 실행 등)에서
        # 파이썬 오류가 그대로 튀어나오면 무엇이 문제인지 알 수가 없다.
        print("\n키보드 입력을 받을 수 없는 상태입니다.")
        print("터미널(명령 프롬프트)을 직접 열어서 실행하시거나,")
        print("물어보는 항목을 옵션으로 미리 넘겨 주세요. 예:")
        print("  python -m paradogo --date 2026-09-19 --nights 1 --zones C,D start")
        return 1


if __name__ == "__main__":
    sys.exit(main())
