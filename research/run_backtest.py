"""Run a reproducible NIFTY 50 strategy comparison and generate SVG charts."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from .backtest import BacktestResult, run_backtest
from .data import Bar, load_bars
from .metrics import summarize
from .plots import save_alpha_price_chart, save_equity_and_drawdown
from .strategies import build_strategies


def buy_and_hold(bars: list[Bar]) -> BacktestResult:
    returns = [0.0] * len(bars)
    equity = [1.0] * len(bars)
    for index in range(1, len(bars)):
        returns[index] = bars[index].close / bars[index - 1].close - 1.0
        equity[index] = equity[index - 1] * (1.0 + returns[index])
    return BacktestResult("NIFTY buy-and-hold", [bar.date for bar in bars], equity, returns, [1.0] * len(bars), [0.0] * len(bars), returns.copy(), [])


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _signal_log(results: list[BacktestResult]) -> list[dict[str, Any]]:
    return [trade for result in results for trade in result.trades]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data/raw/nifty50_daily.csv", help="Input daily OHLC CSV")
    parser.add_argument("--out", default="results", help="Directory for metrics, trades, and SVG charts")
    parser.add_argument("--cost-bps", type=float, default=5.0, help="Estimated transaction cost per one-way notional turnover, in basis points (default: 5)")
    parser.add_argument("--train-fraction", type=float, default=0.70, help="Chronological in-sample fraction (default: 0.70)")
    args = parser.parse_args()
    if not 0.5 <= args.train_fraction < 0.95:
        parser.error("--train-fraction must be between 0.50 and 0.95")
    bars = load_bars(args.data)
    signals_by_name = build_strategies(bars)
    results = [run_backtest(bars, signals, cost_bps=args.cost_bps) for signals in signals_by_name.values()]
    benchmark = buy_and_hold(bars)
    results.append(benchmark)

    split_index = int(len(bars) * args.train_fraction)
    warmup = 200
    if split_index <= warmup or len(bars) - split_index < 60:
        raise ValueError("Need more data: require >200 warm-up bars and at least 60 out-of-sample bars")

    rows: list[dict[str, Any]] = []
    for result in results:
        for period, start, end in (("in_sample", warmup, split_index), ("out_of_sample", split_index, len(bars)), ("full_after_warmup", warmup, len(bars))):
            row = summarize(result, start, end, period=period)
            row["cost_bps_per_side"] = args.cost_bps if result is not benchmark else 0.0
            rows.append(row)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "metrics.csv", rows)

    sensitivity_rows: list[dict[str, Any]] = []
    for sensitivity_cost in (0.0, 5.0, 10.0):
        for signal in signals_by_name.values():
            sensitivity_result = run_backtest(bars, signal, cost_bps=sensitivity_cost)
            row = summarize(sensitivity_result, split_index, len(bars), period="out_of_sample")
            row["cost_bps_per_side"] = sensitivity_cost
            sensitivity_rows.append(row)
    _write_csv(out_dir / "cost_sensitivity.csv", sensitivity_rows)

    trades = _signal_log(results[:-1])
    _write_csv(out_dir / "trade_log.csv", trades)
    save_equity_and_drawdown(results, out_dir, split_index)
    alpha = next(result for result in results if result.name.startswith("126d momentum"))
    save_alpha_price_chart(bars, alpha, split_index, out_dir)

    input_metadata = Path(args.data).with_suffix(".metadata.json")
    metadata = json.loads(input_metadata.read_text(encoding="utf-8")) if input_metadata.exists() else {
        "provider": "user-supplied CSV; source not recorded",
        "symbol": "unspecified",
        "first_date": bars[0].date.isoformat(),
        "last_date": bars[-1].date.isoformat(),
        "rows": len(bars),
    }
    report_metadata = {
        "dataset": metadata,
        "data_rows_used": len(bars),
        "first_date": bars[0].date.isoformat(),
        "last_date": bars[-1].date.isoformat(),
        "train_fraction": args.train_fraction,
        "split_date": bars[split_index].date.isoformat(),
        "warmup_bars_excluded_from_metrics": warmup,
        "cost_bps_per_side": args.cost_bps,
        "execution": "close signal executes at next session open; daily OHLC stop/target; stop-first if both stop and target touched; simple percentage exposure, no contract multiplier or margin model",
        "alpha_candidate": "126-session time-series momentum direction gated by 200-session SMA; 10% annualized realized-volatility target capped at 1.0x exposure; no parameter optimization",
    }
    (out_dir / "run_metadata.json").write_text(json.dumps(report_metadata, indent=2) + "\n", encoding="utf-8")

    print(f"Loaded {len(bars)} NIFTY 50 daily bars: {bars[0].date} to {bars[-1].date}")
    print(f"Out-of-sample starts {bars[split_index].date}; assumed cost {args.cost_bps:g} bps per side")
    print(f"Wrote metrics, trade log, metadata, and three SVG charts to {out_dir.resolve()}")
    print("\nOut-of-sample annualized return and risk metrics:")
    print(f"{'Strategy':38} {'CAGR':>9} {'BuyHold':>9} {'Sharpe':>8} {'MaxDD':>9} {'Trades':>7}")
    for row in rows:
        if row["period"] == "out_of_sample":
            sharpe = "n/a" if row["sharpe_rf0"] is None else f"{row['sharpe_rf0']:.2f}"
            print(f"{row['strategy'][:38]:38} {row['cagr_pct']:8.2f}% {row['buy_hold_cagr_pct']:8.2f}% {sharpe:>8} {row['max_drawdown_pct']:8.2f}% {row['trade_count']:7}")


if __name__ == "__main__":
    main()
