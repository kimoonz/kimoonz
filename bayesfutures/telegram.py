"""텔레그램 발송."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
MAX_LEN = 4096


class TelegramError(RuntimeError):
    pass


@dataclass
class Telegram:
    token: str | None
    chat_id: str | None
    dry_run: bool = False
    timeout: int = 20
    retries: int = 3

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, silent: bool = False) -> bool:
        """메시지 발송. dry_run이면 콘솔 출력."""
        if self.dry_run or not self.configured:
            if not self.configured and not self.dry_run:
                log.warning("텔레그램 미설정 — 콘솔로 출력합니다. "
                            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 를 설정하세요.")
            print("\n" + "=" * 46)
            print(text)
            print("=" * 46 + "\n", flush=True)
            return True

        ok = True
        for chunk in _split(text):
            ok = self._post(chunk, silent) and ok
        return ok

    def _post(self, text: str, silent: bool) -> bool:
        url = f"{API}/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
            "disable_notification": silent,
        }
        delay = 2.0
        for attempt in range(1, self.retries + 1):
            try:
                resp = requests.post(url, json=payload, timeout=self.timeout)
                if resp.status_code == 200:
                    return True
                body = resp.text[:300]
                # 텔레그램 쪽 속도 제한
                if resp.status_code == 429:
                    retry_after = 5
                    try:
                        retry_after = int(resp.json().get("parameters", {})
                                          .get("retry_after", 5))
                    except Exception:
                        pass
                    time.sleep(min(retry_after, 60))
                    continue
                if 400 <= resp.status_code < 500:
                    log.error("텔레그램 거부 (%s): %s", resp.status_code, body)
                    return False       # 토큰/채팅ID 문제 — 재시도해도 소용 없음
                log.warning("텔레그램 오류 (%s) 재시도 %d/%d: %s",
                            resp.status_code, attempt, self.retries, body)
            except requests.RequestException as exc:
                log.warning("텔레그램 통신 실패 재시도 %d/%d: %s", attempt, self.retries, exc)
            if attempt < self.retries:
                time.sleep(delay)
                delay *= 2
        return False

    def check(self) -> str:
        """봇 연결 확인 (getMe)."""
        if not self.token:
            raise TelegramError("TELEGRAM_BOT_TOKEN 이 설정되지 않았습니다.")
        resp = requests.get(f"{API}/bot{self.token}/getMe", timeout=self.timeout)
        if resp.status_code != 200:
            raise TelegramError(f"봇 토큰이 유효하지 않습니다 ({resp.status_code}).")
        return resp.json()["result"].get("username", "?")


def _split(text: str, limit: int = MAX_LEN) -> list[str]:
    """텔레그램 길이 제한에 맞춰 줄 단위로 자른다."""
    if len(text) <= limit:
        return [text]
    chunks, buf = [], ""
    for line in text.split("\n"):
        while len(line) > limit:                 # 줄 하나가 제한보다 길면 강제 분할
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(buf) + len(line) + 1 > limit:
            if buf:
                chunks.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        chunks.append(buf)
    return chunks
