#!/usr/bin/env python3
"""텔레그램 연결 도우미.

  1. 텔레그램에서 @BotFather 를 찾아 /newbot -> 봇 이름 정하기 -> 토큰 받기
  2. 만든 봇을 검색해서 대화창을 열고 /start 를 한 번 보내기
  3. 이 스크립트 실행 -> chat_id 확인
  4. .env 파일에 토큰과 chat_id 저장
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from bayesfutures.cli import _load_env  # noqa: E402
import os  # noqa: E402

API = "https://api.telegram.org"


def main() -> int:
    _load_env(Path(".env"))
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or input("봇 토큰을 붙여넣으세요: ").strip()
    if not token:
        print("토큰이 없습니다.")
        return 1

    me = requests.get(f"{API}/bot{token}/getMe", timeout=20)
    if me.status_code != 200:
        print(f"토큰이 유효하지 않습니다 ({me.status_code}). @BotFather 에서 다시 확인하세요.")
        return 1
    print(f"봇 확인: @{me.json()['result']['username']}")

    upd = requests.get(f"{API}/bot{token}/getUpdates", timeout=20).json().get("result", [])
    chats = {}
    for u in upd:
        chat = (u.get("message") or u.get("channel_post") or {}).get("chat")
        if chat:
            chats[chat["id"]] = chat.get("title") or chat.get("first_name") or chat.get("username", "")
    if not chats:
        print("\n아직 봇에게 온 메시지가 없습니다.")
        print("텔레그램에서 위 봇을 검색해 /start 를 보낸 뒤 다시 실행하세요.")
        return 1

    print("\n찾은 대화:")
    for cid, label in chats.items():
        print(f"  chat_id = {cid}   ({label})")

    chat_id = str(next(iter(chats)))
    env = Path(".env")
    if env.exists() and input(f"\n.env 를 덮어쓸까요? (y/N) ").strip().lower() != "y":
        print("취소했습니다. 아래 내용을 직접 .env 에 넣으세요:")
        print(f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_CHAT_ID={chat_id}")
        return 0
    env.write_text(f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_CHAT_ID={chat_id}\n", encoding="utf-8")
    print(f"\n.env 저장 완료.  이제 'python run.py telegram-test' 로 확인하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
