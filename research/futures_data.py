"""Download NSE NIFTY index-futures contract history by expiry."""

from __future__ import annotations

import csv
import hashlib
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE = "https://www.nseindia.com/api/historicalOR"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/136.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/all-reports-derivatives",
}


def _get_json(url: str) -> dict[str, object]:
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            request = Request(url, headers=HEADERS)
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except Exception as error:  # NSE sometimes rate-limits archive calls.
            last_error = error
            if attempt < 4:
                time.sleep(1.0 * (attempt + 1))
    assert last_error is not None
    raise RuntimeError(f"NSE request failed after retries: {last_error}") from last_error


def download_nifty_futures(
    start: date,
    end: date,
    output: str | Path,
    *,
    cache_dir: str | Path | None = None,
    request_delay: float = 0.15,
) -> tuple[Path, dict[str, object]]:
    """Fetch individual NIFTY FUTIDX expiry histories and flatten them to CSV.

    Uses NSE's public historicalOR endpoints: one expiry-list call per year and
    one OHLC call per listed monthly futures contract. The local JSON cache makes
    interrupted downloads resumable and avoids repeatedly querying the exchange.
    """
    if end < start:
        raise ValueError("End date must be on or after start date")
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path = Path(cache_dir) if cache_dir is not None else output_path.parent / "nse_futures_cache"
    cache_path.mkdir(parents=True, exist_ok=True)
    rows: dict[tuple[str, str], dict[str, str]] = {}
    contracts_requested = 0
    contracts_with_data = 0

    expiry_horizon = end + timedelta(days=90)
    for year in range(start.year, expiry_horizon.year + 1):
        expiry_url = f"{BASE}/meta/foCPV/expireDts?" + urlencode({"instrument": "FUTIDX", "symbol": "NIFTY", "year": year})
        expiry_payload = _get_json(expiry_url)
        expiry_values = expiry_payload.get("expiresDts", [])
        if not isinstance(expiry_values, list):
            raise RuntimeError(f"NSE returned an invalid expiry list for {year}")
        for expiry_text in expiry_values:
            expiry = datetime.strptime(str(expiry_text), "%d-%b-%Y").date()
            if expiry < start or expiry > expiry_horizon:
                continue
            contracts_requested += 1
            cache_file = cache_path / f"NIFTY_FUTIDX_{expiry.isoformat()}.json"
            if cache_file.exists():
                payload = json.loads(cache_file.read_text(encoding="utf-8"))
            else:
                contract_start = max(start, expiry - timedelta(days=120))
                query = urlencode({
                    "from": contract_start.strftime("%d-%m-%Y"),
                    "to": min(expiry, end).strftime("%d-%m-%Y"),
                    "instrumentType": "FUTIDX",
                    "symbol": "NIFTY",
                    "year": year,
                    "expiryDate": expiry.strftime("%d-%b-%Y").upper(),
                })
                payload = _get_json(f"{BASE}/foCPV?{query}")
                cache_file.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
                time.sleep(max(0.0, request_delay))
            contract_rows = payload.get("data", [])
            if not isinstance(contract_rows, list):
                continue
            usable = 0
            for item in contract_rows:
                if not isinstance(item, dict):
                    continue
                trade_day = datetime.strptime(str(item["FH_TIMESTAMP"]), "%d-%b-%Y").date()
                if trade_day < start or trade_day > end:
                    continue
                try:
                    bar = {
                        "date": trade_day.isoformat(),
                        "expiry": expiry.isoformat(),
                        "open": float(item["FH_OPENING_PRICE"]),
                        "high": float(item["FH_TRADE_HIGH_PRICE"]),
                        "low": float(item["FH_TRADE_LOW_PRICE"]),
                        "close": float(item["FH_CLOSING_PRICE"]),
                        "settlement": float(item["FH_SETTLE_PRICE"]),
                        "previous_settlement": float(item["FH_PREV_CLS"]),
                        "lot_size": int(float(item["FH_MARKET_LOT"])),
                        "volume_contracts": int(float(item["FH_TOT_TRADED_QTY"])),
                        "open_interest": int(float(item["FH_OPEN_INT"])),
                        "turnover_lakh": float(item["FH_TOT_TRADED_VAL"]),
                        "underlying_close": float(item["FH_UNDERLYING_VALUE"]),
                    }
                except (KeyError, TypeError, ValueError):
                    continue
                if bar["volume_contracts"] <= 0 or bar["lot_size"] <= 0 or min(bar[key] for key in ("open", "high", "low", "close", "settlement")) <= 0:
                    continue
                key = (bar["date"], bar["expiry"])
                rows[key] = {field: str(value) for field, value in bar.items()}
                usable += 1
            if usable:
                contracts_with_data += 1

    if len(rows) < 60:
        raise RuntimeError(f"NSE returned only {len(rows)} usable NIFTY futures bars; refusing a short sample")
    fields = ["date", "expiry", "open", "high", "low", "close", "settlement", "previous_settlement", "lot_size", "volume_contracts", "open_interest", "turnover_lakh", "underlying_close"]
    ordered_rows = [rows[key] for key in sorted(rows)]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(ordered_rows)
    metadata = {
        "provider": "National Stock Exchange of India historicalOR FUTIDX endpoint",
        "symbol": "NIFTY",
        "instrument": "FUTIDX (individual monthly contracts)",
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "rows": len(ordered_rows),
        "contracts_requested": contracts_requested,
        "contracts_with_data": contracts_with_data,
        "first_date": ordered_rows[0]["date"],
        "last_date": ordered_rows[-1]["date"],
        "roll_policy_applied_later": "front contract rolled five trading sessions before expiry",
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
    }
    output_path.with_suffix(".metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return output_path, metadata
