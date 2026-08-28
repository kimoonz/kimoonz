#!/usr/bin/env python3
"""텔레그램 연결 도우미.

봇 토큰은 TELEGRAM_BOT_TOKEN 환경변수에서 읽습니다. 이 폴더가 깃이나
클라우드 드라이브로 동기화돼도 토큰이 따라가지 않게 하려는 것입니다.

  Windows (PowerShell, 한 번만):
    [Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN", "<토큰>", "User")
    (설정 후 새 터미널을 열어야 반영됩니다)

이미 다른 자동화에서 이 변수를 쓰고 있다면 그대로 재사용됩니다.

받을 방 정하기
  개인 대화로 받으려면 봇을 검색해 /start 를 보낸 뒤 실행하세요.
  전용 그룹으로 받으려면(권장):
    1. 텔레그램에서 새 그룹을 만든다
    2. 봇을 그룹에 초대한다
    3. 그룹에서 "/start@봇이름" 을 보낸다
       (그룹 안의 봇은 privacy mode 라 '/' 로 시작하는 메시지만 봅니다)
    4. 이 스크립트를 실행한다

  python scripts/setup_telegram.py            # 방이 하나면 자동 선택
  python scripts/setup_telegram.py <chat_id>  # 여러 개면 직접 지정
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from bayesfutures.cli import _load_env  # noqa: E402
from bayesfutures.config import load_config  # noqa: E402

API = "https://api.telegram.org"


def api(token: str, method: str, **params):
    try:
        return requests.get(f"{API}/bot{token}/{method}", params=params, timeout=30).json()
    except requests.RequestException as exc:
        return {"ok": False, "description": f"요청 실패: {exc}"}


def main() -> int:
    _load_env(Path(".env"))
    cfg = load_config()

    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN 환경변수가 없습니다.\n")
        print("@BotFather 에서 /newbot (또는 /mybots -> API Token) 으로 받은 뒤:")
        print('  Windows: [Environment]::SetEnvironmentVariable('
              '"TELEGRAM_BOT_TOKEN", "<토큰>", "User")')
        print('  macOS/Linux: export TELEGRAM_BOT_TOKEN="<토큰>"')
        return 1

    me = api(token, "getMe")
    if not me.get("ok"):
        print(f"토큰이 거부됐습니다: {me.get('description')}")
        print("@BotFather 에서 /mybots -> API Token 으로 다시 확인하세요.")
        return 1
    bot = me["result"]
    print(f"봇 확인: @{bot.get('username')} ({bot.get('first_name')})")

    updates = api(token, "getUpdates", timeout=0)
    if not updates.get("ok"):
        print(f"getUpdates 실패: {updates.get('description')}")
        print("웹훅이 걸려 있으면 getUpdates 가 아무것도 반환하지 않습니다.")
        return 1

    # 그룹에 초대되면 message 없이 my_chat_member 만 오는 경우가 있어 함께 본다.
    chats: dict = {}
    for upd in updates.get("result", []):
        for key in ("message", "channel_post", "edited_message",
                    "my_chat_member", "chat_member"):
            payload = upd.get(key)
            chat = (payload or {}).get("chat")
            if chat and chat.get("id") is not None:
                chats[chat["id"]] = chat

    if not chats:
        print("\n아직 이 봇과의 대화가 없습니다.")
        print(f"  개인: https://t.me/{bot.get('username')} 에서 /start")
        print(f"  그룹: 새 그룹 생성 -> @{bot.get('username')} 초대 -> "
              f"'/start@{bot.get('username')}' 전송")
        print("그 뒤 이 스크립트를 다시 실행하세요.")
        return 1

    chat_list = list(chats.values())
    print("\n이 봇과 대화가 있는 채팅방:")
    for i, chat in enumerate(chat_list):
        label = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
        print(f"  [{i}] {label}  id={chat['id']}  ({chat.get('type')})")

    wanted = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    if wanted:
        chosen = next((c for c in chat_list if str(c["id"]) == wanted), None)
        if chosen is None:
            print(f"\n지정한 chat id 를 찾을 수 없습니다: {wanted}")
            return 1
    elif len(chat_list) == 1:
        chosen = chat_list[0]
    else:
        groups = [c for c in chat_list
                  if c.get("type") in ("group", "supergroup", "channel")]
        if len(groups) == 1:
            chosen = groups[0]
            print(f"\n그룹이 하나뿐이라 이걸로 정합니다: {chosen.get('title')}")
        else:
            print("\n여러 방이 있습니다. chat id 를 인자로 주세요:")
            print("  python scripts/setup_telegram.py <chat_id>")
            return 1

    chat_id = str(chosen["id"])
    label = chosen.get("title") or chosen.get("first_name") or chosen.get("username") or ""
    cfg.save_chat_id(chat_id)
    print(f"\n저장 완료: {label} id={chat_id} -> {cfg.chat_id_file}")
    print("\n환경변수로 고정하고 싶다면 (다른 자동화와 섞이지 않게):")
    print(f'  Windows: [Environment]::SetEnvironmentVariable('
          f'"TELEGRAM_CHAT_ID_FUTURES", "{chat_id}", "User")')
    print(f'  macOS/Linux: export TELEGRAM_CHAT_ID_FUTURES="{chat_id}"')

    from bayesfutures.telegram import Telegram
    tg = Telegram(token, chat_id, on_chat_migrated=cfg.save_chat_id)
    if tg.send("✅ <b>연결 완료</b>\n이제 해외선물 매매 신호가 여기로 옵니다."):
        print("\n테스트 메시지 발송 성공. 설정 끝.")
        return 0
    print("\n테스트 메시지 실패 — 위 오류를 확인하세요.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
