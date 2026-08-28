#!/usr/bin/env python3
"""진입점.

  python run.py watch          # 상시 감시 (PC에 띄워두기)
  python run.py once --dry-run # 지금 한 번만 확인
  python run.py backtest --tune
"""

from bayesfutures.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
