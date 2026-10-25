"""Download individual NIFTY futures contracts from the NSE history API."""

from __future__ import annotations

import argparse
from datetime import date

from .futures_data import download_nifty_futures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2018, 1, 1), help="First session, YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="Last session (default: today)")
    parser.add_argument("--output", default="data/raw/nifty_futures_contracts.csv", help="Output CSV path")
    args = parser.parse_args()
    path, metadata = download_nifty_futures(args.start, args.end or date.today(), args.output)
    print(f"Saved {metadata['rows']} contract-day rows across {metadata['contracts_with_data']} expiries to {path}")
    print(f"Dates: {metadata['first_date']} through {metadata['last_date']}")
    print(f"Source metadata: {path.with_suffix('.metadata.json')}")


if __name__ == "__main__":
    main()
