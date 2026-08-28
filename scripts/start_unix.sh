#!/usr/bin/env bash
# 확률 기반 선물 알림 - macOS / Linux 상시 실행
# 죽으면 10초 뒤 자동 재시작한다.
set -u
cd "$(dirname "$0")/.."

while true; do
    echo "[$(date '+%F %T')] 감시 시작"
    python3 run.py watch
    code=$?
    if [ $code -eq 130 ]; then
        echo "사용자 중단 — 종료"
        exit 0
    fi
    echo "[$(date '+%F %T')] 종료(코드 $code) — 10초 후 재시작"
    sleep 10
done
