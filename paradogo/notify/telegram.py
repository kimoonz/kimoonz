"""텔레그램 봇 알림."""

from __future__ import annotations

import requests

from ..config import TelegramConfig
from .base import Channel, Message

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, cfg: TelegramConfig, timeout: float = 10.0) -> None:
        self.cfg = cfg
        self.timeout = timeout

    @property
    def usable(self) -> bool:
        return self.cfg.usable

    def _send(self, message: Message) -> None:
        text = message.as_text()
        if message.screenshot and message.screenshot.exists():
            # 캡션은 1024자 제한이라 넘치면 사진과 본문을 나눠 보낸다.
            caption = text if len(text) <= 1000 else message.title
            with message.screenshot.open("rb") as fh:
                resp = requests.post(
                    API.format(token=self.cfg.bot_token, method="sendPhoto"),
                    data={"chat_id": self.cfg.chat_id, "caption": caption},
                    files={"photo": fh},
                    timeout=self.timeout,
                )
            resp.raise_for_status()
            if caption != text:
                self._send_text(text)
            return
        self._send_text(text)

    def _send_text(self, text: str) -> None:
        resp = requests.post(
            API.format(token=self.cfg.bot_token, method="sendMessage"),
            data={
                "chat_id": self.cfg.chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
