"""시세 데이터 로딩.

우선순위: yfinance -> Yahoo chart API 직접 호출 -> Stooq CSV(일봉만).
결과는 항상 UTC 기준 DatetimeIndex 를 가진
[open, high, low, close, volume] 데이터프레임.
"""

from __future__ import annotations

import io
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)

COLUMNS = ["open", "high", "low", "close", "volume"]
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

# Yahoo 심볼 -> Stooq 심볼
STOOQ_MAP = {
    "GC=F": "gc.f", "SI=F": "si.f", "CL=F": "cl.f", "NQ=F": "^ndx",
    "DX-Y.NYB": "dx.f", "^VIX": "^vix",
}


class DataError(RuntimeError):
    """시세를 못 가져왔을 때."""


@dataclass
class DataLoader:
    cache_dir: Path
    cache_minutes: int = 30
    source: str = "auto"          # auto | yahoo | stooq | cache
    session: requests.Session | None = None

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if self.session is None:
            self.session = requests.Session()
            self.session.headers.update(_UA)

    # ------------------------------------------------------------------ 공개 API
    def get(self, symbol: str, interval: str = "1d", lookback_days: int = 3650,
            allow_cache_fallback: bool = True) -> pd.DataFrame:
        """시세를 가져온다. 신선한 캐시가 있으면 그대로 쓴다."""
        cache = self._cache_path(symbol, interval)
        if self._cache_fresh(cache):
            df = self._read_cache(cache)
            if df is not None and len(df):
                return df

        errors: list[str] = []
        for fetch in self._fetchers(interval):
            try:
                df = fetch(symbol, interval, lookback_days)
                if df is not None and len(df) > 0:
                    self._write_cache(cache, df)
                    return df
                errors.append(f"{fetch.__name__}: 빈 응답")
            except Exception as exc:  # 소스 하나가 죽어도 다음 소스로
                errors.append(f"{fetch.__name__}: {exc}")
                log.debug("%s 실패 (%s): %s", symbol, fetch.__name__, exc)

        if allow_cache_fallback:
            df = self._read_cache(cache)
            if df is not None and len(df):
                log.warning("%s: 신규 시세 실패, 오래된 캐시 사용 (%s)", symbol, "; ".join(errors))
                return df
        raise DataError(f"{symbol} {interval} 시세 실패 -> " + "; ".join(errors))

    def _fetchers(self, interval: str):
        if self.source == "yahoo":
            return [self._fetch_yfinance, self._fetch_yahoo_http]
        if self.source == "stooq":
            return [self._fetch_stooq]
        fetchers = [self._fetch_yfinance, self._fetch_yahoo_http]
        if interval in ("1d", "1wk"):
            fetchers.append(self._fetch_stooq)   # stooq는 일봉 이상만 제공
        return fetchers

    # ------------------------------------------------------------------ 소스별 구현
    def _fetch_yfinance(self, symbol: str, interval: str, lookback_days: int) -> pd.DataFrame:
        import yfinance as yf  # 선택적 의존성: 없으면 다음 소스로

        period_days = min(lookback_days, 729) if interval.endswith(("m", "h")) else lookback_days
        start = datetime.now(timezone.utc) - timedelta(days=period_days)
        raw = yf.download(
            symbol, start=start.date().isoformat(), interval=interval,
            auto_adjust=False, progress=False, threads=False,
        )
        return _normalize(raw)

    def _fetch_yahoo_http(self, symbol: str, interval: str, lookback_days: int) -> pd.DataFrame:
        period_days = min(lookback_days, 729) if interval.endswith(("m", "h")) else lookback_days
        now = int(time.time())
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(symbol)}"
        params = {
            "period1": now - period_days * 86400, "period2": now,
            "interval": interval, "includePrePost": "false", "events": "div,splits",
        }
        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        result = (payload.get("chart") or {}).get("result")
        if not result:
            err = (payload.get("chart") or {}).get("error")
            raise DataError(f"yahoo 응답에 데이터 없음: {err}")
        node = result[0]
        quote = node["indicators"]["quote"][0]
        df = pd.DataFrame(
            {
                "open": quote.get("open"), "high": quote.get("high"),
                "low": quote.get("low"), "close": quote.get("close"),
                "volume": quote.get("volume"),
            },
            index=pd.to_datetime(node["timestamp"], unit="s", utc=True),
        )
        return _normalize(df)

    def _fetch_stooq(self, symbol: str, interval: str, lookback_days: int) -> pd.DataFrame:
        if interval not in ("1d", "1wk"):
            raise DataError("stooq는 일봉/주봉만 지원")
        code = STOOQ_MAP.get(symbol, symbol.lower().replace("=f", ".f"))
        url = "https://stooq.com/q/d/l/"
        resp = self.session.get(
            url, params={"s": code, "i": "d" if interval == "1d" else "w"}, timeout=30
        )
        resp.raise_for_status()
        text = resp.text
        if not text.startswith("Date") or "<html" in text[:200].lower():
            raise DataError("stooq가 CSV 대신 차단 페이지를 반환")
        df = pd.read_csv(io.StringIO(text))
        df.columns = [c.strip().lower() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df = df.set_index("date")
        if "volume" not in df:
            df["volume"] = np.nan
        return _normalize(df)

    # ------------------------------------------------------------------ 캐시
    def _cache_path(self, symbol: str, interval: str) -> Path:
        safe = symbol.replace("=", "_").replace("^", "idx_").replace("/", "_")
        return self.cache_dir / f"{safe}__{interval}.csv"

    def _cache_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        age_min = (time.time() - path.stat().st_mtime) / 60.0
        return age_min < self.cache_minutes

    def _read_cache(self, path: Path) -> pd.DataFrame | None:
        if not path.exists():
            return None
        try:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            df.index = pd.to_datetime(df.index, utc=True)
            return df
        except Exception:
            return None

    def _write_cache(self, path: Path, df: pd.DataFrame) -> None:
        try:
            df.to_csv(path)
        except OSError as exc:
            log.warning("캐시 저장 실패 %s: %s", path, exc)


def _normalize(raw: pd.DataFrame) -> pd.DataFrame:
    """열 이름/타입/정렬을 통일하고 결측 봉을 제거."""
    if raw is None or len(raw) == 0:
        raise DataError("빈 데이터프레임")
    df = raw.copy()

    if isinstance(df.columns, pd.MultiIndex):        # yfinance 다중 심볼 형태
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    if "adj_close" in df.columns and "close" not in df.columns:
        df["close"] = df["adj_close"]

    missing = [c for c in ("open", "high", "low", "close") if c not in df.columns]
    if missing:
        raise DataError(f"필수 열 없음: {missing} (받은 열: {list(df.columns)})")
    if "volume" not in df.columns:
        df["volume"] = np.nan

    df = df[COLUMNS].astype(float)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["open", "high", "low", "close"])
    # 명백히 깨진 봉 제거
    df = df[(df["high"] >= df["low"]) & (df["close"] > 0)]
    if len(df) == 0:
        raise DataError("정제 후 남은 봉이 없음")
    return df


def synthetic(symbol: str = "SYN", bars: int = 3000, interval: str = "1d",
              seed: int = 7, start_price: float = 2000.0) -> pd.DataFrame:
    """오프라인 테스트용 합성 시세.

    추세 지속성(자기상관)과 변동성 군집(GARCH 유사)을 넣어서
    모델이 실제로 잡아낼 신호가 존재하도록 만든다.
    """
    rng = np.random.default_rng(seed)
    mu = np.zeros(bars)
    vol = np.zeros(bars)
    vol[0] = 0.01
    drift = 0.0
    rets = np.zeros(bars)
    for t in range(1, bars):
        drift = 0.95 * drift + rng.normal(0, 0.0012)          # 느린 추세 성분
        vol[t] = np.sqrt(0.000004 + 0.86 * vol[t - 1] ** 2 + 0.10 * rets[t - 1] ** 2)
        rets[t] = drift + rng.normal(0, vol[t])
        mu[t] = drift
    close = start_price * np.exp(np.cumsum(rets))
    freq = "1D" if interval == "1d" else "1h"
    idx = pd.date_range(end=datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0),
                        periods=bars, freq=freq, tz="UTC")
    noise = np.abs(rng.normal(0, 1, bars)) * vol * close
    high = close + noise
    low = close - np.abs(rng.normal(0, 1, bars)) * vol * close
    open_ = np.concatenate([[start_price], close[:-1]]) * (1 + rng.normal(0, 0.0005, bars))
    high = np.maximum.reduce([high, open_, close])
    low = np.minimum.reduce([low, open_, close])
    volume = rng.lognormal(11, 0.4, bars)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
