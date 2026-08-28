#!/usr/bin/env bash
# 파라다이스 도고 캐빈 예약 도우미 — macOS / Linux 설치
#
# 터미널에서 이 파일이 있는 폴더로 간 뒤:  ./setup.sh
# 날짜를 바로 정하려면:                    ./setup.sh --date 2026-09-19 --nights 1
set -u

cd "$(dirname "$0")" || exit 1

say()  { printf '%s\n' "$*"; }
fail() { printf '\n[막힘] %s\n' "$*"; exit 1; }

say "=================================================="
say " 파라다이스 도고 캐빈 예약 도우미 — 설치"
say "=================================================="

# 1) 파이썬 찾기 -------------------------------------------------------------
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PY="$candidate"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  say ""
  say "파이썬 3.10 이상이 필요한데 찾지 못했습니다."
  say "  macOS : https://www.python.org/downloads/ 에서 설치하세요"
  say "  Ubuntu: sudo apt install python3 python3-venv python3-pip"
  fail "파이썬을 설치한 뒤 이 파일을 다시 실행해 주세요."
fi
say "[1/4] 파이썬     : $("$PY" --version 2>&1)"

# 2) 가상환경 ---------------------------------------------------------------
# 시스템 파이썬을 건드리지 않기 위해 이 폴더 안에만 설치한다.
if [ ! -d .venv ]; then
  say "[2/4] 가상환경   : 만드는 중…"
  "$PY" -m venv .venv || fail "가상환경을 만들지 못했습니다. (Ubuntu 라면: sudo apt install python3-venv)"
else
  say "[2/4] 가상환경   : 이미 있음"
fi

VENV_PY=".venv/bin/python"
[ -x "$VENV_PY" ] || fail "가상환경이 망가진 것 같습니다. .venv 폴더를 지우고 다시 실행해 주세요."

# 3) 필요한 것 설치 ----------------------------------------------------------
say "[3/4] 필요한 것  : 내려받는 중… (처음 한 번은 몇 분 걸립니다)"
"$VENV_PY" -m pip install --quiet --upgrade pip || fail "pip 를 갱신하지 못했습니다. 인터넷 연결을 확인해 주세요."
"$VENV_PY" -m pip install --quiet -r requirements.txt || fail "필요한 패키지를 설치하지 못했습니다. 인터넷 연결을 확인해 주세요."

if "$VENV_PY" -c "from paradogo.cli import _browser_dirs; import sys; sys.exit(0 if any(d.is_dir() for d in _browser_dirs()) else 1)" 2>/dev/null; then
  say "[4/4] 브라우저   : 이미 있음"
else
  say "[4/4] 브라우저   : 내려받는 중… (150MB 정도)"
  "$VENV_PY" -m playwright install chromium || fail "브라우저를 내려받지 못했습니다. 인터넷 연결을 확인해 주세요."
fi

say ""
say "설치 끝났습니다."
say ""

# 4) 바로 설정으로 ------------------------------------------------------------
say "=================================================="
say " 이어서 설정을 시작합니다"
say "=================================================="
say "브라우저 창이 뜨면 안내대로 클릭만 하시면 됩니다."
say ""
exec "$VENV_PY" -m paradogo "$@" start
