#!/usr/bin/env bash
# 파라다이스 도고 캐빈 예약 도우미 — macOS / Linux 한 줄 설치
#
# 터미널에 아래 한 줄을 붙여넣으세요.
#
#   curl -fsSL https://raw.githubusercontent.com/kimoonz/kimoonz/claude/paradise-dogo-cabin-booking-y5lryk/install.sh | bash
#
# 하는 일: 홈 폴더에 코드를 내려받고 setup.sh 를 실행합니다.
# (무엇을 하는지 먼저 보고 싶으면 위 주소를 브라우저로 열어 읽어 보세요)
set -u

REPO="kimoonz/kimoonz"
BRANCH="claude/paradise-dogo-cabin-booking-y5lryk"
DEST="$HOME/paradogo"

say()  { printf '%s\n' "$*"; }
fail() { printf '\n[막힘] %s\n' "$*"; exit 1; }

say "=================================================="
say " 파라다이스 도고 캐빈 예약 도우미 — 설치"
say "=================================================="
say ""

if [ -d "$DEST" ]; then
  say "이미 설치돼 있습니다: $DEST"
  say "코드만 최신으로 바꾸고, 설정과 로그인 정보는 그대로 둡니다."
  UPDATING=1
else
  say "새로 설치합니다: $DEST"
  UPDATING=0
fi
say ""

say "코드를 내려받는 중…"
tmp="$(mktemp -d)" || fail "임시 폴더를 만들지 못했습니다."
trap 'rm -rf "$tmp"' EXIT

url="https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH"
if ! curl -fsSL "$url" -o "$tmp/src.tar.gz"; then
  fail "코드를 내려받지 못했습니다. 인터넷 연결을 확인해 주세요."
fi

# 내려받은 압축에는 설정(config.yaml)도, 로그인 세션(.state)도, 파이썬 환경(.venv)도
# 들어 있지 않다. 그래서 그냥 덮어써도 쓰던 것이 지워지지 않는다.
# 폴더째 지우는 방식은 쓰지 않는다 — 그 폴더 안에서 실행하면 지울 수 없고,
# 무엇보다 설정과 로그인 정보가 날아간다.
mkdir -p "$DEST"
if ! tar -xzf "$tmp/src.tar.gz" -C "$DEST" --strip-components=1; then
  fail "압축을 푸는 데 실패했습니다."
fi

if [ "$UPDATING" = "1" ]; then
  say "최신 코드로 바꿨습니다. (설정과 로그인 정보는 그대로)"
else
  say "받았습니다: $DEST"
fi

say ""
[ -f "$DEST/setup.sh" ] || fail "설치 파일을 찾지 못했습니다: $DEST/setup.sh"
chmod +x "$DEST"/*.sh 2>/dev/null

say "이어서 준비를 시작합니다. (처음 한 번은 몇 분 걸립니다)"
say ""

cd "$DEST" || fail "폴더로 이동하지 못했습니다: $DEST"
# 이 스크립트 자체가 파이프로 실행될 수 있으므로, 마법사가 키보드를 읽도록 터미널을 연결한다.
# /dev/tty 는 파일로는 보이지만 실제로는 못 여는 환경(cron, 컨테이너)이 있어 열어서 확인한다.
if (exec < /dev/tty) 2>/dev/null; then
  exec ./setup.sh < /dev/tty
fi
exec ./setup.sh
