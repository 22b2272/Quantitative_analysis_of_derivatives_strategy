"""Next-session OHLC backtest with conservative daily stop/target handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .data import Bar
from .strategies import Signals


@dataclass
class BacktestResult:
    name: str
    dates: list[date]
    equity: list[float]
    returns: list[float]
    positions: list[float]
    turnover: list[float]
    benchmark_returns: list[float]
    trades: list[dict[str, Any]]


def _price_exit(bar: Bar, side: int, stop: float | None, target: float | None) -> tuple[float | None, str | None]:
    """Resolve stop/target touches; if both touch inside one daily bar, assume stop first."""
    if side == 1:
        if stop is not None and bar.open <= stop:
            return bar.open, "stop_gap"
        if target is not None and bar.open >= target:
            return bar.open, "target_gap"
        stop_hit = stop is not None and bar.low <= stop
        target_hit = target is not None and bar.high >= target
        if stop_hit:
            return stop, "stop_both_touched" if target_hit else "stop"
        if target_hit:
            return target, "target"
    else:
        if stop is not None and bar.open >= stop:
            return bar.open, "stop_gap"
        if target is not None and bar.open <= target:
            return bar.open, "target_gap"
        stop_hit = stop is not None and bar.high >= stop
        target_hit = target is not None and bar.low <= target
        if stop_hit:
            return stop, "stop_both_touched" if target_hit else "stop"
        if target_hit:
            return target, "target"
    return None, None


def run_backtest(bars: list[Bar], signals: Signals, cost_bps: float = 5.0) -> BacktestResult:
    """Simulate close-generated orders at the next open, with OHLC bracket exits.

    Cost is charged in basis points per one-way notional turnover. Signal-time
    stop/target levels are fixed at their signal-close values. If a bar touches
    both levels, the stop is filled first to avoid favorable-path assumptions.
    """
    if cost_bps < 0:
        raise ValueError("cost_bps must be non-negative")
    count = len(bars)
    if any(len(field) != count for field in (signals.entries, signals.exits, signals.stops, signals.targets, signals.sizes)):
        raise ValueError("Signal arrays must have one value per input bar")

    fee_rate = cost_bps / 10_000.0
    equity = [1.0] * count
    daily_returns = [0.0] * count
    positions = [0.0] * count
    turnover = [0.0] * count
    benchmark_returns = [0.0] * count
    trades: list[dict[str, Any]] = []
    side = 0
    size = 0.0
    entry_date: date | None = None
    entry_price: float | None = None
    stop: float | None = None
    target: float | None = None
    entry_fee = 0.0
    trade_size = 0.0

    def close_trade(day: date, price: float, reason: str) -> None:
        nonlocal side, size, entry_date, entry_price, stop, target, entry_fee, trade_size
        assert entry_date is not None and entry_price is not None
        gross = side * (price / entry_price - 1.0) * size
        trades.append({
            "strategy": signals.name,
            "entry_date": entry_date.isoformat(),
            "exit_date": day.isoformat(),
            "side": "long" if side > 0 else "short",
            "entry_price": entry_price,
            "exit_price": price,
            "exposure": trade_size,
            "exit_reason": reason,
            "gross_return_pct": gross * 100.0,
            "estimated_cost_pct": (entry_fee + fee_rate * size) * 100.0,
            "net_return_pct": (gross - entry_fee - fee_rate * size) * 100.0,
        })
        side, size, entry_date, entry_price, stop, target = 0, 0.0, None, None, None, None
        entry_fee, trade_size = 0.0, 0.0

    for index in range(1, count):
        bar = bars[index]
        previous_close = bars[index - 1].close
        order_index = index - 1
        desired_entry = signals.entries[order_index]
        exit_requested = signals.exits[order_index]
        period_factor = 1.0
        traded_notional = 0.0

        # Existing positions earn the overnight close-to-open move before any
        # next-open signal action is processed.
        if side:
            period_factor *= 1.0 + side * size * (bar.open / previous_close - 1.0)

        had_position = side != 0
        should_close = had_position and (exit_requested or (signals.allow_reverse and desired_entry and desired_entry != side))
        if should_close:
            period_factor *= 1.0 - fee_rate * size
            traded_notional += size
            close_trade(bar.date, bar.open, "signal")

        may_open = desired_entry != 0 and side == 0 and (not had_position or signals.allow_reverse)
        if may_open:
            new_size = max(0.0, float(signals.sizes[order_index]))
            if new_size > 0:
                side = int(desired_entry)
                size = new_size
                entry_date = bar.date
                entry_price = bar.open
                stop = signals.stops[order_index]
                target = signals.targets[order_index]
                trade_size = size
                entry_fee = fee_rate * size
                period_factor *= 1.0 - entry_fee
                traded_notional += size

        exit_price: float | None = None
        exit_reason: str | None = None
        if side:
            exit_price, exit_reason = _price_exit(bar, side, stop, target)
            end_price = exit_price if exit_price is not None else bar.close
            period_factor *= 1.0 + side * size * (end_price / bar.open - 1.0)
            if exit_price is not None:
                period_factor *= 1.0 - fee_rate * size
                traded_notional += size
                close_trade(bar.date, float(exit_price), str(exit_reason))

        if period_factor <= 0:
            raise ArithmeticError(f"Portfolio equity became non-positive on {bar.date}")
        daily_returns[index] = period_factor - 1.0
        equity[index] = equity[index - 1] * period_factor
        positions[index] = side * size
        turnover[index] = traded_notional
        benchmark_returns[index] = bar.close / previous_close - 1.0

    if side:
        last_bar = bars[-1]
        final_factor = 1.0 - fee_rate * size
        daily_returns[-1] = (1.0 + daily_returns[-1]) * final_factor - 1.0
        equity[-1] = equity[-2] * (1.0 + daily_returns[-1])
        turnover[-1] += size
        close_trade(last_bar.date, last_bar.close, "end_of_data")
        positions[-1] = 0.0

    return BacktestResult(signals.name, [bar.date for bar in bars], equity, daily_returns, positions, turnover, benchmark_returns, trades)
