"""Download NIFTY 50 daily underlying-index bars to a local CSV."""

from __future__ import annotations

import argparse
from datetime import date

from .data import download_yahoo_daily


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="^NSEI", help="Yahoo Finance symbol (default: ^NSEI)")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2010, 1, 1), help="First session date, YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="Last session date, YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--output", default="data/raw/nifty50_daily.csv", help="Output CSV path")
    args = parser.parse_args()
    path, metadata = download_yahoo_daily(args.symbol, args.start, args.output, end=args.end)
    print(f"Saved {metadata['rows']} daily bars to {path}")
    print(f"Dates: {metadata['first_date']} through {metadata['last_date']}")
    print(f"Source metadata: {path.with_suffix('.metadata.json')}")


if __name__ == "__main__":
    main()
