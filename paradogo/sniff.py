"""재고 조회 API 찾기.

달력 페이지를 매번 통째로 여는 대신, 사이트가 내부적으로 쓰는 재고 조회 요청을
직접 호출하면 훨씬 빠르고 가볍다. 문제는 그 주소와 응답 구조를 모른다는 것.

이 명령은 브라우저를 띄운 채 오가는 XHR/fetch 응답을 엿듣고, 그중 '날짜가 들어 있는
객체 배열'을 담은 응답을 골라 ``config/config.yaml`` 의 ``api:`` 블록 초안을 만든다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .browser import BrowserSession
from .config import Config

log = logging.getLogger(__name__)

_DATE_LIKE = re.compile(r"^\d{4}[-.\/]?\d{2}[-.\/]?\d{2}$")
_DATE_KEY_HINTS = ("date", "dt", "ymd", "day", "일자", "rsv")
_CABIN_KEY_HINTS = ("name", "nm", "room", "type", "title", "prod", "goods")
_REMAIN_KEY_HINTS = ("rest", "remain", "cnt", "qty", "stock", "avail", "possible")
_STATUS_KEY_HINTS = ("status", "state", "stat", "yn", "flag", "sale", "code")
_PRICE_KEY_HINTS = ("price", "amt", "amount", "fee", "cost", "won")

MAX_BODY_BYTES = 400_000


def _looks_like_date(value: Any) -> bool:
    return isinstance(value, (str, int)) and bool(_DATE_LIKE.match(str(value).strip()))


def find_item_arrays(payload: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, dict]]:
    """응답 안에서 '객체 배열'이 있는 경로를 모두 찾는다. (경로, 첫 항목) 목록."""
    found: list[tuple[str, dict]] = []
    if depth > 6:
        return found
    if isinstance(payload, list):
        objects = [item for item in payload if isinstance(item, dict)]
        if objects:
            found.append((prefix, objects[0]))
        elif payload and isinstance(payload[0], (dict, list)):
            found.extend(find_item_arrays(payload[0], prefix, depth + 1))
    elif isinstance(payload, dict):
        for key, value in payload.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            found.extend(find_item_arrays(value, child, depth + 1))
    return found


def _pick_key(item: dict, hints: tuple[str, ...], predicate=None) -> str:
    for key, value in item.items():
        lowered = str(key).lower()
        if any(hint in lowered for hint in hints):
            if predicate is None or predicate(value):
                return str(key)
    return ""


def score_candidate(item: dict) -> int:
    """재고 목록처럼 보일수록 높은 점수."""
    score = 0
    for key, value in item.items():
        lowered = str(key).lower()
        if _looks_like_date(value):
            score += 5
        if any(h in lowered for h in _DATE_KEY_HINTS):
            score += 2
        if any(h in lowered for h in _REMAIN_KEY_HINTS) and isinstance(value, int):
            score += 3
        if any(h in lowered for h in _STATUS_KEY_HINTS):
            score += 1
        if any(h in lowered for h in _CABIN_KEY_HINTS) and isinstance(value, str):
            score += 1
    return score


def guess_mapping(url: str, payload: Any) -> dict[str, Any] | None:
    """가장 재고 목록다워 보이는 배열을 골라 api 블록 초안을 만든다."""
    candidates = find_item_arrays(payload)
    if not candidates:
        return None
    path, item = max(candidates, key=lambda pair: score_candidate(pair[1]))
    if score_candidate(item) < 5:
        return None

    date_field = _pick_key(item, _DATE_KEY_HINTS, _looks_like_date)
    if not date_field:
        for key, value in item.items():
            if _looks_like_date(value):
                date_field = str(key)
                break
    if not date_field:
        return None

    remaining_field = _pick_key(item, _REMAIN_KEY_HINTS, lambda v: isinstance(v, (int, str)))
    status_field = "" if remaining_field else _pick_key(item, _STATUS_KEY_HINTS)

    return {
        "enabled": True,
        "url": templatize_url(url),
        "method": "GET",
        "items_path": path,
        "date_field": date_field,
        "cabin_field": _pick_key(item, _CABIN_KEY_HINTS, lambda v: isinstance(v, str)),
        "remaining_field": remaining_field,
        "price_field": _pick_key(item, _PRICE_KEY_HINTS),
        "status_field": status_field,
        "status_available_values": [],
        "_sample_item": item,
    }


def templatize_url(url: str) -> str:
    """URL 안의 연·월 값을 placeholder 로 바꿔 다른 달에도 쓸 수 있게 한다."""
    out = re.sub(r"(?<=[=/])(\d{4})(\d{2})(?![\d])", "{year}{month02}", url)
    out = re.sub(r"(?<=[=/])(\d{4})-(\d{2})(?![\d])", "{year}-{month02}", out)
    out = re.sub(r"([?&](?:year|yyyy|yy)=)\d{4}", r"\1{year}", out, flags=re.I)
    out = re.sub(r"([?&](?:month|mm)=)\d{1,2}", r"\1{month02}", out, flags=re.I)
    return out


async def run_sniff(cfg: Config, url: str | None, interactive: bool = True) -> Path:
    target = url or cfg.site.booking_url
    out_dir = cfg.run.artifacts_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    captured: list[dict[str, Any]] = []

    async with BrowserSession(cfg) as session:
        page = session.page
        assert page is not None

        async def on_response(response) -> None:
            try:
                content_type = (response.headers or {}).get("content-type", "")
                if "json" not in content_type.lower() or not response.ok:
                    return
                body = await response.body()
                if len(body) > MAX_BODY_BYTES:
                    return
                payload = json.loads(body.decode("utf-8", "replace"))
            except Exception:
                return
            captured.append(
                {
                    "url": response.url,
                    "method": response.request.method,
                    "post_data": response.request.post_data,
                    "payload": payload,
                }
            )

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        log.info("페이지 로드: %s", target)
        await page.goto(target, wait_until="domcontentloaded")

        if interactive:
            print("\n" + "=" * 68)
            print("브라우저에서 예약 달력을 열고 달을 넘기거나 날짜를 눌러 보세요.")
            print("그때 사이트가 보내는 재고 조회 요청을 엿듣습니다.")
            print("충분히 눌러봤으면 이 터미널에서 Enter 를 누르세요.")
            print("=" * 68)
            await asyncio.get_event_loop().run_in_executor(None, input)
        else:
            await page.wait_for_timeout(5000)

    raw_path = out_dir / f"{stamp}_sniff.json"
    raw_path.write_text(
        json.dumps(
            [{k: v for k, v in c.items() if k != "payload"} | {"payload": c["payload"]}
             for c in captured],
            ensure_ascii=False,
            indent=2,
        )[:2_000_000],
        encoding="utf-8",
    )
    log.info("JSON 응답 %d건을 기록했습니다: %s", len(captured), raw_path)

    ranked: list[tuple[int, dict[str, Any]]] = []
    for entry in captured:
        guess = guess_mapping(entry["url"], entry["payload"])
        if guess:
            ranked.append((score_candidate(guess["_sample_item"]), guess))
    ranked.sort(key=lambda pair: pair[0], reverse=True)

    draft = Path("config/api.discovered.yaml")
    draft.parent.mkdir(parents=True, exist_ok=True)
    if not ranked:
        draft.write_text(
            "# 재고 목록처럼 보이는 JSON 응답을 찾지 못했습니다.\n"
            f"# 기록된 응답 {len(captured)}건은 {raw_path} 에 있으니 직접 확인해 보세요.\n"
            "# API를 못 찾아도 추적기는 달력 DOM을 읽는 방식으로 동작합니다.\n",
            encoding="utf-8",
        )
        log.warning("재고 API 후보를 찾지 못했습니다. 달력 DOM 폴백을 쓰세요.")
        return draft

    best = ranked[0][1]
    sample = best.pop("_sample_item")
    header = (
        "# `python -m paradogo sniff` 가 만든 초안입니다.\n"
        "# 확인 후 이 내용을 config/config.yaml 의 최상위 `api:` 블록으로 옮기세요.\n"
        f"# 응답 샘플 1건: {json.dumps(sample, ensure_ascii=False)[:400]}\n"
        f"# 후보 {len(ranked)}건 중 1순위. 나머지는 {raw_path} 참고.\n"
        "# status_field 를 쓴다면 status_available_values 에 '예약가능'에 해당하는\n"
        "# 값들을 직접 채워야 합니다.\n\n"
    )
    draft.write_text(
        header + yaml.safe_dump({"api": best}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    log.info("api 블록 초안: %s", draft)
    return draft
