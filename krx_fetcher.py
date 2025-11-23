"""Improved KRX metric collector.

This module refactors the provided single-file script into a reusable, testable
component with clearer separation of concerns. The main changes are:

- All configuration is encapsulated in ``FetchSettings`` so callers can easily
  override paths, timeouts, and concurrency.
- Network access is centralized in ``SessionManager`` with consistent retry and
  timeout behavior across threads.
- Parsing logic is split into focused helpers (metadata parsing vs. time-series
  extraction) to simplify maintenance and unit testing.
- Graceful shutdown is handled by ``PartialResultStore`` instead of calling
  ``os._exit`` from signal handlers.
- CSV/Excel export is isolated from computation to make the fetcher usable as a
  library as well as a script.

The module can still be executed directly, but the ``KRXFetcher`` class is the
primary entry point for programmatic usage.
"""

from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import platform
import random
import re
import signal
import threading
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pykrx import stock


# --------------------------------------------------------------------------------------
# Configuration objects
# --------------------------------------------------------------------------------------


def _default_desktop() -> Path:
    """Return the best-effort desktop path for the running platform."""
    home = Path.home()
    desktop = home / "Desktop"
    return desktop if desktop.is_dir() else home


@dataclass
class FetchSettings:
    base_dir: Path = field(default_factory=Path.cwd)
    log_dir: Path = field(default_factory=lambda: Path.cwd() / "logs")
    output_dir: Path = field(default_factory=_default_desktop)

    max_workers: int = 10
    retries: int = 1
    sleep_between_rounds: float = 0.5
    request_connect_timeout: int = 3
    request_read_timeout: int = 6
    global_deadline_sec: int = 6
    pool_connections: int = 16
    pool_maxsize: int = 32

    show_progress_every: int = 100
    print_line_per_ticker: bool = True
    print_raw_per_ticker: bool = False
    print_whole_dataframe: bool = False

    annual_years: List[int] = field(default_factory=lambda: list(range(2019, 2026)))
    quarter_years: List[int] = field(default_factory=lambda: list(range(2023, 2026)))
    quarters: List[int] = field(default_factory=lambda: [1, 2, 3, 4])

    def __post_init__(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------------------
# Logging helpers
# --------------------------------------------------------------------------------------


def build_logger(settings: FetchSettings) -> logging.Logger:
    """Create a rotating-file logger for the fetcher."""
    logger = logging.getLogger("krx_refactor")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    log_file = settings.log_dir / f"run_{datetime.now():%Y%m%d_%H%M%S}.log"
    handler = RotatingFileHandler(log_file, maxBytes=20_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.info("Logger initialized: %s", log_file)
    return logger


# --------------------------------------------------------------------------------------
# Networking utilities
# --------------------------------------------------------------------------------------


class SessionManager:
    """Thread-local ``requests.Session`` factory with retry and pooling."""

    def __init__(self, settings: FetchSettings):
        self.settings = settings
        self._local = threading.local()
        self._headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Connection": "close",
            "Cache-Control": "no-cache",
        }

    def _build_session(self) -> requests.Session:
        retries = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.4,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(
            max_retries=retries,
            pool_connections=self.settings.pool_connections,
            pool_maxsize=self.settings.pool_maxsize,
        )

        session = requests.Session()
        session.headers.update(self._headers)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def get(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._build_session()
            self._local.session = session
        return session

    def fetch_html(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        max_tries: int = 2,
    ) -> str:
        """Fetch HTML content with retries and charset detection."""
        end_at = time.monotonic() + float(self.settings.global_deadline_sec)
        last_err: Optional[Exception] = None
        timeout = (self.settings.request_connect_timeout, self.settings.request_read_timeout)
        session = self.get()
        for attempt in range(1, max_tries + 1):
            if time.monotonic() >= end_at:
                if last_err:
                    raise last_err
                raise requests.exceptions.ReadTimeout("global deadline exceeded")
            try:
                resp = session.get(url, params=params, headers=headers or self._headers, timeout=timeout)
                raw = resp.content
                encodings = []
                m = re.search(rb"charset=[\"']?([a-zA-Z0-9_-]+)", raw[:2400], re.I)
                if m:
                    encodings.append(m.group(1).decode("ascii", "ignore"))
                if resp.encoding:
                    encodings.append(resp.encoding)
                encodings += ["utf-8", "cp949", "euc-kr", "latin1"]
                for enc in encodings:
                    try:
                        return raw.decode(enc)
                    except Exception:
                        continue
                return raw.decode("utf-8", "ignore")
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ReadTimeout,
                requests.exceptions.SSLError,
            ) as exc:  # retryable
                last_err = exc
                if attempt == max_tries:
                    raise
                time.sleep(0.3 * attempt + random.uniform(0, 0.2))


# --------------------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------------------


def _extract_numeric(text: Optional[str]) -> Optional[float]:
    cleaned = re.sub(r"[^\d\.-]", "", text or "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _short_name(name: str, width: int = 12) -> str:
    return name if len(name) <= width else f"{name[: width - 1]}…"


FIELD_PATTERNS = {
    "52주베타": [r"52\s*주\s*베타"],
    "시가총액(억원)": [r"시가\s*총액"],
    "외국인지분율": [r"외국인\s*지분율"],
    "현금배당수익률": [r"현금\s*배당\s*수익률"],
    "EPS": [r"\bEPS\b", r"EPS\(원\)"],
    "BPS": [r"\bBPS\b", r"BPS\(원\)"],
    "PER": [r"\bPER\b"],
    "업종PER": [r"업종\s*PER"],
    "PBR": [r"\bPBR\b"],
    "WICS": [r"\bWICS\b"],
}

REGEX_FALLBACK = {
    "업종PER": r"업종PER\s*[:：]?\s*([\d\.]+)",
    "WICS": r"WICS\s*[:：]?\s*([\w\d&\(\)가-힣]+)",
    "영업이익률(%)": r"영업\s*이익\s*률\s*[:：]?\s*([\d\.\-]+)",
}

ROW_PAT_OM = re.compile(r"영업\s*이익\s*[률율]", re.I)
ROW_PAT_CASH_DY = re.compile(r"현금\s*배당\s*수익률", re.I)
ROW_PAT_ROE = re.compile(r"\bROE\b|자기\s*자본\s*이익\s*률", re.I)

YEAR_MONTH_RE = re.compile(r"(\d{4})\s*[./-]\s*(\d{1,2})")
YEAR_Q_RE1 = re.compile(r"(\d{4})\s*\.?\s*[Qq]\s*([1-4])")
YEAR_Q_RE2 = re.compile(r"(\d{4})\s*년\s*([1-4])\s*분기")
YEAR_Q_RE3 = re.compile(r"(\d{4}).{0,6}([1-4])\s*/\s*4")
YEAR_Q_RE4 = re.compile(r"(\d{4})\s*([1-4])\s*[Qq]")
YEAR_ONLY_RE = re.compile(r"^\s*(\d{4})\s*(?:년)?\s*$")


# --------------------------------------------------------------------------------------
# Data containers
# --------------------------------------------------------------------------------------


@dataclass
class MetricSeries:
    annual: Dict[int, float] = field(default_factory=dict)
    quarterly: Dict[str, float] = field(default_factory=dict)
    latest_annual: Optional[float] = None
    latest_quarterly: Optional[float] = None


@dataclass
class FetchResult:
    name: str
    code: str
    metrics: Dict[str, Any]
    series: Dict[str, MetricSeries]
    error: Optional[str] = None


# --------------------------------------------------------------------------------------
# Partial result store for graceful shutdown
# --------------------------------------------------------------------------------------


class PartialResultStore:
    def __init__(self) -> None:
        self._rows: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def add(self, row: Dict[str, Any]) -> None:
        with self._lock:
            self._rows.append(row)

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._rows)


# --------------------------------------------------------------------------------------
# Core fetcher
# --------------------------------------------------------------------------------------


class KRXFetcher:
    def __init__(self, settings: Optional[FetchSettings] = None, logger: Optional[logging.Logger] = None):
        self.settings = settings or FetchSettings()
        self.logger = logger or build_logger(self.settings)
        self.session_manager = SessionManager(self.settings)
        self.partial_store = PartialResultStore()
        self._install_signal_handlers()

    # ----------------------------- signal handling ---------------------------------
    def _install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            self.logger.warning("Signal %s received; partial results will be kept in memory only.", signum)
        try:
            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)
        except Exception:
            self.logger.debug("Signal handlers could not be registered on this platform.")

    # ----------------------------- parsing utilities --------------------------------
    def _parse_coinfo_main(self, soup: BeautifulSoup) -> Dict[str, Any]:
        result: Dict[str, Any] = {"현재가(원)": None, "업종PER": None, "WICS": None}
        no_today = soup.select_one("p.no_today")
        if no_today:
            span = no_today.select_one("span.blind")
            if span:
                result["현재가(원)"] = _extract_numeric(span.get_text(strip=True))
        em_cper = soup.find("em", id="_cper")
        if em_cper:
            result["업종PER"] = _extract_numeric(em_cper.get_text(strip=True))
        full_text = soup.get_text(" ", strip=True)
        if result["업종PER"] is None:
            result["업종PER"] = self._fallback_extract(full_text, "업종PER")
        if result["WICS"] is None:
            result["WICS"] = self._fallback_extract(full_text, "WICS")
        return result

    def _parse_coinfo_iframe(self, iframe_soup: BeautifulSoup) -> Dict[str, Any]:
        result = {k: None for k in ["52주베타", "시가총액(억원)", "외국인지분율", "현금배당수익률", "EPS", "BPS", "PER", "업종PER", "PBR", "WICS"]}
        for table in iframe_soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["th", "td"])
                if not cells:
                    continue
                row_texts = [c.get_text(strip=True) for c in cells]
                for field, patterns in FIELD_PATTERNS.items():
                    if result.get(field) is not None:
                        continue
                    for pat in patterns:
                        if any(re.search(pat, t) for t in row_texts):
                            match_idx = next((idx for idx, cell_text in enumerate(row_texts) if re.search(pat, cell_text)), None)
                            if match_idx is None:
                                break
                            if field == "WICS":
                                result[field] = row_texts[match_idx + 1] if match_idx + 1 < len(row_texts) else None
                            else:
                                cand = None
                                for j in range(len(row_texts) - 1, match_idx, -1):
                                    cand = _extract_numeric(row_texts[j])
                                    if cand is not None:
                                        break
                                result[field] = cand
                            break
        iframe_text = iframe_soup.get_text(" ", strip=True)
        if result["업종PER"] is None:
            result["업종PER"] = self._fallback_extract(iframe_text, "업종PER")
        if result["WICS"] is None:
            result["WICS"] = self._fallback_extract(iframe_text, "WICS")
        return result

    def _fallback_extract(self, text: str, field: str) -> Optional[Any]:
        pattern = REGEX_FALLBACK.get(field)
        if not pattern:
            return None
        match = re.search(pattern, text or "")
        if not match:
            return None
        value = match.group(1)
        if field == "WICS":
            return value
        try:
            return float(value)
        except ValueError:
            return None

    # ----------------------------- series helpers -----------------------------------
    def _parse_col_to_year_month(self, label: Any) -> Optional[Tuple[int, int]]:
        s = str(label)
        m = YEAR_MONTH_RE.search(s)
        if m:
            y, mm = int(m.group(1)), int(m.group(2))
            if 1 <= mm <= 12:
                return y, mm
        for rx in (YEAR_Q_RE1, YEAR_Q_RE2, YEAR_Q_RE3, YEAR_Q_RE4):
            m = rx.search(s)
            if m:
                y = int(m.group(1))
                q = int(m.group(2))
                return y, q * 3
        m = YEAR_ONLY_RE.match(s)
        if m:
            return int(m.group(1)), 12
        return None

    def _table_signature(self, df: pd.DataFrame) -> Tuple[set, set]:
        months = []
        years = set()
        cols = list(df.columns)
        if not cols:
            return set(), set()
        for c in cols[1:]:
            label = c if not isinstance(c, tuple) else c[-1]
            ym = self._parse_col_to_year_month(label)
            if ym:
                y, m = ym
                years.add(y)
                months.append(m)
        return set(months), years

    def _filter_by_top_header(self, df: pd.DataFrame, want_type: str) -> pd.DataFrame:
        if not isinstance(df.columns, pd.MultiIndex):
            return df
        keep = [df.columns[0]]
        upper = "분기" if want_type == "Q" else "연간"
        for col in df.columns[1:]:
            top = str(col[0]) if isinstance(col, tuple) else ""
            if upper in top:
                keep.append(col)
        if len(keep) == 1:
            return df
        return df.loc[:, keep]

    def _flatten_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        if isinstance(df.columns, pd.MultiIndex):
            new_cols = []
            for tpl in df.columns:
                parts = [str(x).strip() for x in tpl if str(x).strip() not in ("", "nan", "NaN", "None")]
                new_cols.append(" ".join(parts))
            df = df.copy()
            df.columns = new_cols
        else:
            df = df.copy()
            df.columns = [str(c).strip() for c in df.columns]
        return df

    def _collect_series_points(self, df: pd.DataFrame, want_type: str, row_pat: re.Pattern) -> List[Tuple[int, int, float]]:
        months_sig, _ = self._table_signature(df)
        if want_type == "A":
            if len(months_sig) != 1:
                return []
            fiscal_month = next(iter(months_sig))
        else:
            if len(months_sig) <= 1:
                return []
            fiscal_month = None

        df2 = self._filter_by_top_header(df, want_type)
        df2 = self._flatten_columns(df2)
        if df2.empty:
            return []

        col0 = df2.columns[0]
        if not df2[col0].astype(str).str.contains(row_pat).any():
            return []
        df2 = df2.set_index(col0)
        row_label = next((idx for idx in df2.index if row_pat.search(str(idx))), None)
        if row_label is None:
            return []

        s = df2.loc[row_label]
        pts = []
        for col in s.index:
            ym = self._parse_col_to_year_month(col)
            if not ym:
                continue
            y, m = ym
            if want_type == "A" and m != fiscal_month:
                continue
            v = _extract_numeric(str(s[col]))
            if v is None:
                continue
            pts.append((y, m, v))
        return pts

    def _read_html_tables(self, html: str) -> List[pd.DataFrame]:
        try:
            return pd.read_html(StringIO(html))
        except ValueError:
            return []

    def _get_wise_enc_and_id(self, stock_code: str) -> Tuple[Optional[str], Optional[str]]:
        url = f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={stock_code}"
        html = self.session_manager.fetch_html(url)
        m_enc = re.search(r"encparam\s*:\s*'([^']+)'", html, re.I)
        m_id = re.search(r"\bid\s*:\s*'([^']+)'", html, re.I)
        return (m_enc.group(1) if m_enc else None, m_id.group(1) if m_id else None)

    def _fetch_series_points_anyway(self, stock_code: str, encparam: str, encid: str, want_type: str, row_pat: re.Pattern) -> List[Tuple[int, int, float]]:
        endpoints = [
            "https://companyinfo.stock.naver.com/v1/company/ajax/cF1001.aspx",
            "https://navercomp.wisereport.co.kr/v2/company/ajax/cF1001.aspx",
        ]
        freq_tries = [want_type, ("Q" if want_type == "A" else "A"), None]

        best_pts: List[Tuple[int, int, float]] = []
        best_years: set = set()

        for base in endpoints:
            for ft in (0, 1):
                for fq in freq_tries:
                    params: Dict[str, Any] = {"cmp_cd": stock_code, "fin_typ": str(ft), "encparam": encparam, "id": encid}
                    if fq is not None:
                        params["freq_typ"] = fq
                    ref = "https://finance.naver.com/" if "companyinfo.stock" in base else f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={stock_code}"
                    headers = {
                        "User-Agent": "Mozilla/5.0",
                        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                        "Referer": ref,
                        "Connection": "close",
                    }
                    try:
                        html = self.session_manager.fetch_html(base, params=params, headers=headers, max_tries=1)
                        dfs = self._read_html_tables(html)
                        if not dfs:
                            continue
                        local_pts: List[Tuple[int, int, float]] = []
                        for d in dfs:
                            if d is None or d.empty:
                                continue
                            local_pts += self._collect_series_points(d, want_type, row_pat)
                        if not local_pts:
                            continue
                        years_here = {y for (y, _, _) in local_pts}
                        choose = False
                        if len(years_here) > len(best_years):
                            choose = True
                        elif len(years_here) == len(best_years) and years_here and max(years_here) > (max(best_years) if best_years else -1):
                            choose = True
                        if choose:
                            best_pts = local_pts
                            best_years = years_here
                        else:
                            new_pts = [t for t in local_pts if t[0] not in best_years]
                            if new_pts:
                                best_pts.extend(new_pts)
                                best_years |= {y for (y, _, _) in new_pts}
                    except requests.RequestException:
                        continue
                    except Exception:
                        continue
            if best_pts:
                break
        return best_pts

    def _get_series_full(self, stock_code: str, row_pat: re.Pattern) -> MetricSeries:
        encparam, encid = self._get_wise_enc_and_id(stock_code)
        if not encparam or not encid:
            return MetricSeries()
        annual_pts = self._fetch_series_points_anyway(stock_code, encparam, encid, "A", row_pat)
        quarterly_pts = self._fetch_series_points_anyway(stock_code, encparam, encid, "Q", row_pat)

        annual_map: Dict[int, float] = {}
        if annual_pts:
            by_year: Dict[int, Tuple[int, float]] = {}
            for y, m, v in annual_pts:
                if y not in by_year or m > by_year[y][0]:
                    by_year[y] = (m, v)
            annual_map = {y: mv[1] for y, mv in by_year.items()}

        q_map: Dict[str, float] = {}
        for y, m, v in quarterly_pts:
            q = ((m - 1) // 3) + 1
            q_map[f"{y}Q{q}"] = v

        ann_latest = max(annual_pts, key=lambda t: (t[0], t[1]))[2] if annual_pts else None
        qtr_latest = max(quarterly_pts, key=lambda t: (t[0], t[1]))[2] if quarterly_pts else None
        return MetricSeries(annual=annual_map, quarterly=q_map, latest_annual=ann_latest, latest_quarterly=qtr_latest)

    # ----------------------------- fetch primitives ---------------------------------
    def fetch_company_snapshot(self, code: str) -> Dict[str, Any]:
        url = f"https://finance.naver.com/item/coinfo.naver?code={code}"
        html = self.session_manager.fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")
        result = self._parse_coinfo_main(soup)
        for iframe_tag in soup.find_all("iframe"):
            src = iframe_tag.get("src")
            if not src:
                continue
            iframe_url = requests.compat.urljoin(url, src)
            try:
                iframe_html = self.session_manager.fetch_html(iframe_url)
                iframe_soup = BeautifulSoup(iframe_html, "html.parser")
                iframe_data = self._parse_coinfo_iframe(iframe_soup)
                for key, value in iframe_data.items():
                    if result.get(key) is None and value is not None:
                        result[key] = value
            except requests.RequestException:
                continue
        return result

    def fetch_metric_series(self, code: str) -> Dict[str, MetricSeries]:
        return {
            "영업이익률": self._get_series_full(code, ROW_PAT_OM),
            "현금배당수익률": self._get_series_full(code, ROW_PAT_CASH_DY),
            "ROE": self._get_series_full(code, ROW_PAT_ROE),
        }

    def fetch_one(self, code: str, name: str) -> FetchResult:
        metrics: Dict[str, Any] = {
            "현재가(원)": None,
            "52주베타": None,
            "시가총액(억원)": None,
            "외국인지분율": None,
            "현금배당수익률": None,
            "EPS": None,
            "BPS": None,
            "PER": None,
            "업종PER": None,
            "PBR": None,
            "WICS": None,
        }
        try:
            snapshot = self.fetch_company_snapshot(code)
            metrics.update(snapshot)
            series = self.fetch_metric_series(code)
            return FetchResult(name=name, code=code, metrics=metrics, series=series)
        except Exception as exc:  # noqa: BLE001 - top-level fetch should not raise
            self.logger.exception("Error fetching %s (%s)", name, code)
            return FetchResult(name=name, code=code, metrics=metrics, series={}, error=f"{type(exc).__name__}: {exc}")

    # ----------------------------- orchestration ------------------------------------
    def fetch_all(self, tickers: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        tickers = list(tickers) if tickers is not None else stock.get_market_ticker_list(market="ALL")
        code2name = {code: stock.get_market_ticker_name(code) for code in tickers}
        rows: List[Dict[str, Any]] = []
        pending = list(tickers)
        started = time.time()

        for attempt in range(self.settings.retries + 1):
            if not pending:
                break
            next_pending: List[str] = []
            done_cnt = 0
            with ThreadPoolExecutor(max_workers=self.settings.max_workers) as executor:
                future_to_code = {executor.submit(self.fetch_one, code, code2name.get(code, code)): code for code in pending}
                total_cnt = len(pending)
                for future in as_completed(future_to_code):
                    code = future_to_code[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # noqa: BLE001 - capture worker failure
                        result = FetchResult(name=code2name.get(code, code), code=code, metrics={}, series={}, error=str(exc))

                    row = self._build_row(result)
                    rows.append(row)
                    self.partial_store.add(row)

                    done_cnt += 1
                    self._print_progress(row, done_cnt, len(tickers), started)

                    if done_cnt % self.settings.show_progress_every == 0:
                        elapsed = time.time() - started
                        self.logger.info("Progress: %s/%s processed (%.1fs)", done_cnt, len(tickers), elapsed)

                    if row.get("에러"):
                        next_pending.append(code)
            if next_pending and attempt < self.settings.retries:
                self.logger.info("Retrying %s failed tickers after %.1fs", len(next_pending), self.settings.sleep_between_rounds)
                time.sleep(self.settings.sleep_between_rounds)
            pending = next_pending

        return rows

    def _build_row(self, result: FetchResult) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "종목명": result.name,
            "종목코드": result.code,
            "에러": result.error,
            **result.metrics,
        }
        for series_name, series in result.series.items():
            ann_label = f"{series_name}_연간(%)"
            q_label = f"{series_name}_분기(%)"
            row[ann_label] = series.latest_annual
            row[q_label] = series.latest_quarterly
            for year in self.settings.annual_years:
                row[f"{series_name}_연간_{year}(%)"] = series.annual.get(year)
            for y in self.settings.quarter_years:
                for q in self.settings.quarters:
                    label = f"{y}Q{q}"
                    row[f"{series_name}_분기_{label}(%)"] = series.quarterly.get(label)
        return row

    def _print_progress(self, row: Dict[str, Any], done_cnt: int, total_cnt: int, started_ts: float) -> None:
        if not self.settings.print_line_per_ticker:
            return
        pct = (done_cnt / max(1, total_cnt)) * 100.0
        name = _short_name(row.get("종목명", ""))
        core_fields = ["현재가(원)", "PER", "PBR", "현금배당수익률", "52주베타", "영업이익률_연간(%)"]
        parts = []
        for field in core_fields:
            val = row.get(field)
            parts.append(f"{field}={val if val is not None else '-'}")
        err = row.get("에러")
        msg_err = f" [ERR: {str(err)[:120]}]" if err else ""
        line = f"[{pct:6.2f}%] {done_cnt:>5}/{total_cnt:<5} {row.get('종목코드','')} {name:<12} | " + " | ".join(parts) + msg_err
        print(line, flush=True)

    # ----------------------------- export helpers -----------------------------------
    def build_ordered_columns(self, df_cols: List[str]) -> List[str]:
        fixed_cols = [
            "종목명",
            "종목코드",
            "현재가(원)",
            "52주베타",
            "시가총액(억원)",
            "외국인지분율",
            "현금배당수익률",
            "EPS",
            "BPS",
            "PER",
            "업종PER",
            "PBR",
            "WICS",
            "영업이익률_연간(%)",
            "영업이익률_분기(%)",
            "현금배당수익률_연간(%)",
            "현금배당수익률_분기(%)",
            "ROE_연간(%)",
            "ROE_분기(%)",
            "에러",
        ]
        annual_labels = [f"{y}(%)" for y in self.settings.annual_years]
        quarter_labels = [f"{y}Q{q}(%)" for y in self.settings.quarter_years for q in self.settings.quarters]
        metric_prefixes = ["영업이익률", "현금배당수익률", "ROE"]

        desired = fixed_cols
        for prefix in metric_prefixes:
            desired += [f"{prefix}_연간_{label}" for label in annual_labels]
        for prefix in metric_prefixes:
            desired += [f"{prefix}_분기_{label}" for label in quarter_labels]

        ordered = [c for c in desired if c in df_cols]
        for c in df_cols:
            if c not in ordered:
                ordered.append(c)
        return ordered

    def finalize_dataframe(self, rows: List[Dict[str, Any]]) -> pd.DataFrame:
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        ordered_cols = self.build_ordered_columns(list(df.columns))
        df = df.reindex(columns=ordered_cols)
        sort_col = "52주베타" if "52주베타" in df.columns else "종목코드"
        df = df.sort_values(by=sort_col, ascending=False, na_position="last").reset_index(drop=True)
        if self.settings.print_whole_dataframe:
            pd.set_option("display.max_columns", None, "display.width", None, "display.max_colwidth", None)
            print(df)
        return df

    def save_csv(self, df: pd.DataFrame, filename: str) -> Path:
        path = self.settings.output_dir / filename
        df.to_csv(path, index=False, encoding="utf-8-sig")
        self.logger.info("CSV saved: %s", path)
        return path

    def save_excel(self, df: pd.DataFrame, filename: str) -> Path:
        path = self.settings.output_dir / filename
        df.to_excel(path, index=False)
        self.logger.info("Excel saved: %s", path)
        return path


# --------------------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------------------


def main() -> None:
    settings = FetchSettings()
    fetcher = KRXFetcher(settings=settings)
    fetcher.logger.info("Python: %s", platform.python_version())
    fetcher.logger.info("Platform: %s", platform.platform())

    rows = fetcher.fetch_all()
    df = fetcher.finalize_dataframe(rows)
    if df.empty:
        print("No data collected.")
        return
    fetcher.save_csv(df, "KRX_metrics.csv")
    fetcher.save_excel(df, "KRX_metrics.xlsx")
    print(f"Completed {len(df)} tickers.")


if __name__ == "__main__":
    main()
