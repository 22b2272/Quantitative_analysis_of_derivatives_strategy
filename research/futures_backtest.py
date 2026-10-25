"""NSE NIFTY futures roll, sizing, leverage-cap, and mark-to-market simulator."""

from __future__ import annotations

import csv
import math
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .backtest import BacktestResult, _price_exit
from .data import Bar
from .strategies import Signals


@dataclass(frozen=True)
class FuturesBar:
    date: date
    expiry: date
    open: float
    high: float
    low: float
    close: float
    settlement: float
    previous_settlement: float
    lot_size: int
    volume_contracts: int
    underlying_close: float


def load_futures_bars(path: str | Path) -> list[FuturesBar]:
    source = Path(path)
    with source.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"date", "expiry", "open", "high", "low", "close", "settlement", "previous_settlement", "lot_size", "volume_contracts", "underlying_close"}
        fields = {field.strip().lower() for field in (reader.fieldnames or [])}
        missing = required - fields
        if missing:
            raise ValueError(f"Futures CSV missing columns: {', '.join(sorted(missing))}")
        bars = []
        seen = set()
        for row_number, row in enumerate(reader, 2):
            values = {key.strip().lower(): value for key, value in row.items() if key}
            try:
                bar = FuturesBar(
                    date.fromisoformat(values["date"]), date.fromisoformat(values["expiry"]),
                    *(float(values[key]) for key in ("open", "high", "low", "close", "settlement", "previous_settlement")),
                    int(values["lot_size"]), int(values["volume_contracts"]), float(values["underlying_close"]),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"Invalid futures row {row_number}: {error}") from error
            if not all(math.isfinite(value) for value in (bar.open, bar.high, bar.low, bar.close, bar.settlement, bar.previous_settlement, bar.underlying_close)):
                raise ValueError(f"Non-finite futures price on row {row_number}")
            if min(bar.open, bar.high, bar.low, bar.close, bar.settlement, bar.previous_settlement, bar.underlying_close) <= 0 or bar.lot_size <= 0:
                raise ValueError(f"Non-positive price or lot size on row {row_number}")
            if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close, bar.high):
                raise ValueError(f"Inconsistent futures OHLC on row {row_number}")
            key = (bar.date, bar.expiry)
            if key in seen:
                raise ValueError(f"Duplicate futures contract row {key}")
            seen.add(key)
            bars.append(bar)
    if len(bars) < 100:
        raise ValueError("At least 100 contract-day rows are required for futures research")
    return sorted(bars, key=lambda item: (item.date, item.expiry))


def front_month_bars(bars: list[FuturesBar], roll_sessions: int = 5) -> tuple[list[FuturesBar], list[dict[date, FuturesBar]]]:
    """Select the nearest listed expiry and roll when it is <= N sessions away."""
    if roll_sessions < 0:
        raise ValueError("roll_sessions must be non-negative")
    by_day: dict[date, dict[date, FuturesBar]] = {}
    for bar in bars:
        by_day.setdefault(bar.date, {})[bar.expiry] = bar
    dates = sorted(by_day)
    front: list[FuturesBar] = []
    for index, day in enumerate(dates):
        candidates = sorted(expiry for expiry in by_day[day] if expiry >= day)
        if not candidates:
            continue
        selected = candidates[0]
        expiry_position = bisect_left(dates, selected)
        sessions_left = expiry_position - index
        if sessions_left <= roll_sessions and len(candidates) > 1:
            selected = candidates[1]
        front.append(by_day[day][selected])
    if len(front) < 2:
        raise ValueError("Could not construct a futures series with at least two sessions")
    return front, [by_day[bar.date] for bar in front]


def run_futures_backtest(
    futures_bars: list[FuturesBar],
    spot_bars: list[Bar],
    signals: Signals,
    *,
    initial_capital: float = 5_000_000.0,
    risk_per_trade: float = 0.01,
    max_leverage: float = 2.0,
    cost_bps_per_side: float = 5.0,
    roll_sessions: int = 5,
) -> tuple[BacktestResult, dict[str, float]]:
    """Backtest signals on spot but use actual individual futures contracts.

    Positions are integer lots sized to a per-trade stop-risk budget (or the
    candidate's volatility-targeted exposure) and capped by notional leverage.
    P&L is rupee mark-to-market on daily settlement; fills and brackets use OHLC.
    The maximum leverage is an exposure cap, not NSE's SPAN margin schedule.
    """
    if initial_capital <= 0 or not 0 < risk_per_trade <= 0.1 or max_leverage <= 0 or cost_bps_per_side < 0:
        raise ValueError("Capital/leverage/cost must be positive and risk_per_trade must be in (0, 0.1]")
    if len(spot_bars) != len(signals.entries):
        raise ValueError("Spot data and signal arrays must have identical lengths")
    front, contracts_by_day = front_month_bars(futures_bars, roll_sessions)
    spot_index = {bar.date: index for index, bar in enumerate(spot_bars)}
    for bar in front:
        if bar.date not in spot_index:
            raise ValueError(f"No spot bar available for futures session {bar.date}")

    count = len(front)
    cash_equity = initial_capital
    normalized_equity = [1.0] * count
    daily_returns = [0.0] * count
    exposures = [0.0] * count
    turnover = [0.0] * count
    benchmark_returns = [0.0] * count
    dates = [bar.date for bar in front]
    trades: list[dict[str, Any]] = []
    side = 0
    lots = 0
    lot_size = 0
    held_expiry: date | None = None
    entry_date: date | None = None
    entry_price: float | None = None
    initial_contract: date | None = None
    signal_spot_price: float | None = None
    stop: float | None = None
    target: float | None = None
    entry_equity = initial_capital
    trade_cash_pnl = 0.0
    trade_fees = 0.0
    rolls = 0
    size_hint = 1.0
    max_observed_leverage = 0.0
    max_entry_leverage = 0.0
    total_fees = 0.0
    total_rolls = 0
    fee_rate = cost_bps_per_side / 10_000.0

    def fee(price: float, quantity: int, lot: int) -> float:
        return price * quantity * lot * fee_rate

    def pick_lots(equity: float, price: float, lot: int, stop_price: float | None, signal_size: float) -> int:
        leverage_capacity = int((equity * max_leverage) // (price * lot))
        if leverage_capacity <= 0:
            return 0
        if stop_price is not None:
            risk_per_lot = abs(price - stop_price) * lot
            if risk_per_lot <= 0:
                return 0
            risk_capacity = int((equity * risk_per_trade) // risk_per_lot)
            return min(risk_capacity, leverage_capacity)
        target_notional = equity * max(0.0, signal_size)
        return min(int(target_notional // (price * lot)), leverage_capacity)

    def close_trade(day: date, price: float, reason: str) -> None:
        nonlocal side, lots, lot_size, held_expiry, entry_date, entry_price, initial_contract, signal_spot_price
        nonlocal stop, target, entry_equity, trade_cash_pnl, trade_fees, rolls, size_hint
        assert entry_date is not None and entry_price is not None and initial_contract is not None
        trades.append({
            "strategy": signals.name,
            "entry_date": entry_date.isoformat(),
            "exit_date": day.isoformat(),
            "side": "long" if side > 0 else "short",
            "initial_contract_expiry": initial_contract.isoformat(),
            "exit_contract_expiry": held_expiry.isoformat() if held_expiry else "",
            "entry_price": entry_price,
            "signal_spot_price": signal_spot_price,
            "exit_price": price,
            "exposure": 1.0,
            "roll_count": rolls,
            "exit_reason": reason,
            "net_pnl_inr": trade_cash_pnl,
            "estimated_cost_inr": trade_fees,
            "net_return_pct": trade_cash_pnl / entry_equity * 100.0,
        })
        side, lots, lot_size, held_expiry = 0, 0, 0, None
        entry_date, entry_price, initial_contract, signal_spot_price = None, None, None, None
        stop, target = None, None
        entry_equity, trade_cash_pnl, trade_fees, rolls = cash_equity, 0.0, 0.0, 0
        size_hint = 1.0

    for index, front_bar in enumerate(front):
        day_contracts = contracts_by_day[index]
        spot_pos = spot_index[front_bar.date]
        spot_bar = spot_bars[spot_pos]
        day_pnl = 0.0
        day_turnover = 0.0
        signal_index = spot_index[front[index - 1].date] if index > 0 else -1
        entry_event = signals.entries[signal_index] if signal_index >= 0 else 0
        exit_event = signals.exits[signal_index] if signal_index >= 0 else False

        # Mark the held contract from its prior settlement to today's open.
        if side:
            held_today = day_contracts.get(held_expiry) if held_expiry is not None else None
            if held_today is None:
                # Missing a held-contract quote is not silently treated as a flat return.
                raise ValueError(f"Missing held NIFTY futures contract {held_expiry} on {front_bar.date}")
            held_yesterday = contracts_by_day[index - 1].get(held_expiry) if index > 0 and held_expiry is not None else None
            previous_settlement = held_yesterday.settlement if held_yesterday is not None else held_today.previous_settlement
            overnight_pnl = side * lots * lot_size * (held_today.open - previous_settlement)
            day_pnl += overnight_pnl
            trade_cash_pnl += overnight_pnl

            if front_bar.expiry != held_expiry:
                old_notional = held_today.open * lots * lot_size
                old_fee = fee(held_today.open, lots, lot_size)
                day_pnl -= old_fee
                day_turnover += old_notional
                trade_cash_pnl -= old_fee
                trade_fees += old_fee
                total_fees += old_fee
                prior_open = held_today.open
                stop_distance = front_bar.open - prior_open
                if stop is not None:
                    stop += stop_distance
                if target is not None:
                    target += stop_distance
                new_lots = pick_lots(max(1.0, cash_equity + day_pnl), front_bar.open, front_bar.lot_size, stop, size_hint)
                if new_lots <= 0:
                    close_trade(front_bar.date, held_today.open, "roll_size_below_one_lot")
                else:
                    new_fee = fee(front_bar.open, new_lots, front_bar.lot_size)
                    day_pnl -= new_fee
                    day_turnover += front_bar.open * new_lots * front_bar.lot_size
                    trade_cash_pnl -= new_fee
                    trade_fees += new_fee
                    total_fees += new_fee
                    lots, lot_size, held_expiry = new_lots, front_bar.lot_size, front_bar.expiry
                    rolls += 1
                    total_rolls += 1

        had_position = side != 0
        should_close = had_position and (exit_event or (signals.allow_reverse and entry_event and entry_event != side))
        if should_close:
            close_notional = front_bar.open * lots * lot_size
            close_fee = fee(front_bar.open, lots, lot_size)
            day_pnl -= close_fee
            day_turnover += close_notional
            trade_cash_pnl -= close_fee
            trade_fees += close_fee
            total_fees += close_fee
            close_trade(front_bar.date, front_bar.open, "signal")

        may_open = entry_event != 0 and side == 0 and (not had_position or signals.allow_reverse)
        if may_open:
            signal_spot = spot_bars[signal_index]
            future_signal_bar = contracts_by_day[index - 1].get(front_bar.expiry, front[index - 1])
            stop_delta = signals.stops[signal_index] - signal_spot.close if signals.stops[signal_index] is not None else None
            target_delta = signals.targets[signal_index] - signal_spot.close if signals.targets[signal_index] is not None else None
            new_stop = future_signal_bar.close + stop_delta if stop_delta is not None else None
            new_target = future_signal_bar.close + target_delta if target_delta is not None else None
            size_hint = signals.sizes[signal_index]
            new_lots = pick_lots(max(1.0, cash_equity + day_pnl), front_bar.open, front_bar.lot_size, new_stop, size_hint)
            if new_lots > 0:
                open_fee = fee(front_bar.open, new_lots, front_bar.lot_size)
                day_pnl -= open_fee
                day_turnover += front_bar.open * new_lots * front_bar.lot_size
                side, lots, lot_size, held_expiry = int(entry_event), new_lots, front_bar.lot_size, front_bar.expiry
                entry_date, entry_price, initial_contract = front_bar.date, front_bar.open, front_bar.expiry
                signal_spot_price = signal_spot.close
                stop, target = new_stop, new_target
                entry_equity = max(1.0, cash_equity + day_pnl + open_fee)
                trade_cash_pnl, trade_fees, rolls = -open_fee, open_fee, 0
                total_fees += open_fee
                entry_leverage = front_bar.open * lots * lot_size / entry_equity
                max_entry_leverage = max(max_entry_leverage, entry_leverage)

        if side:
            # Price exit helper uses the daily range; when no bracket fills, use
            # the exchange settlement price for variation-margin P&L.
            synthetic = Bar(front_bar.date, front_bar.open, front_bar.high, front_bar.low, front_bar.close)
            exit_price, exit_reason = _price_exit(synthetic, side, stop, target)
            mark_price = exit_price if exit_price is not None else front_bar.settlement
            intraday_pnl = side * lots * lot_size * (mark_price - front_bar.open)
            day_pnl += intraday_pnl
            trade_cash_pnl += intraday_pnl
            if exit_price is not None:
                exit_fee = fee(float(exit_price), lots, lot_size)
                day_pnl -= exit_fee
                day_turnover += float(exit_price) * lots * lot_size
                trade_cash_pnl -= exit_fee
                trade_fees += exit_fee
                total_fees += exit_fee
                close_trade(front_bar.date, float(exit_price), str(exit_reason))
        prior_equity = cash_equity
        cash_equity += day_pnl
        if cash_equity <= 0:
            raise ArithmeticError(f"Futures account equity became non-positive on {front_bar.date}")
        if index > 0:
            daily_returns[index] = cash_equity / prior_equity - 1.0
            benchmark_returns[index] = spot_bar.close / spot_bars[spot_pos - 1].close - 1.0
        normalized_equity[index] = cash_equity / initial_capital
        notional = front_bar.settlement * lots * lot_size if side else 0.0
        exposures[index] = side * notional / cash_equity
        max_observed_leverage = max(max_observed_leverage, abs(exposures[index]))
        turnover[index] = day_turnover / max(prior_equity, 1.0)

    if side:
        final_bar = front[-1]
        final_fee = fee(final_bar.settlement, lots, lot_size)
        cash_equity -= final_fee
        daily_returns[-1] = cash_equity / (normalized_equity[-2] * initial_capital) - 1.0
        normalized_equity[-1] = cash_equity / initial_capital
        turnover[-1] += final_bar.settlement * lots * lot_size / max(normalized_equity[-2] * initial_capital, 1.0)
        trade_cash_pnl += -final_fee
        trade_fees += final_fee
        total_fees += final_fee
        close_trade(final_bar.date, final_bar.settlement, "end_of_data")
        exposures[-1] = 0.0

    result = BacktestResult(signals.name, dates, normalized_equity, daily_returns, exposures, turnover, benchmark_returns, trades)
    meta = {
        "initial_capital_inr": initial_capital,
        "risk_per_trade_pct": risk_per_trade * 100,
        "max_leverage_cap": max_leverage,
        "max_entry_leverage": max_entry_leverage,
        "max_observed_leverage": max_observed_leverage,
        "roll_sessions_before_expiry": roll_sessions,
        "roll_count": total_rolls,
        "estimated_fees_inr": total_fees,
        "cost_bps_per_side": cost_bps_per_side,
    }
    return result, meta
