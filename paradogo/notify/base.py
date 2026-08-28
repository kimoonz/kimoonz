"""알림 채널 공통 인터페이스."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Message:
    title: str
    body: str
    screenshot: Path | None = None
    url: str | None = None

    def as_text(self) -> str:
        lines = [self.title, "", self.body]
        if self.url:
            lines += ["", f"링크: {self.url}"]
        return "\n".join(lines).strip()


class Channel:
    """알림 채널 베이스. 전송 실패가 예약 실행을 막으면 안 되므로 예외를 삼킨다."""

    name = "base"

    @property
    def usable(self) -> bool:
        return False

    def _send(self, message: Message) -> None:
        raise NotImplementedError

    def send(self, message: Message) -> bool:
        if not self.usable:
            return False
        try:
            self._send(message)
            return True
        except Exception as exc:
            log.warning("[%s] 알림 전송 실패: %s", self.name, exc)
            return False
