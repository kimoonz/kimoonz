"""실제 사이트에서 셀렉터 후보를 수집한다.

이 저장소는 사이트 DOM을 모른 채 작성됐다. 처음 한 번은 이 명령으로 진짜 화면을 열어
후보를 뽑고, 그 결과를 보고 ``config/selectors.yaml`` 을 채우면 된다.

``--interactive`` 로 실행하면 브라우저를 띄운 채 기다린다. 로그인·날짜 선택 등
원하는 화면까지 직접 이동한 뒤 터미널에서 Enter 를 치면 그 화면을 분석한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

import yaml

from .browser import BrowserSession
from .config import Config

log = logging.getLogger(__name__)

# 페이지 안에서 실행되어 후보를 뽑아 오는 스크립트.
_COLLECT_JS = r"""
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const cssPath = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 4) {
      let part = node.tagName.toLowerCase();
      const cls = (node.className || '').toString().trim().split(/\s+/)
        .filter(c => c && !/^(ng|is|js)-/.test(c) && c.length < 30).slice(0, 2);
      if (cls.length) part += '.' + cls.map(c => CSS.escape(c)).join('.');
      parts.unshift(part);
      if (node.id) { parts[0] = '#' + CSS.escape(node.id); break; }
      node = node.parentElement;
    }
    return parts.join(' > ');
  };
  const text = (el) => (el.innerText || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 60);

  const inputs = [...document.querySelectorAll('input, select, textarea')]
    .filter(visible)
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      id: el.id || '',
      name: el.getAttribute('name') || '',
      placeholder: el.getAttribute('placeholder') || '',
      selector: cssPath(el),
    }));

  const clickables = [...document.querySelectorAll('button, a, [role=button], input[type=submit]')]
    .filter(visible)
    .map(el => ({ text: text(el), href: el.getAttribute('href') || '', selector: cssPath(el) }))
    .filter(x => x.text || x.href);

  // 날짜 칸 후보: data-* 속성에 날짜처럼 생긴 값을 가진 요소
  const dateish = [...document.querySelectorAll('*')].filter(visible).map(el => {
    const hit = [...el.attributes].find(a =>
      /^data-/.test(a.name) && /^\d{4}[-.\/]?\d{2}[-.\/]?\d{2}$|^\d{1,2}$/.test(a.value));
    return hit ? { attr: hit.name, value: hit.value, selector: cssPath(el),
                   classes: (el.className || '').toString().slice(0, 80) } : null;
  }).filter(Boolean).slice(0, 40);

  // 반복 카드 후보: 같은 클래스 조합이 3번 이상 나오는 컨테이너
  const counts = {};
  for (const el of document.querySelectorAll('li, div, article, tr')) {
    if (!visible(el)) continue;
    const cls = (el.className || '').toString().trim().split(/\s+/).filter(Boolean).slice(0, 2).join('.');
    if (!cls || cls.length > 40) continue;
    const key = el.tagName.toLowerCase() + '.' + cls;
    counts[key] = (counts[key] || 0) + 1;
  }
  const repeated = Object.entries(counts).filter(([, n]) => n >= 3)
    .sort((a, b) => b[1] - a[1]).slice(0, 15)
    .map(([selector, count]) => ({ selector, count }));

  return { url: location.href, title: document.title, inputs, clickables, dateish, repeated };
}
"""

# 사람이 보고 채우기 쉽도록 논리 키 목록을 미리 깔아 둔다.
_SKELETON_KEYS: dict[str, list[str]] = {
    "common.popup_close": [],
    "login.id_input": [],
    "login.pw_input": [],
    "login.submit": [],
    "login.success_marker": [],
    "booking.month_label": [],
    "booking.prev_month": [],
    "booking.next_month": [],
    "booking.day_cell": [],
    "booking.day_soldout_marker": [],
    "booking.search_button": [],
    "booking.room_card": [],
    "booking.room_name": [],
    "booking.room_price": [],
    "booking.room_soldout_marker": [],
    "booking.room_reserve_button": [],
    "guest.name": [],
    "guest.phone": [],
    "guest.email": [],
    "guest.adults": [],
    "guest.children": [],
    "guest.agree_all": [],
    "guest.to_payment": [],
    "payment.marker": [],
}

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "login.submit": ("로그인", "login", "sign in"),
    "login.success_marker": ("로그아웃", "logout", "마이페이지"),
    "common.popup_close": ("닫기", "오늘 하루", "close", "확인"),
    "booking.next_month": ("다음", "next", ">"),
    "booking.prev_month": ("이전", "prev", "<"),
    "booking.search_button": ("조회", "검색", "search"),
    "booking.room_reserve_button": ("예약", "선택", "신청"),
    "guest.to_payment": ("결제", "다음 단계", "다음단계", "예약하기"),
    "guest.agree_all": ("전체 동의", "모두 동의", "전체동의"),
}

_INPUT_HINTS: dict[str, tuple[str, ...]] = {
    "login.id_input": ("id", "userid", "loginid", "아이디", "email"),
    "login.pw_input": ("pw", "passwd", "password", "비밀번호"),
    "guest.name": ("name", "이름", "예약자"),
    "guest.phone": ("phone", "tel", "mobile", "연락처", "휴대"),
    "guest.email": ("email", "메일"),
    "guest.adults": ("adult", "성인", "대인"),
    "guest.children": ("child", "아동", "소인"),
    "guest.agree_all": ("agreeall", "agree_all", "allagree", "전체동의"),
}


def _guess(data: dict) -> dict[str, list[str]]:
    """수집 결과에서 논리 키별 후보를 추측한다. 어디까지나 초안이다."""
    guessed = {k: list(v) for k, v in _SKELETON_KEYS.items()}

    for key, words in _KEYWORDS.items():
        for item in data.get("clickables", []):
            label = item["text"].lower()
            if any(w.lower() in label for w in words):
                candidate = f"text={item['text']}"
                if candidate not in guessed[key]:
                    guessed[key].append(candidate)
                if item["selector"] and item["selector"] not in guessed[key]:
                    guessed[key].append(item["selector"])
        guessed[key] = guessed[key][:4]

    for key, hints in _INPUT_HINTS.items():
        for item in data.get("inputs", []):
            haystack = " ".join(
                [item["id"], item["name"], item["placeholder"], item["type"]]
            ).lower()
            if any(h in haystack for h in hints):
                for candidate in filter(None, [
                    f"#{item['id']}" if item["id"] else "",
                    f"{item['tag']}[name='{item['name']}']" if item["name"] else "",
                    item["selector"],
                ]):
                    if candidate not in guessed[key]:
                        guessed[key].append(candidate)
        guessed[key] = guessed[key][:3]

    for item in data.get("dateish", [])[:3]:
        candidate = f"[{item['attr']}='{{date}}']"
        if candidate not in guessed["booking.day_cell"]:
            guessed["booking.day_cell"].append(candidate)

    for item in data.get("repeated", [])[:3]:
        guessed["booking.room_card"].append(item["selector"])

    return guessed


def _nest(flat: dict[str, list[str]]) -> dict:
    """'a.b' 키를 중첩 매핑으로 되돌린다(YAML 가독성)."""
    out: dict = {}
    for key, value in flat.items():
        head, _, tail = key.partition(".")
        if tail:
            out.setdefault(head, {})[tail] = value
        else:
            out[key] = value
    return out


async def run_discover(cfg: Config, url: str | None, interactive: bool) -> Path:
    target = url or cfg.site.booking_url
    out_dir = cfg.run.artifacts_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    async with BrowserSession(cfg) as session:
        page = session.page
        assert page is not None
        log.info("페이지 로드: %s", target)
        await page.goto(target, wait_until="domcontentloaded")

        if interactive:
            print("\n" + "=" * 68)
            print("브라우저에서 분석하고 싶은 화면까지 직접 이동하세요.")
            print("(로그인 → 캐빈파크 예약 → 날짜 선택 등)")
            print("준비되면 이 터미널에서 Enter 를 누르세요.")
            print("=" * 68)
            await asyncio.get_event_loop().run_in_executor(None, input)

        data = await page.evaluate(_COLLECT_JS)

        raw_path = out_dir / f"{stamp}_discover.json"
        raw_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        await session.screenshot("discover")
        await session.dump_html("discover")

    guessed = _guess(data)
    draft = Path("config/selectors.discovered.yaml")
    draft.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# `python -m paradogo discover` 가 만든 초안입니다.\n"
        f"# 수집 대상: {data.get('url')}\n"
        f"# 수집 시각: {stamp}\n"
        "# 비어 있는 키는 직접 채워야 합니다. 확인 후 config/selectors.yaml 로 복사하세요.\n"
        f"# 원본 수집 결과: {raw_path}\n\n"
    )
    draft.write_text(
        header + yaml.safe_dump(_nest(guessed), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    empty = [k for k, v in guessed.items() if not v]
    log.info("초안 저장: %s", draft)
    if empty:
        log.warning("아직 비어 있는 키 %d개: %s", len(empty), ", ".join(empty))
    return draft
