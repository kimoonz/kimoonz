import requests
import pandas as pd
import sys

API_URL = "https://financialmodelingprep.com/api/v3"
API_KEY = "demo"  # Replace with your API key if available


def fetch_financials(ticker: str, statement: str) -> pd.DataFrame:
    url = f"{API_URL}/{statement}/{ticker}?period=annual&limit=10&apikey={API_KEY}"
    response = requests.get(url)
    response.raise_for_status()
    data = response.json()
    return pd.DataFrame(data)


def main(ticker: str) -> None:
    income = fetch_financials(ticker, "income-statement")
    balance = fetch_financials(ticker, "balance-sheet-statement")
    cash_flow = fetch_financials(ticker, "cash-flow-statement")

    for name, df in [
        ("income_statement", income),
        ("balance_sheet", balance),
        ("cash_flow", cash_flow),
    ]:
        df.to_csv(f"{ticker}_{name}.csv", index=False)

    print(f"Financial statements saved for {ticker}")


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    main(ticker)
