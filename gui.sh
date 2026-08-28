#!/usr/bin/env bash
# 창으로 쓰기
cd "$(dirname "$0")" || exit 1
[ -x .venv/bin/python ] || { echo "[막힘] 아직 설치가 안 됐습니다. ./setup.sh 를 먼저 실행해 주세요."; exit 1; }
exec .venv/bin/python -m paradogo "$@" gui
