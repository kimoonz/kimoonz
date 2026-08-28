"""설치 마법사 — `python -m paradogo start` 하나로 끝낸다.

사용자가 명령어를 외울 필요가 없어야 한다. 브라우저를 한 번 띄워 놓고 안내대로
클릭만 하면, 그 사이에 오가는 화면과 통신을 관찰해서 설정 파일을 자동으로 만든다.
(예전에는 discover / login / sniff 를 따로 돌려야 했던 일이다.)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .config import Config
from .discover import _COLLECT_JS, _guess

log = logging.getLogger(__name__)

# 달력처럼 특정 화면에서만 알 수 있는 것들을 따로 훑는다.
CALENDAR_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const sel = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1 && parts.length < 3) {
      let p = n.tagName.toLowerCase();
      const cls = (n.className || '').toString().trim().split(/\s+/)
        .filter(c => c && c.length < 30).slice(0, 2);
      if (cls.length) p += '.' + cls.map(c => CSS.escape(c)).join('.');
      parts.unshift(p);
      if (n.id) { parts[0] = '#' + CSS.escape(n.id); break; }
      n = n.parentElement;
    }
    return parts.join(' > ');
  };

  // 'YYYY년 M월' / 'YYYY.MM' / 'M월' 처럼 생긴 짧은 텍스트 = 달력 제목 후보
  const monthRe = /^\s*(\d{4}\s*[.\-년/]?\s*)?\d{1,2}\s*월?\s*$/;
  const monthLabels = [...document.querySelectorAll('span,div,p,h1,h2,h3,h4,strong,em,td,th')]
    .filter(el => vis(el) && el.children.length === 0)
    .filter(el => monthRe.test((el.innerText || '').trim()))
    .map(el => ({ text: (el.innerText || '').trim(), selector: sel(el) }))
    .slice(0, 5);

  // 날짜 값을 담은 속성이 있는 요소 = 날짜 칸 후보
  const attrCounts = {};
  for (const el of document.querySelectorAll('*')) {
    if (!vis(el)) continue;
    for (const a of el.attributes) {
      if (!/^data-/.test(a.name)) continue;
      if (!/^\d{4}[-.\/]?\d{2}[-.\/]?\d{2}$/.test(a.value)) continue;
      const key = a.name + '|' + el.tagName.toLowerCase();
      attrCounts[key] = (attrCounts[key] || 0) + 1;
    }
  }
  const dayAttrs = Object.entries(attrCounts)
    .filter(([, n]) => n >= 5)
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => ({ attr: k.split('|')[0], tag: k.split('|')[1], count: n }));

  // 매진처럼 보이는 클래스 이름과 짧은 문구를 모은다.
  const soldoutTokens = new Set();
  for (const el of document.querySelectorAll('[class]')) {
    for (const c of (el.className || '').toString().split(/\s+/)) {
      if (/sold|disabled|close|full|impossible|off/i.test(c) && c.length < 25) {
        soldoutTokens.add(c);
      }
    }
  }
  for (const el of document.querySelectorAll('span,em,i,b,div')) {
    const t = (el.innerText || '').trim();
    if (t.length <= 6 && /(마감|불가|매진|완료)/.test(t)) {
      soldoutTokens.add(t);
    }
  }

  return { monthLabels, dayAttrs, soldoutTokens: [...soldoutTokens].slice(0, 12) };
}
"""

__all__ = [
    "CALENDAR_JS",
    "COLLECT_JS",
    "fill_defaults",
    "merge_selectors",
    "nest",
    "render_config",
    "soldout_selector",
]

COLLECT_JS = _COLLECT_JS


def soldout_selector(token: str) -> str:
    """매진 표식 하나를 셀렉터로 바꾼다.

    영문/숫자뿐이면 클래스 이름으로, 한글이 섞였으면 화면 문구로 본다.
    """
    if token and all(ch.isascii() and (ch.isalnum() or ch in "-_") for ch in token):
        return f".{token}"
    return f"text={token}"


def merge_selectors(stages: dict[str, dict], calendar: dict[str, Any]) -> dict[str, list[str]]:
    """단계별로 수집한 화면에서 셀렉터를 조합한다.

    같은 논리 키라도 어느 화면에서 봤는지에 따라 신뢰도가 다르다. 예를 들어
    '로그아웃' 링크는 로그인 **후** 화면에서만 의미가 있다.
    """
    merged: dict[str, list[str]] = {}

    def take(stage: str, keys: tuple[str, ...]) -> None:
        data = stages.get(stage)
        if not data:
            return
        for key, values in _guess(data).items():
            if key not in keys or not values:
                continue
            bucket = merged.setdefault(key, [])
            for value in values:
                if value not in bucket:
                    bucket.append(value)

    take("login_form", ("login.id_input", "login.pw_input", "login.submit", "common.popup_close"))
    take(
        "calendar",
        (
            "login.success_marker",
            "common.popup_close",
            "booking.next_month",
            "booking.prev_month",
            "booking.search_button",
        ),
    )
    take("rooms", ("booking.room_card", "booking.room_reserve_button"))
    take(
        "guest",
        (
            "guest.name",
            "guest.phone",
            "guest.email",
            "guest.adults",
            "guest.children",
            "guest.agree_all",
            "guest.to_payment",
        ),
    )

    labels = calendar.get("monthLabels") or []
    if labels:
        merged["booking.month_label"] = [labels[0]["selector"]]

    day_attrs = calendar.get("dayAttrs") or []
    if day_attrs:
        attr, tag = day_attrs[0]["attr"], day_attrs[0]["tag"]
        merged["booking.day_cell"] = [f"[{attr}='{{date}}']"]
        merged["booking.day_cell_all"] = [f"{tag}[{attr}]", f"[{attr}]"]
        merged["booking.day_date_attr"] = [attr]
        # 체크아웃도 같은 달력에서 고르므로 같은 셀렉터를 쓴다.
        merged["booking.checkout_cell"] = [f"[{attr}='{{date}}']"]

    tokens = [t for t in (calendar.get("soldoutTokens") or []) if t]
    if tokens:
        merged["booking.day_soldout_tokens"] = tokens
        markers = [soldout_selector(t) for t in tokens[:4]]
        merged["booking.day_soldout_marker"] = markers
        merged["booking.room_soldout_marker"] = markers

    return merged


# 수집으로 못 채운 칸을 메울 흔한 패턴들. 빈 채로 두면 실행하자마자 실패한다.
FALLBACKS: dict[str, list[str]] = {
    "common.popup_close": ["text=오늘 하루 보지 않기", "text=닫기", "button.close"],
    "login.id_input": ["#userId", "input[name='userId']", "input[name='id']"],
    "login.pw_input": ["input[type='password']"],
    "login.submit": ["button[type='submit']", "text=로그인"],
    "login.success_marker": ["text=로그아웃", "text=마이페이지"],
    "booking.month_label": [".calendar .month", "[data-month]"],
    "booking.prev_month": ["text=이전달", ".prev"],
    "booking.next_month": ["text=다음달", ".next"],
    "booking.day_cell": ["[data-date='{date}']", "td[data-day='{day}']"],
    "booking.day_cell_all": ["[data-date]", ".calendar td"],
    "booking.day_date_attr": ["data-date"],
    "booking.day_soldout_marker": [".soldout", ".disabled", "text=마감"],
    "booking.day_soldout_tokens": ["soldout", "disabled", "마감", "예약불가"],
    "booking.search_button": [],
    "booking.nights_select": ["select[name='nights']"],
    "booking.nights_button": ["text={nights}박"],
    "booking.checkout_cell": [],
    "booking.room_card": [".room-list li", ".product-item"],
    "booking.room_name": [".room-name", "h3", ".tit"],
    "booking.room_price": [".price", ".won"],
    "booking.room_zone": [".zone", ".area"],
    "booking.room_soldout_marker": ["text=마감", "text=예약불가", ".soldout"],
    "booking.room_reserve_button": ["text=예약하기", "button.btn-reserve"],
    "guest.name": ["input[name='bookerName']", "input[name='name']"],
    "guest.phone": ["input[name='bookerTel']", "input[name='hp']"],
    "guest.email": ["input[name='email']"],
    "guest.adults": ["input[name='adultCnt']", "select[name='adult']"],
    "guest.children": ["input[name='childCnt']"],
    "guest.agree_all": ["#agreeAll", "text=전체 동의"],
    "guest.to_payment": ["text=결제하기", "text=다음 단계"],
    "payment.marker": ["text=결제수단", "text=결제 수단", ".payment-method"],
}


def fill_defaults(merged: dict[str, list[str]]) -> dict[str, list[str]]:
    """수집 결과를 우선하되, 비어 있는 칸은 기본 패턴으로 메운다."""
    out = {key: list(value) for key, value in FALLBACKS.items()}
    for key, values in merged.items():
        if values:
            out[key] = list(values)
    return out


def nest(flat: dict[str, list[str]]) -> dict:
    """'a.b' 키를 중첩 매핑으로 되돌린다(YAML 가독성)."""
    out: dict = {}
    for key, value in flat.items():
        head, _, tail = key.partition(".")
        if tail:
            out.setdefault(head, {})[tail] = value
        else:
            out[key] = value
    return out


def _yaml_list(values: list[Any]) -> str:
    return "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in values) + "]"


def render_config(
    base_url: str,
    login_path: str,
    booking_path: str,
    dates: list[date],
    nights: list[int],
    zones: list[str],
    exclude_zones: list[str],
    api: dict[str, Any] | None = None,
) -> str:
    """설정 파일 내용. 사람이 나중에 열어봐도 알아볼 수 있게 주석을 남긴다."""
    if api:
        api_block = yaml.safe_dump({"api": api}, allow_unicode=True, sort_keys=False)
    else:
        api_block = (
            "api:\n"
            "  enabled: false   # 재고 조회 API 를 찾지 못했습니다.\n"
            "                   # 달력 화면을 읽는 방식으로 동작합니다(조금 느림).\n"
        )

    return f"""# `python -m paradogo start` 가 만든 설정입니다. 언제든 직접 고쳐도 됩니다.

site:
  # 파라다이스 스파 도고 공식 홈페이지
  base_url: "{base_url}"
  login_path: "{login_path}"
  booking_path: "{booking_path}"
  timezone: "Asia/Seoul"

account:
  # 비밀번호는 파일에 적지 않습니다. 로그인은 브라우저에서 직접 하고
  # 그 세션(.state/session.json)을 재사용합니다.
  login_id: "${{PARADOGO_ID:-}}"
  password: "${{PARADOGO_PW:-}}"
  booker:
    name: ""
    phone: ""
    email: ""

target:
  check_in_dates: {_yaml_list([d.isoformat() for d in dates])}
  nights_options: {_yaml_list(nights)}     # 우선순위 순서
  cabin_types: []
  zones: {_yaml_list(zones)}
  exclude_zones: {_yaml_list(exclude_zones)}
  zone_pattern: ""
  zone_strict: true
  adults: 2
  children: 0

run:
  headless: false
  dry_run: false          # 취소를 잡으면 결제 직전까지 진행합니다 (결제는 직접)
  keep_open_minutes: 30
  storage_state: ".state/session.json"
  artifacts_dir: ".artifacts"
  track:
    interval_seconds: 20
    jitter_seconds: 3
    months_ahead: 2
    auto_reserve: true
    notify_all_changes: false
    reserve_cooldown_minutes: 10
    max_duration_minutes: 0
    dashboard: true
    db_path: ".state/tracker.db"
  open_time:
    day_of_month: 1
    hour: 9
    minute: 0
    lead_seconds: 30
    max_attempts: 40
    attempt_interval_ms: 700
    sync_clock: true

notify:
  # 알림을 켜려면 enabled 를 true 로 바꾸고 환경변수를 채우세요.
  telegram:
    enabled: false
    bot_token: "${{TELEGRAM_BOT_TOKEN:-}}"
    chat_id: "${{TELEGRAM_CHAT_ID:-}}"
  email:
    enabled: false
    smtp_host: "smtp.gmail.com"
    smtp_port: 587
    username: "${{SMTP_USER:-}}"
    password: "${{SMTP_PASS:-}}"
    sender: "${{SMTP_USER:-}}"
    recipients: []

{api_block}"""


def ask(prompt: str, default: str = "") -> str:
    """터미널 입력. 기본값이 있으면 그냥 Enter 로 넘어갈 수 있다."""
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def ask_yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = input(f"{prompt} [{hint}]: ").strip().lower()
    if not answer:
        return default
    return answer.startswith(("y", "ㅇ"))


async def wait_enter(message: str) -> None:
    print("\n" + "-" * 66)
    print(message)
    print("다 하셨으면 여기 터미널에서 Enter 를 누르세요.")
    print("-" * 66)
    await asyncio.get_event_loop().run_in_executor(None, input)


# ------------------------------------------------------------------ 실행부


async def _capture(page, stages: dict[str, dict], name: str) -> None:
    """지금 화면을 훑어 단계별 수집함에 넣는다."""
    try:
        stages[name] = await page.evaluate(COLLECT_JS)
    except Exception as exc:
        log.warning("[%s] 화면 수집 실패(계속 진행): %s", name, exc)
        stages[name] = {}


def _pick_api(captured: list[dict[str, Any]]) -> dict[str, Any] | None:
    """엿들은 JSON 응답 중 재고 목록으로 가장 그럴듯한 것을 고른다."""
    from .sniff import guess_mapping, score_candidate

    ranked: list[tuple[int, dict[str, Any]]] = []
    for entry in captured:
        guess = guess_mapping(entry["url"], entry["payload"])
        if guess:
            ranked.append((score_candidate(guess["_sample_item"]), guess))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    best = dict(ranked[0][1])
    best.pop("_sample_item", None)
    return best


def check_login_form(data: dict[str, Any]) -> tuple[bool, str]:
    inputs = data.get("inputs") or []
    has_pw = any(i.get("type") == "password" for i in inputs)
    if has_pw:
        return True, "아이디·비밀번호 입력칸을 찾았습니다."
    return False, "이 화면에서 비밀번호 입력칸을 못 찾았습니다. 로그인 화면이 맞나요?"


def check_calendar(data: dict[str, Any], calendar: dict[str, Any]) -> tuple[bool, str]:
    days = calendar.get("dayAttrs") or []
    labels = calendar.get("monthLabels") or []
    logged_in = any(
        any(word in (item.get("text") or "") for word in ("로그아웃", "마이페이지"))
        for item in (data.get("clickables") or [])
    )
    if days:
        note = f"날짜 칸 {days[0]['count']}개를 찾았습니다"
    elif labels:
        note = f"달력 제목 '{labels[0]['text']}' 을 찾았습니다"
    else:
        return False, "이 화면에서 달력을 못 찾았습니다. 캐빈 예약 달력이 맞나요?"
    if not logged_in:
        note += " (다만 로그인 표시는 확인하지 못했습니다)"
    return True, note + "."


def check_rooms(data: dict[str, Any]) -> tuple[bool, str]:
    repeated = data.get("repeated") or []
    has_reserve = any(
        any(word in (item.get("text") or "") for word in ("예약", "선택", "신청"))
        for item in (data.get("clickables") or [])
    )
    if repeated and has_reserve:
        return True, f"캐빈 목록 {repeated[0]['count']}칸과 예약 버튼을 찾았습니다."
    if not has_reserve:
        return False, "이 화면에서 '예약하기' 같은 버튼을 못 찾았습니다."
    return False, "캐빈 목록처럼 반복되는 항목을 못 찾았습니다."


def check_guest(data: dict[str, Any]) -> tuple[bool, str]:
    inputs = data.get("inputs") or []
    if len(inputs) >= 2:
        return True, f"입력칸 {len(inputs)}개를 찾았습니다."
    return False, "이 화면에서 예약자 정보 입력칸을 못 찾았습니다."


async def run_wizard(
    cfg: Config,
    config_path: Path,
    selectors_path: Path,
    dates: list[date],
    nights: list[int],
    zones: list[str],
    exclude_zones: list[str],
    auto_urls: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    """브라우저를 한 번 띄워 놓고 안내대로 따라오게 하면서 설정을 만든다.

    ``auto_urls`` 로 단계별 주소를 주면 그 화면을 대신 열어 준다(단계 이름 →  URL).
    이미 주소를 아는 경우 손이 덜 가고, 자동 테스트에서도 같은 경로를 탄다.

    단계마다 화면이 맞는지 바로 확인한다. 엉뚱한 화면에서 Enter 를 눌러도 조용히
    넘어가면, 한참 뒤 알 수 없는 오류로만 드러나기 때문이다.
    """
    from .browser import BrowserSession

    stages: dict[str, dict] = {}
    calendar: dict[str, Any] = {}
    captured: list[dict[str, Any]] = []
    login_url = cfg.site.login_url
    booking_url = cfg.site.booking_url

    async with BrowserSession(cfg, reuse_state=False) as session:
        page = session.page
        assert page is not None

        async def on_response(response) -> None:
            # 재고 조회 API 를 찾기 위해 오가는 JSON 응답을 조용히 기록해 둔다.
            try:
                if "json" not in (response.headers or {}).get("content-type", "").lower():
                    return
                if not response.ok:
                    return
                body = await response.body()
                if len(body) > 400_000:
                    return
                captured.append(
                    {"url": response.url, "payload": json.loads(body.decode("utf-8", "replace"))}
                )
            except Exception:
                return

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        async def step(name: str, instruction: str, validate, tries: int = 3) -> bool:
            for attempt in range(1, tries + 1):
                if auto_urls and name in auto_urls:
                    await page.goto(auto_urls[name], wait_until="domcontentloaded")
                await wait_enter(instruction)
                await _capture(page, stages, name)
                if name == "calendar":
                    try:
                        calendar.update(await page.evaluate(CALENDAR_JS))
                    except Exception as exc:
                        log.warning("달력 수집 실패: %s", exc)
                ok, detail = validate(stages.get(name) or {})
                if ok:
                    print(f"     ✓ {detail}")
                    return True
                print(f"\n     ! {detail}")
                print(f"       (현재 열려 있는 주소: {page.url})")
                if attempt < tries and ask_yes("     화면을 옮기고 다시 해보시겠어요?"):
                    continue
                print("     건너뜁니다 — 이 부분은 흔한 기본값으로 채웁니다.")
                return False
            return False

        print("\n브라우저 창을 띄웠습니다. 이제 안내대로 따라오시면 됩니다.")
        await page.goto(cfg.site.base_url, wait_until="domcontentloaded")

        if await step(
            "login_form",
            "1/4  파라다이스 홈페이지에서 [로그인] 화면까지 들어가 주세요.\n"
            "     (아직 로그인은 하지 마시고, 아이디·비밀번호 입력칸이 보이는 상태까지만)",
            check_login_form,
        ):
            login_url = page.url

        if await step(
            "calendar",
            "2/4  직접 로그인하신 뒤, 캐빈파크 [예약 달력] 화면까지 들어가 주세요.\n"
            "     (캡차·본인확인이 있어도 직접 하시면 됩니다)",
            lambda data: check_calendar(data, calendar),
        ):
            booking_url = page.url
        await session.save_state()
        print("     ✓ 로그인 세션을 저장했습니다. 다음부터는 자동으로 재사용합니다.")

        await step(
            "rooms",
            "3/4  달력에서 예약 가능한 날짜를 아무거나 눌러, [캐빈 목록]이 뜨게 해주세요.\n"
            "     (구역 A~H 와 '예약하기' 버튼이 보이는 화면)",
            check_rooms,
        )
        if not calendar.get("dayAttrs"):
            # 목록 화면에서 달력이 더 잘 보이는 사이트도 있다.
            try:
                calendar.update(await page.evaluate(CALENDAR_JS))
            except Exception:
                pass

        if ask_yes(
            "\n4/4  예약자 정보 입력 화면까지 한 번 들어가 보시겠어요?\n"
            "     (해두면 취소표를 잡을 때 더 정확합니다. 결제는 절대 하지 않습니다)"
        ):
            await step(
                "guest",
                "     [예약하기]를 눌러 예약자 정보 입력 화면까지 가주세요.\n"
                "     ※ 결제 버튼은 누르지 마세요.",
                check_guest,
            )
        else:
            print("     건너뜁니다. 나중에 필요하면 다시 `start` 를 돌리면 됩니다.")

    # 수집 결과를 설정 파일로
    selectors = fill_defaults(merge_selectors(stages, calendar))
    selectors_path.parent.mkdir(parents=True, exist_ok=True)
    selectors_path.write_text(
        "# `python -m paradogo start` 가 자동으로 만든 셀렉터입니다.\n"
        "# 화면이 바뀌어 안 맞으면 `start` 를 다시 돌리세요.\n\n"
        + yaml.safe_dump(nest(selectors), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    api = _pick_api(captured)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        render_config(
            base_url=cfg.site.base_url,
            login_path=_path_of(login_url),
            booking_path=_path_of(booking_url),
            dates=dates,
            nights=nights,
            zones=zones,
            exclude_zones=exclude_zones,
            api=api,
        ),
        encoding="utf-8",
    )

    print("\n설정 저장 완료")
    print(f"  · {config_path}")
    print(f"  · {selectors_path}")
    if api:
        print("  · 재고 조회 API 를 찾았습니다 — 취소 감지가 훨씬 빨라집니다.")
    else:
        print("  · 재고 조회 API 는 못 찾았습니다. 달력 화면을 읽는 방식으로 동작합니다.")
    return config_path, selectors_path


def _path_of(url: str) -> str:
    """전체 URL 에서 경로 부분만 남긴다."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return path
