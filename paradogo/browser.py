"""Playwright 브라우저 세션 관리.

로그인 세션은 storage_state 로 파일에 저장해 재사용한다. 오픈 시각에 로그인부터
새로 하면 몇 초를 그냥 버리기 때문에, 미리 로그인해 둔 세션을 그대로 얹는 것이 핵심이다.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from types import TracebackType

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .config import Config

log = logging.getLogger(__name__)

# 헤드리스 티가 덜 나도록 최소한만 손본다. 우회 목적이 아니라, 자동화 브라우저에서
# 레이아웃이 깨져 셀렉터가 안 잡히는 문제를 줄이기 위한 설정이다.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled", "--start-maximized"]


class BrowserSession:
    """async with 로 쓰는 브라우저 세션."""

    def __init__(self, cfg: Config, reuse_state: bool = True) -> None:
        self.cfg = cfg
        self.reuse_state = reuse_state
        self._pw = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def __aenter__(self) -> "BrowserSession":
        self._pw = await async_playwright().start()
        # 이미 설치된 크롬/크로미움을 쓰고 싶을 때(예: 사내 PC의 고정 버전) 경로를 지정한다.
        executable = os.environ.get("PARADOGO_CHROMIUM_PATH") or None
        if executable:
            log.info("지정된 브라우저를 사용합니다: %s", executable)
        self.browser = await self._pw.chromium.launch(
            headless=self.cfg.run.headless,
            slow_mo=self.cfg.run.slow_mo_ms or 0,
            args=LAUNCH_ARGS,
            executable_path=executable,
        )
        state_path = self.cfg.run.storage_state
        state_arg = (
            str(state_path)
            if self.reuse_state and state_path.exists()
            else None
        )
        if state_arg:
            log.info("저장된 로그인 세션을 불러옵니다: %s", state_path)
        self.context = await self.browser.new_context(
            locale="ko-KR",
            timezone_id=self.cfg.site.timezone,
            user_agent=DEFAULT_UA,
            viewport={"width": 1440, "height": 950},
            storage_state=state_arg,
        )
        self.context.set_default_timeout(15_000)
        self.page = await self.context.new_page()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        for closer in (self.context, self.browser):
            if closer is not None:
                try:
                    await closer.close()
                except Exception as err:
                    log.debug("종료 중 무시된 오류: %s", err)
        if self._pw is not None:
            await self._pw.stop()
        self._pw = None
        self.browser = None
        self.context = None
        self.page = None

    async def save_state(self) -> Path:
        """현재 쿠키/스토리지를 파일로 저장해 다음 실행에서 로그인을 건너뛰게 한다."""
        assert self.context is not None
        path = self.cfg.run.storage_state
        path.parent.mkdir(parents=True, exist_ok=True)
        await self.context.storage_state(path=str(path))
        log.info("로그인 세션을 저장했습니다: %s", path)
        return path

    async def screenshot(self, label: str, full_page: bool = True) -> Path | None:
        """실패 원인 파악과 알림 첨부용 스크린샷."""
        if self.page is None:
            return None
        directory = self.cfg.run.artifacts_dir
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)
        path = directory / f"{stamp}_{safe}.png"
        try:
            await self.page.screenshot(path=str(path), full_page=full_page)
            return path
        except Exception as err:
            log.debug("스크린샷 실패: %s", err)
            return None

    async def dump_html(self, label: str) -> Path | None:
        """셀렉터를 다시 만들 때 쓰는 원본 HTML 저장."""
        if self.page is None:
            return None
        directory = self.cfg.run.artifacts_dir
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = directory / f"{stamp}_{label}.html"
        try:
            path.write_text(await self.page.content(), encoding="utf-8")
            return path
        except Exception as err:
            log.debug("HTML 저장 실패: %s", err)
            return None
