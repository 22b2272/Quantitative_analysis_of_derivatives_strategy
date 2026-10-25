"""Run the indicator research on rolled NIFTY futures contracts."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from .backtest import BacktestResult
from .data import load_bars
from .futures_backtest import front_month_bars, load_futures_bars, run_futures_backtest
from .metrics import summarize
from .plots import save_alpha_price_chart, save_equity_and_drawdown
from .strategies import build_strategies


def spot_benchmark(spot_bars: list[Any], futures_dates: list[Any]) -> BacktestResult:
    spot_by_day = {bar.date: bar for bar in spot_bars}
    close = [spot_by_day[day].close for day in futures_dates]
    equity = [1.0] * len(close)
    returns = [0.0] * len(close)
    for index in range(1, len(close)):
        returns[index] = close[index] / close[index - 1] - 1.0
        equity[index] = equity[index - 1] * (1.0 + returns[index])
    return BacktestResult("NIFTY spot buy-and-hold", futures_dates, equity, returns, [1.0] * len(close), [0.0] * len(close), returns.copy(), [])


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spot-data", default="data/raw/nifty50_daily.csv", help="Underlying daily OHLC CSV used to calculate strategy signals")
    parser.add_argument("--futures-data", default="data/raw/nifty_futures_contracts.csv", help="NSE per-expiry NIFTY futures OHLC CSV")
    parser.add_argument("--out", default="results/futures", help="Directory for contract backtest outputs")
    parser.add_argument("--capital-inr", type=float, default=5_000_000, help="Starting simulated capital in INR (default: 50 lakh)")
    parser.add_argument("--risk-per-trade", type=float, default=0.01, help="Equity fraction risked to the initial stop (default: 1%%)")
    parser.add_argument("--max-leverage", type=float, default=2.0, help="Maximum entry notional / account equity (default: 2x)")
    parser.add_argument("--cost-bps", type=float, default=5.0, help="Estimated all-in transaction cost per side in bps of notional")
    parser.add_argument("--roll-sessions", type=int, default=5, help="Roll this many trading sessions before expiry")
    parser.add_argument("--train-fraction", type=float, default=0.70, help="Chronological in-sample fraction")
    args = parser.parse_args()
    if not 0.5 <= args.train_fraction < 0.95:
        parser.error("--train-fraction must be between 0.50 and 0.95")

    spot = load_bars(args.spot_data)
    futures = load_futures_bars(args.futures_data)
    signals_by_name = build_strategies(spot)
    front, _ = front_month_bars(futures, args.roll_sessions)
    split_index = int(len(front) * args.train_fraction)
    if split_index < 60 or len(front) - split_index < 60:
        raise ValueError("Require at least 60 front-contract sessions in each evaluation period")

    results: list[BacktestResult] = []
    metadata_by_name: dict[str, dict[str, float]] = {}
    for name, signals in signals_by_name.items():
        result, meta = run_futures_backtest(
            futures,
            spot,
            signals,
            initial_capital=args.capital_inr,
            risk_per_trade=args.risk_per_trade,
            max_leverage=args.max_leverage,
            cost_bps_per_side=args.cost_bps,
            roll_sessions=args.roll_sessions,
        )
        results.append(result)
        metadata_by_name[name] = meta
    benchmark = spot_benchmark(spot, [bar.date for bar in front])
    results.append(benchmark)

    rows: list[dict[str, Any]] = []
    for result in results:
        for period, start, end in (("in_sample", 0, split_index), ("out_of_sample", split_index, len(front)), ("full", 0, len(front))):
            row = summarize(result, start, end, period=period)
            row["cost_bps_per_side"] = args.cost_bps if result is not benchmark else 0.0
            if result.name in metadata_by_name:
                meta = metadata_by_name[result.name]
                row["max_entry_leverage"] = meta["max_entry_leverage"]
                row["max_observed_leverage"] = meta["max_observed_leverage"]
                row["estimated_fees_inr_full_run"] = meta["estimated_fees_inr"]
                row["roll_count_full_run"] = meta["roll_count"]
            rows.append(row)

    sensitivity: list[dict[str, Any]] = []
    for cost in (0.0, 5.0, 10.0):
        for name, signals in signals_by_name.items():
            result, meta = run_futures_backtest(
                futures, spot, signals,
                initial_capital=args.capital_inr,
                risk_per_trade=args.risk_per_trade,
                max_leverage=args.max_leverage,
                cost_bps_per_side=cost,
                roll_sessions=args.roll_sessions,
            )
            row = summarize(result, split_index, len(front), period="out_of_sample")
            row["cost_bps_per_side"] = cost
            row["max_entry_leverage"] = meta["max_entry_leverage"]
            row["max_observed_leverage"] = meta["max_observed_leverage"]
            row["estimated_fees_inr_full_run"] = meta["estimated_fees_inr"]
            sensitivity.append(row)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_rows(out_dir / "metrics.csv", rows)
    write_rows(out_dir / "cost_sensitivity.csv", sensitivity)
    write_rows(out_dir / "trade_log.csv", [trade for result in results[:-1] for trade in result.trades])
    save_equity_and_drawdown(results, out_dir, split_index)
    alpha_result = next(result for result in results if result.name.startswith("126d momentum"))
    spot_start = next(index for index, bar in enumerate(spot) if bar.date == front[0].date)
    spot_split = next(index for index, bar in enumerate(spot) if bar.date == front[split_index].date)
    save_alpha_price_chart(spot[spot_start:], alpha_result, spot_split - spot_start, out_dir)

    spot_metadata_path = Path(args.spot_data).with_suffix(".metadata.json")
    futures_metadata_path = Path(args.futures_data).with_suffix(".metadata.json")
    run_metadata = {
        "spot_dataset": json.loads(spot_metadata_path.read_text(encoding="utf-8")) if spot_metadata_path.exists() else {"source": "user-supplied CSV"},
        "futures_dataset": json.loads(futures_metadata_path.read_text(encoding="utf-8")) if futures_metadata_path.exists() else {"source": "user-supplied CSV"},
        "front_month_sessions": len(front),
        "front_month_start": front[0].date.isoformat(),
        "front_month_end": front[-1].date.isoformat(),
        "oos_start": front[split_index].date.isoformat(),
        "train_fraction": args.train_fraction,
        "initial_capital_inr": args.capital_inr,
        "risk_per_trade_pct": args.risk_per_trade * 100,
        "entry_notional_leverage_cap": args.max_leverage,
        "estimated_cost_bps_per_side": args.cost_bps,
        "roll_sessions_before_expiry": args.roll_sessions,
        "roll_policy": "at the close five trading sessions before expiry, next session position uses the next listed expiry; old contract closed at its daily open and new contract opened at its open",
        "pnl_model": "rupee P&L using historical daily lot size, futures OHLC stop/target fills, daily settlement marks, integer lots, and next-session-open execution",
        "limitations": ["max leverage is a notional exposure cap, not historical NSE SPAN margin or liquidation logic", "costs are flat bps scenarios, not a verified historical fee/tax schedule", "signal indicators are computed on Yahoo NIFTY spot history and fills use NSE futures history", "options are not modeled"],
    }
    (out_dir / "run_metadata.json").write_text(json.dumps(run_metadata, indent=2) + "\n", encoding="utf-8")

    print(f"Loaded {len(front)} front-month sessions: {front[0].date} to {front[-1].date}")
    print(f"OOS starts {front[split_index].date}; capital ₹{args.capital_inr:,.0f}; risk/trade {args.risk_per_trade:.1%}; leverage cap {args.max_leverage:g}x; costs {args.cost_bps:g} bps/side")
    print(f"Wrote futures metrics, trade log, metadata, and SVG charts to {out_dir.resolve()}")
    print("\nOut-of-sample futures-backed comparison:")
    print(f"{'Strategy':38} {'CAGR':>9} {'Spot BH':>9} {'Sharpe':>8} {'MaxDD':>9} {'Trades':>7}")
    for row in rows:
        if row["period"] == "out_of_sample":
            sharpe = "n/a" if row["sharpe_rf0"] is None else f"{row['sharpe_rf0']:.2f}"
            print(f"{row['strategy'][:38]:38} {row['cagr_pct']:8.2f}% {row['buy_hold_cagr_pct']:8.2f}% {sharpe:>8} {row['max_drawdown_pct']:8.2f}% {row['trade_count']:7}")


if __name__ == "__main__":
    main()
