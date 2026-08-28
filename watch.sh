#!/usr/bin/env bash
# 취소표 감시 시작 — 멈춰도 알아서 다시 뜹니다. 끄려면 Ctrl+C.
cd "$(dirname "$0")" || exit 1
[ -x .venv/bin/python ] || { echo "먼저 ./setup.sh 를 실행해 주세요."; exit 1; }
exec .venv/bin/python -m paradogo "$@" track --forever
