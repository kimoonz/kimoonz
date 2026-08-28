"""삼중 배리어(triple barrier) 라벨링.

각 봉 t 에서:
    상단 = 종가 + up_mult * ATR(t)
    하단 = 종가 - down_mult * ATR(t)
앞으로 horizon 봉 안에 어느 쪽을 먼저 건드리는지 본다.
    y = 1  상단 먼저 (롱이 이김)
    y = 0  하단 먼저 (숏이 이김)
    둘 다 안 닿으면 만기 종가의 방향으로 판정 (timeout=True 로 표시)

한 봉 안에서 고가/저가가 모두 배리어를 넘긴 경우는 순서를 알 수 없으므로
보수적으로 '하단 먼저'로 본다 (롱 기준 최악의 경우).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .features import atr


def triple_barrier(
    df: pd.DataFrame,
    atr_window: int = 14,
    up_mult: float = 1.0,
    down_mult: float = 1.0,
    horizon: int = 10,
) -> pd.DataFrame:
    """라벨 테이블 반환: y, timeout, bars_held, upper, lower, atr."""
    a = atr(df, atr_window)
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    a_arr = a.to_numpy(dtype=float)
    n = len(df)

    y = np.full(n, np.nan)
    timeout = np.zeros(n, dtype=bool)
    held = np.full(n, np.nan)
    upper = close + up_mult * a_arr
    lower = close - down_mult * a_arr

    for t in range(n):
        if not np.isfinite(a_arr[t]) or a_arr[t] <= 0:
            continue
        end = min(t + horizon, n - 1)
        if end <= t:
            continue
        up, dn = upper[t], lower[t]
        hit = 0
        for k in range(t + 1, end + 1):
            touched_up = high[k] >= up
            touched_dn = low[k] <= dn
            if touched_up and touched_dn:
                hit = -1          # 같은 봉에서 양쪽 -> 보수적으로 손절 먼저
            elif touched_up:
                hit = 1
            elif touched_dn:
                hit = -1
            if hit:
                y[t] = 1.0 if hit == 1 else 0.0
                held[t] = k - t
                break
        if hit == 0:
            y[t] = 1.0 if close[end] > close[t] else 0.0
            timeout[t] = True
            held[t] = end - t

    return pd.DataFrame(
        {"y": y, "timeout": timeout, "bars_held": held,
         "upper": upper, "lower": lower, "atr": a_arr},
        index=df.index,
    )
