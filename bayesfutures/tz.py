"""시간대 조회.

Windows 에는 시스템 시간대 데이터베이스가 없다. 리눅스·macOS 는
/usr/share/zoneinfo 를 쓰지만 Windows 는 그게 없어서 tzdata 패키지가
설치돼 있지 않으면 ZoneInfo("Asia/Seoul") 이 그대로 터진다.

이 모듈들은 import 시점에 시간대를 만들기 때문에 실패하면 프로그램이
첫 줄부터 죽는다. 그래서 원인과 해결책이 바로 보이는 에러로 바꾼다.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_MESSAGE = (
    "시간대 데이터를 찾을 수 없습니다: {key}\n"
    "Windows 에는 시스템 시간대 DB가 없어 tzdata 패키지가 필요합니다.\n"
    "  pip install tzdata\n"
    "(requirements.txt 에 들어 있으니 'pip install -r requirements.txt' 로도 됩니다)"
)


def zone(key: str) -> ZoneInfo:
    try:
        return ZoneInfo(key)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(_MESSAGE.format(key=key)) from exc
