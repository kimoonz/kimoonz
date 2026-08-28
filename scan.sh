#!/usr/bin/env bash
# 지금 예약 가능한 날짜 보기 (읽기 전용 — 아무것도 예약하지 않습니다)
cd "$(dirname "$0")" || exit 1
[ -x .venv/bin/python ] || { echo "[막힘] 아직 설치가 안 됐습니다. ./setup.sh 를 먼저 실행해 주세요."; exit 1; }
exec .venv/bin/python -m paradogo "$@" scan
