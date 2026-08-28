"""알림 팬아웃."""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import NotifyConfig
from .base import Channel, Message
from .mail import EmailChannel
from .telegram import TelegramChannel

log = logging.getLogger(__name__)

__all__ = ["Message", "Channel", "Notifier", "TelegramChannel", "EmailChannel"]


class Notifier:
    """설정된 모든 채널로 같은 메시지를 뿌린다. 채널 하나가 죽어도 나머지는 계속 간다."""

    def __init__(self, cfg: NotifyConfig) -> None:
        self.channels: list[Channel] = [
            TelegramChannel(cfg.telegram),
            EmailChannel(cfg.email),
        ]

    @property
    def active(self) -> list[str]:
        return [c.name for c in self.channels if c.usable]

    def send(
        self,
        title: str,
        body: str,
        screenshot: Path | None = None,
        url: str | None = None,
    ) -> dict[str, bool]:
        message = Message(title=title, body=body, screenshot=screenshot, url=url)
        log.info("[알림] %s\n%s", title, body)
        results: dict[str, bool] = {}
        for channel in self.channels:
            if channel.usable:
                results[channel.name] = channel.send(message)
        if not results:
            log.warning("활성화된 알림 채널이 없습니다. 콘솔 로그로만 남습니다.")
        return results
