"""증거(evidence) 피처 계산.

모든 값은 봉 t 마감 시점에 알 수 있는 정보만 사용한다 (look-ahead 없음).
가격 단위 피처는 ATR로 나눠서 종목/시대에 상관없이 비교 가능하게 만든다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [df["high"] - df["low"],
         (df["high"] - prev_close).abs(),
         (df["low"] - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Wilder ATR."""
    return true_range(df).ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder RSI.

    하락이 하나도 없는 구간(avg_loss=0)은 100, 상승이 없는 구간은 0이다.
    이걸 중립 50으로 채우면 강한 추세 구간이 '보통'으로 위장되어
    모델이 과열 신호를 놓친다. 워밍업 구간은 NaN 으로 남겨
    베이즈 엔진이 '증거 없음'으로 처리하게 한다.
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()

    out = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss.where(avg_loss > 0))
    warm = avg_gain.notna() & avg_loss.notna()
    out = out.mask(warm & (avg_loss <= 0) & (avg_gain > 0), 100.0)
    out = out.mask(warm & (avg_gain <= 0) & (avg_loss > 0), 0.0)
    out = out.mask(warm & (avg_gain <= 0) & (avg_loss <= 0), 50.0)
    return out


def _run_length(close: pd.Series, cap: int = 5) -> pd.Series:
    """같은 방향으로 연속 마감한 봉 수 (부호 포함, ±cap 로 절단)."""
    sign = np.sign(close.diff().fillna(0.0)).to_numpy()
    out = np.zeros(len(sign))
    run = 0.0
    for i, s in enumerate(sign):
        if s == 0:
            run = 0.0
        elif np.sign(run) == s:
            run += s
        else:
            run = s
        out[i] = np.clip(run, -cap, cap)
    return pd.Series(out, index=close.index)


def build_features(
    df: pd.DataFrame,
    atr_window: int = 14,
    extras: dict[str, pd.DataFrame] | None = None,
    intraday: bool = False,
) -> pd.DataFrame:
    """OHLCV -> 피처 테이블."""
    close, high, low, open_ = df["close"], df["high"], df["low"], df["open"]
    a = atr(df, atr_window)
    a_safe = a.replace(0.0, np.nan)

    feats: dict[str, pd.Series] = {}

    # --- 추세 / 모멘텀 -------------------------------------------------
    feats["mom_20"] = (close - close.shift(20)) / (a_safe * np.sqrt(20))
    feats["mom_5"] = (close - close.shift(5)) / (a_safe * np.sqrt(5))
    # 장기 시계열 모멘텀 — 선물에서 그나마 근거가 쌓인 이상현상
    for span in (60, 120, 250):
        feats[f"mom_{span}"] = (close - close.shift(span)) / (a_safe * np.sqrt(span))
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    feats["dist_ma50"] = (close - ema50) / a_safe
    feats["ma_slope"] = (ema20 - ema20.shift(5)) / a_safe
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    feats["macd_hist"] = (macd - macd.ewm(span=9, adjust=False).mean()) / a_safe

    # --- 과열 / 되돌림 -------------------------------------------------
    feats["rsi_14"] = rsi(close, 14)
    hi20 = high.rolling(20).max()
    lo20 = low.rolling(20).min()
    feats["range_pos"] = (close - lo20) / (hi20 - lo20).replace(0.0, np.nan)
    feats["consec"] = _run_length(close)

    # --- 변동성 국면 ---------------------------------------------------
    atr_slow = true_range(df).ewm(alpha=1.0 / 50, adjust=False, min_periods=50).mean()
    feats["atr_ratio"] = a / atr_slow.replace(0.0, np.nan)
    sd20 = close.rolling(20).std()
    bb_width = (4.0 * sd20) / close
    feats["bb_width_pct"] = bb_width.rolling(250, min_periods=60).rank(pct=True)

    # --- 미시 구조 -----------------------------------------------------
    feats["gap"] = (open_ - close.shift(1)) / a_safe
    logvol = np.log(df["volume"].replace(0.0, np.nan))
    vol_mean = logvol.rolling(20).mean()
    vol_std = logvol.rolling(20).std().replace(0.0, np.nan)
    feats["volume_z"] = (logvol - vol_mean) / vol_std

    # --- 계절성 --------------------------------------------------------
    idx = df.index
    if intraday:
        feats["seasonal"] = pd.Series(idx.hour.astype(float), index=idx)
    else:
        feats["seasonal"] = pd.Series(idx.dayofweek.astype(float), index=idx)

    # --- 상관 자산 (달러, VIX 등) ---------------------------------------
    for symbol, ext in (extras or {}).items():
        if ext is None or len(ext) < 60:
            continue
        ext_close = ext["close"].reindex(df.index, method="ffill")
        ret = np.log(ext_close).diff()
        window = 10
        vol = ret.rolling(60).std().replace(0.0, np.nan)
        name = "x_" + symbol.lower().replace("=f", "").replace("^", "").replace("-", "_").replace(".", "_")
        feats[name] = (np.log(ext_close) - np.log(ext_close.shift(window))) / (vol * np.sqrt(window))

    out = pd.DataFrame(feats, index=df.index)
    out = out.replace([np.inf, -np.inf], np.nan)
    return out


FEATURE_LABELS_KO = {
    "mom_20": "20봉 모멘텀",
    "mom_60": "60봉 모멘텀",
    "mom_120": "120봉 모멘텀",
    "mom_250": "250봉 장기 모멘텀",
    "mom_5": "5봉 단기 흐름",
    "dist_ma50": "50선 이격",
    "ma_slope": "20선 기울기",
    "macd_hist": "MACD 히스토그램",
    "rsi_14": "RSI(14)",
    "range_pos": "20봉 레인지 위치",
    "consec": "연속 양/음봉",
    "atr_ratio": "변동성 확대/축소",
    "bb_width_pct": "볼밴 폭 백분위",
    "gap": "갭",
    "volume_z": "거래량 이상치",
    "seasonal": "요일/시간대",
    "x_gc": "금 흐름",
    "x_dx_y_nyb": "달러지수 흐름",
    "x_vix": "VIX 흐름",
    "regime": "시장 국면",
}


def label_ko(name: str) -> str:
    return FEATURE_LABELS_KO.get(name, name)
