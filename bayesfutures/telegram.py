"""텔레그램 발송."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
MAX_LEN = 4096


class TelegramError(RuntimeError):
    pass


# 세션 하나를 재사용한다. requests.post() 를 그냥 부르면 호출마다 새 세션과
# 새 TLS 컨텍스트를 만들며 CA 번들을 다시 읽는다. 파이썬이 동기화 드라이브에
# 올라가 있으면 그 비용이 메시지당 1분까지 간다.
_SESSION = requests.Session()


@dataclass
class Telegram:
    token: str | None
    chat_id: str | None
    dry_run: bool = False
    timeout: int = 20
    retries: int = 3
    on_chat_migrated: Callable[[str], None] | None = None

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
                resp = _SESSION.post(url, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                log.warning("텔레그램 통신 실패 재시도 %d/%d: %s", attempt, self.retries, exc)
                if attempt < self.retries:
                    time.sleep(delay)
                    delay *= 2
                continue

            if resp.status_code == 200:
                return True

            try:
                body = resp.json()
            except ValueError:
                body = {"description": resp.text[:300]}
            params = body.get("parameters") or {}

            # 텔레그램 쪽 속도 제한 — 대기 시간을 알려준다
            if resp.status_code == 429:
                wait = int(params.get("retry_after", 5))
                log.info("텔레그램 속도 제한, %d초 대기", wait)
                time.sleep(min(wait + 1, 60))
                continue

            # 일반 그룹은 멤버 추가나 설정 변경만으로 슈퍼그룹이 되고, 그때
            # chat_id 가 바뀐다. 새 id 는 이 에러에 딱 한 번 실려 오므로
            # 여기서 안 받으면 발송이 조용히 끊긴다.
            migrated = params.get("migrate_to_chat_id")
            if migrated and str(migrated) != str(self.chat_id):
                log.warning("채팅방이 슈퍼그룹으로 전환됨: %s -> %s", self.chat_id, migrated)
                self.chat_id = str(migrated)
                payload["chat_id"] = self.chat_id
                if self.on_chat_migrated:
                    try:
                        self.on_chat_migrated(self.chat_id)
                    except Exception as exc:
                        log.warning("새 chat_id 저장 실패: %s", exc)
                continue

            if 400 <= resp.status_code < 500:
                log.error("텔레그램 거부 (%s): %s", resp.status_code,
                          body.get("description"))
                return False       # 토큰/채팅ID 문제 — 재시도해도 소용 없음

            log.warning("텔레그램 오류 (%s) 재시도 %d/%d: %s", resp.status_code,
                        attempt, self.retries, body.get("description"))
            if attempt < self.retries:
                time.sleep(delay)
                delay *= 2
        return False

    def check(self) -> str:
        """봇 연결 확인 (getMe)."""
        if not self.token:
            raise TelegramError("TELEGRAM_BOT_TOKEN 이 설정되지 않았습니다.")
        resp = _SESSION.get(f"{API}/bot{self.token}/getMe", timeout=self.timeout)
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
