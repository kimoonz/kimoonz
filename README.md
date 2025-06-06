# preparedness

This repository contains a simple script to download the last 10 years of financial statements for a U.S. stock using the [Financial Modeling Prep](https://financialmodelingprep.com) API.

## Requirements

- Python 3
- `requests` and `pandas` libraries

Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

Run the script with a stock ticker symbol. For example, to fetch data for Apple (AAPL):

```bash
python extract_financials.py AAPL
```

CSV files containing the income statement, balance sheet, and cash flow statement will be saved in the current directory.
