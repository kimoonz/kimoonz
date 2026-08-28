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
  say "이미 폴더가 있습니다: $DEST"
  printf '지우고 새로 받을까요? 기존 설정과 로그인 정보는 사라집니다 (y/N): '
  read -r answer < /dev/tty || answer=""
  case "$answer" in
    [yY]*) rm -rf "$DEST" ;;
    *)     say "기존 폴더를 그대로 씁니다." ;;
  esac
fi

if [ ! -d "$DEST" ]; then
  say "코드를 내려받는 중… ($REPO)"
  tmp="$(mktemp -d)" || fail "임시 폴더를 만들지 못했습니다."
  trap 'rm -rf "$tmp"' EXIT

  url="https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH"
  if ! curl -fsSL "$url" -o "$tmp/src.tar.gz"; then
    fail "코드를 내려받지 못했습니다. 인터넷 연결을 확인해 주세요."
  fi
  # 압축을 풀면 'kimoonz-<브랜치이름>' 처럼 한 겹 더 들어가 있다.
  mkdir -p "$DEST"
  if ! tar -xzf "$tmp/src.tar.gz" -C "$DEST" --strip-components=1; then
    rm -rf "$DEST"
    fail "압축을 푸는 데 실패했습니다."
  fi
  say "받았습니다: $DEST"
fi

say ""
[ -f "$DEST/setup.sh" ] || fail "설치 파일을 찾지 못했습니다: $DEST/setup.sh"
chmod +x "$DEST/setup.sh" "$DEST/watch.sh" "$DEST/status.sh" 2>/dev/null

say "이어서 설치를 시작합니다. (처음 한 번은 몇 분 걸립니다)"
say ""

cd "$DEST" || fail "폴더로 이동하지 못했습니다: $DEST"
# 이 스크립트 자체가 파이프로 실행될 수 있으므로, 마법사가 키보드를 읽도록 터미널을 연결한다.
if [ -e /dev/tty ]; then
  exec ./setup.sh < /dev/tty
fi
exec ./setup.sh
