"""Market data download and validation helpers."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def load_bars(path: str | Path) -> list[Bar]:
    """Load ISO-dated OHLC CSV and reject malformed or inconsistent bars."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Price data not found: {source}")
    bars: list[Bar] = []
    with source.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"date", "open", "high", "low", "close"}
        fields = {name.strip().lower() for name in (reader.fieldnames or [])}
        missing = required - fields
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")
        for line_number, row in enumerate(reader, start=2):
            normalized = {key.strip().lower(): value for key, value in row.items() if key}
            try:
                day = date.fromisoformat(normalized["date"].strip()[:10])
                values = {key: float(normalized[key]) for key in ("open", "high", "low", "close")}
                volume = float(normalized.get("volume") or 0.0)
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"Invalid data at {source}:{line_number}: {error}") from error
            if not all(math.isfinite(value) for value in (*values.values(), volume)):
                raise ValueError(f"Non-finite OHLCV value at {source}:{line_number}")
            if min(values.values()) <= 0 or volume < 0:
                raise ValueError(f"Prices must be positive and volume non-negative at {source}:{line_number}")
            if values["high"] < max(values["open"], values["close"], values["low"]):
                raise ValueError(f"High is inconsistent with OHLC at {source}:{line_number}")
            if values["low"] > min(values["open"], values["close"], values["high"]):
                raise ValueError(f"Low is inconsistent with OHLC at {source}:{line_number}")
            bars.append(Bar(day, **values, volume=volume))
    if len(bars) < 2:
        raise ValueError("At least two valid daily bars are required")
    if any(current.date <= previous.date for previous, current in zip(bars, bars[1:])):
        raise ValueError("Dates must be unique and strictly increasing")
    return bars


def download_yahoo_daily(
    symbol: str,
    start: date,
    output: str | Path,
    *,
    end: date | None = None,
) -> tuple[Path, dict[str, object]]:
    """Download daily OHLC bars from Yahoo Finance's public chart endpoint.

    This is a convenience data source, not an official exchange feed. Check its
    availability and terms before relying on it for anything beyond research.
    """
    end = end or datetime.now(timezone.utc).date()
    if end <= start:
        raise ValueError("End date must be after start date")
    period1 = int(datetime.combine(start, time.min, timezone.utc).timestamp())
    period2 = int(datetime.combine(end + timedelta(days=1), time.min, timezone.utc).timestamp())
    query = urlencode({"period1": period1, "period2": period2, "interval": "1d", "events": "div,splits"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?{query}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; QuantResearch/1.0)"})
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    chart = payload.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(f"Yahoo Finance returned an error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"Yahoo Finance returned no data for {symbol}")
    result = results[0]
    timestamps = result.get("timestamp") or []
    quotes = result.get("indicators", {}).get("quote", [{}])[0]
    timezone_name = result.get("meta", {}).get("exchangeTimezoneName", "UTC")
    market_timezone = ZoneInfo(timezone_name)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        for index, timestamp in enumerate(timestamps):
            fields = [quotes.get(name, [None] * len(timestamps))[index] for name in ("open", "high", "low", "close")]
            if any(value is None for value in fields):
                continue
            session_date = datetime.fromtimestamp(timestamp, market_timezone).date()
            if session_date < start or session_date > end:
                continue
            volume_values = quotes.get("volume") or []
            volume = volume_values[index] if index < len(volume_values) and volume_values[index] is not None else 0
            writer.writerow([session_date.isoformat(), *(f"{float(value):.8f}" for value in fields), int(volume)])
            count += 1
    if count < 2:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"Yahoo Finance returned only {count} usable rows for {symbol}")
    file_hash = hashlib.sha256(output_path.read_bytes()).hexdigest()
    metadata: dict[str, object] = {
        "provider": "Yahoo Finance public chart endpoint",
        "symbol": result.get("meta", {}).get("symbol", symbol),
        "exchange_timezone": timezone_name,
        "interval": "1d",
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "rows": count,
        "sha256": file_hash,
        "first_date": None,
        "last_date": None,
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "price_series": "unadjusted index OHLC; no derivatives contract data",
    }
    with output_path.open(newline="", encoding="utf-8") as handle:
        data_rows = list(csv.DictReader(handle))
    metadata["first_date"] = data_rows[0]["date"]
    metadata["last_date"] = data_rows[-1]["date"]
    metadata_path = output_path.with_suffix(".metadata.json")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return output_path, metadata
