#!/usr/bin/env bash
# 지금 감시가 돌고 있는지 확인
cd "$(dirname "$0")" || exit 1
[ -x .venv/bin/python ] || { echo "먼저 ./setup.sh 를 실행해 주세요."; exit 1; }
exec .venv/bin/python -m paradogo "$@" status
