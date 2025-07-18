import os
import re
import time
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup
from pykrx import stock
from tqdm import tqdm


@dataclass
class StockMetrics:
    """Container for metrics of a single stock."""
    name: str
    code: str
    current_price: Optional[float] = None
    beta_52w: Optional[float] = None
    market_cap: Optional[float] = None
    foreign_ratio: Optional[float] = None
    dividend_yield: Optional[float] = None
    eps: Optional[float] = None
    bps: Optional[float] = None
    per: Optional[float] = None
    industry_per: Optional[float] = None
    pbr: Optional[float] = None
    wics: Optional[str] = None


class NaverFinanceScraper:
    """Scraper for Naver Finance stock metrics."""

    BASE_URL = "https://finance.naver.com/item/coinfo.naver"

    FIELD_PATTERNS: Dict[str, List[str]] = {
        "beta_52w":       [r'52\s*주\s*베타'],
        "market_cap":     [r'시가\s*총액'],
        "foreign_ratio":  [r'외국인\s*지분율'],
        "dividend_yield": [r'현금\s*배당\s*수익률'],
        "eps":            [r'\bEPS\b', r'EPS\(원\)'],
        "bps":            [r'\bBPS\b', r'BPS\(원\)'],
        "per":            [r'\bPER\b'],
        "industry_per":   [r'업종\s*PER'],
        "pbr":            [r'\bPBR\b'],
        "wics":           [r'\bWICS\b'],
    }

    REGEX_FALLBACK: Dict[str, str] = {
        "industry_per": r'업종PER\s*[:：]?\s*([\d\.]+)',
        "wics": r'WICS\s*[:：]?\s*([\w\d&\(\)가-힣]+)',
    }

    def __init__(self, pause: float = 0.2):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0'})
        self.pause = pause

    @staticmethod
    def _extract_numeric(text: str) -> Optional[float]:
        cleaned = re.sub(r'[^\d\.-]', '', text)
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _fallback_extract(self, text: str, field: str) -> Optional[str]:
        pattern = self.REGEX_FALLBACK.get(field)
        if not pattern:
            return None
        match = re.search(pattern, text)
        if not match:
            return None
        value = match.group(1)
        if field == "wics":
            return value
        try:
            return float(value)
        except ValueError:
            return None

    def _request_soup(self, url: str) -> Optional[BeautifulSoup]:
        try:
            resp = self.session.get(url, timeout=5)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, 'html.parser')
        except Exception as exc:
            print(f"[요청 실패] {url} - {exc}")
            return None

    def _parse_main(self, soup: BeautifulSoup) -> Dict[str, Optional[float]]:
        result = {"current_price": None, "industry_per": None, "wics": None}
        price_tag = soup.select_one('p.no_today span.blind')
        if price_tag:
            result["current_price"] = self._extract_numeric(price_tag.get_text(strip=True))
        cper_tag = soup.find('em', id='_cper')
        if cper_tag:
            result["industry_per"] = self._extract_numeric(cper_tag.get_text(strip=True))
        text = soup.get_text(" ", strip=True)
        if result["industry_per"] is None:
            result["industry_per"] = self._fallback_extract(text, "industry_per")
        if result["wics"] is None:
            result["wics"] = self._fallback_extract(text, "wics")
        return result

    def _parse_iframe(self, soup: BeautifulSoup) -> Dict[str, Optional[float]]:
        result = {k: None for k in self.FIELD_PATTERNS}
        for table in soup.find_all('table'):
            for row in table.find_all('tr'):
                cells = [c.get_text(strip=True) for c in row.find_all(['th', 'td'])]
                for field, patterns in self.FIELD_PATTERNS.items():
                    if result[field] is not None:
                        continue
                    if any(re.search(pat, text) for pat in patterns for text in cells):
                        for idx, cell in enumerate(cells):
                            if any(re.search(pat, cell) for pat in patterns):
                                if field == "wics":
                                    result[field] = cells[idx + 1] if idx + 1 < len(cells) else None
                                else:
                                    for nxt in range(idx + 1, len(cells)):
                                        val = self._extract_numeric(cells[nxt])
                                        if val is not None:
                                            result[field] = val
                                            break
                                break
        text = soup.get_text(" ", strip=True)
        if result["industry_per"] is None:
            result["industry_per"] = self._fallback_extract(text, "industry_per")
        if result["wics"] is None:
            result["wics"] = self._fallback_extract(text, "wics")
        return result

    def fetch_metrics(self, code: str) -> Dict[str, Optional[float]]:
        url = f"{self.BASE_URL}?code={code}"
        metrics = {
            "current_price": None, "beta_52w": None, "market_cap": None, "foreign_ratio": None,
            "dividend_yield": None, "eps": None, "bps": None, "per": None, "industry_per": None,
            "pbr": None, "wics": None
        }
        soup = self._request_soup(url)
        if not soup:
            return metrics
        metrics.update(self._parse_main(soup))
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src')
            if not src:
                continue
            iframe_url = requests.compat.urljoin(url, src)
            iframe_soup = self._request_soup(iframe_url)
            if not iframe_soup:
                continue
            iframe_data = self._parse_iframe(iframe_soup)
            for k in metrics:
                if metrics[k] is None and iframe_data.get(k) is not None:
                    metrics[k] = iframe_data[k]
            if all(v is not None for v in metrics.values()):
                break
        time.sleep(self.pause)
        return metrics


def save_dataframe(df: pd.DataFrame, filename: str) -> None:
    home = os.path.expanduser("~")
    desktop = os.path.join(home, "Desktop")
    os.makedirs(desktop, exist_ok=True)
    path = os.path.join(desktop, filename)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"파일 저장 완료: {path}")


def collect_all_metrics() -> pd.DataFrame:
    scraper = NaverFinanceScraper()
    tickers = stock.get_market_ticker_list(market="ALL")
    name_map = {code: stock.get_market_ticker_name(code) for code in tickers}
    results: List[StockMetrics] = []
    for code in tqdm(tickers, desc="데이터 수집 중", unit="종목"):
        metrics = scraper.fetch_metrics(code)
        results.append(StockMetrics(name=name_map[code], code=code, **metrics))
    df = pd.DataFrame(asdict(r) for r in results)
    df.sort_values("beta_52w", ascending=False, na_position="last", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def main():
    df = collect_all_metrics()
    print("\n52주 베타 상위 15종목")
    print(df.head(15).to_string(index=False))
    save_dataframe(df, "KRX_52주Beta_and_Dividend.csv")


if __name__ == "__main__":
    main()
