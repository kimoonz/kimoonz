"""셀렉터 맵.

사이트 DOM은 언제든 바뀌므로 코드에 셀렉터를 박지 않는다. ``config/selectors.yaml`` 에
논리적 이름 → 후보 셀렉터 목록을 두고, 실행 시 위에서부터 순서대로 시도한다.
후보 문자열은 Playwright 셀렉터 문법을 그대로 쓴다(CSS, ``text=``, ``role=``, ``xpath=`` 등).

후보에 ``{date}`` / ``{day}`` / ``{year}`` / ``{month}`` 같은 placeholder 를 쓰면
호출 시 넘긴 값으로 치환된다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from .errors import ConfigError, SelectorNotFound

log = logging.getLogger(__name__)


def _flatten(node: Any, prefix: str = "") -> dict[str, list[str]]:
    """중첩 매핑을 'a.b.c' → [후보...] 평면 딕셔너리로 편다."""
    out: dict[str, list[str]] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten(value, child))
    elif isinstance(node, list):
        out[prefix] = [str(v) for v in node if str(v).strip()]
    elif node is None:
        out[prefix] = []
    else:
        out[prefix] = [str(node)]
    return out


@dataclass(slots=True)
class SelectorMap:
    entries: dict[str, list[str]] = field(default_factory=dict)
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any], source: Path | None = None) -> "SelectorMap":
        return cls(entries=_flatten(raw or {}), source_path=source)

    @classmethod
    def load(cls, path: str | Path) -> "SelectorMap":
        p = Path(path)
        if not p.exists():
            raise ConfigError(
                f"셀렉터 파일이 없습니다: {p}\n"
                "→ `python -m paradogo start` 를 실행하면 자동으로 만들어집니다."
            )
        with p.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls.from_dict(raw, source=p)

    def candidates(self, key: str, **fmt: Any) -> list[str]:
        """``key`` 의 후보 목록을 placeholder 치환해서 돌려준다."""
        raw = self.entries.get(key, [])
        if not raw:
            return []
        rendered: list[str] = []
        for candidate in raw:
            try:
                rendered.append(candidate.format(**fmt) if fmt else candidate)
            except (KeyError, IndexError):
                # placeholder 가 안 맞으면 원문 그대로 시도한다(중괄호를 쓰는 CSS 대비).
                rendered.append(candidate)
        return rendered

    def has(self, key: str) -> bool:
        return bool(self.entries.get(key))

    def missing(self, required: Iterable[str]) -> list[str]:
        return [key for key in required if not self.has(key)]


async def first_visible(
    scope: Any,
    smap: SelectorMap,
    key: str,
    timeout_ms: int = 4000,
    required: bool = True,
    **fmt: Any,
):
    """후보를 순서대로 시도해 화면에 보이는 첫 로케이터를 돌려준다.

    ``scope`` 는 Page 또는 Locator. 못 찾으면 required 면 SelectorNotFound, 아니면 None.
    """
    candidates = smap.candidates(key, **fmt)
    if not candidates:
        if required:
            raise SelectorNotFound(key, [])
        return None

    # 후보가 여럿이면 각각에 짧은 시간만 준다. 전체 예산은 timeout_ms 를 넘지 않는다.
    per_candidate = max(500, timeout_ms // max(1, len(candidates)))
    for candidate in candidates:
        try:
            locator = scope.locator(candidate).first
            await locator.wait_for(state="visible", timeout=per_candidate)
            log.debug("셀렉터 %s → %r 매치", key, candidate)
            return locator
        except Exception:
            continue
    if required:
        raise SelectorNotFound(key, candidates)
    return None


async def is_present(
    scope: Any, smap: SelectorMap, key: str, timeout_ms: int = 1500, **fmt: Any
) -> bool:
    """후보 중 하나라도 보이면 True."""
    found = await first_visible(
        scope, smap, key, timeout_ms=timeout_ms, required=False, **fmt
    )
    return found is not None


async def click(
    scope: Any, smap: SelectorMap, key: str, timeout_ms: int = 4000, **fmt: Any
) -> None:
    locator = await first_visible(scope, smap, key, timeout_ms=timeout_ms, **fmt)
    await locator.click()


async def fill(
    scope: Any,
    smap: SelectorMap,
    key: str,
    value: str,
    timeout_ms: int = 4000,
    **fmt: Any,
) -> bool:
    """값이 있을 때만 채운다. 셀렉터가 없으면(선택 항목) 조용히 건너뛴다."""
    if not value:
        return False
    locator = await first_visible(
        scope, smap, key, timeout_ms=timeout_ms, required=False, **fmt
    )
    if locator is None:
        log.debug("입력 필드 %s 를 찾지 못해 건너뜁니다.", key)
        return False
    await locator.fill(value)
    return True


async def first_nonempty(
    scope: Any, smap: SelectorMap, key: str, required: bool = True, **fmt: Any
):
    """요소가 1개 이상 잡히는 첫 후보의 로케이터(전체 집합)를 돌려준다.

    카드 목록처럼 '여러 개'를 다뤄야 할 때 ``first_visible`` 대신 쓴다.
    """
    candidates = smap.candidates(key, **fmt)
    for candidate in candidates:
        locator = scope.locator(candidate)
        try:
            if await locator.count() > 0:
                return locator
        except Exception:
            continue
    if required:
        raise SelectorNotFound(key, candidates)
    return None
