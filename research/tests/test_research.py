from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from math import cos, sin
from pathlib import Path

from research.backtest import _price_exit, run_backtest
from research.data import Bar, load_bars
from research.indicators import atr, bollinger, dmi_adx, ema, rsi, sma
from research.metrics import summarize
from research.strategies import Signals
from research.strategies import build_strategies
from research.futures_backtest import FuturesBar, front_month_bars, run_futures_backtest


def make_bars() -> list[Bar]:
    rows = [
        (100, 102, 99, 101),
        (101, 110, 100, 108),
        (108, 121, 107, 119),
        (119, 120, 116, 118),
    ]
    start = date(2020, 1, 1)
    return [Bar(start + timedelta(days=index), *row) for index, row in enumerate(rows)]


class IndicatorTests(unittest.TestCase):
    def test_sma_and_ema_warmup_and_values(self) -> None:
        self.assertEqual(sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])
        self.assertEqual(ema([1, 2, 3], 2), [None, 1.5, 2.5])

    def test_rsi_flat_series_is_neutral(self) -> None:
        values = rsi([5.0] * 20, 5)
        self.assertEqual(values[5], 50.0)
        self.assertTrue(all(value == 50.0 for value in values[5:] if value is not None))

    def test_atr_and_bands(self) -> None:
        highs, lows, closes = [11, 12, 13], [9, 10, 11], [10, 11, 12]
        self.assertEqual(atr(highs, lows, closes, 2)[1], 2.0)
        basis, upper, lower = bollinger([1, 2, 3], 3, 2)
        self.assertAlmostEqual(float(basis[2]), 2.0)
        self.assertGreater(float(upper[2]), 2.0)
        self.assertLess(float(lower[2]), 2.0)
        plus_di, minus_di, adx = dmi_adx(highs, lows, closes, 1)
        self.assertEqual(len(plus_di), 3)
        self.assertTrue(any(value is not None for value in adx))


class DataValidationTests(unittest.TestCase):
    def test_loads_valid_csv(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "prices.csv"
            path.write_text("date,open,high,low,close\n2020-01-01,10,12,9,11\n2020-01-02,11,13,10,12\n", encoding="utf-8")
            self.assertEqual(len(load_bars(path)), 2)

    def test_rejects_inconsistent_bar(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "prices.csv"
            path.write_text("date,open,high,low,close\n2020-01-01,10,9,8,10\n2020-01-02,10,11,9,10\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "High is inconsistent"):
                load_bars(path)

    def test_rejects_duplicate_dates(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "prices.csv"
            path.write_text("date,open,high,low,close\n2020-01-01,10,12,9,11\n2020-01-01,11,13,10,12\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "strictly increasing"):
                load_bars(path)


class BacktestTests(unittest.TestCase):
    def test_signal_executes_on_next_open_and_target_exits(self) -> None:
        bars = make_bars()
        signals = Signals("fixture", [1, 0, 0, 0], [False] * 4, [90.0, None, None, None], [120.0, None, None, None], [1.0] * 4)
        result = run_backtest(bars, signals, cost_bps=0)
        self.assertEqual(result.positions[0], 0.0)
        self.assertEqual(result.trades[0]["entry_date"], "2020-01-02")
        self.assertEqual(result.trades[0]["entry_price"], 101)
        self.assertEqual(result.trades[0]["exit_price"], 120.0)
        self.assertEqual(result.trades[0]["exit_reason"], "target")

    def test_stop_wins_when_both_brackets_touch(self) -> None:
        bar = Bar(date(2020, 1, 1), 100, 112, 88, 101)
        self.assertEqual(_price_exit(bar, 1, 90, 110), (90, "stop_both_touched"))

    def test_metrics_include_benchmark_comparison(self) -> None:
        result = run_backtest(make_bars(), Signals("fixture", [1, 0, 0, 0], [False] * 4, [90, None, None, None], [120, None, None, None], [1] * 4), 0)
        summary = summarize(result, 0, 4, period="test")
        self.assertIn("sharpe_rf0", summary)
        self.assertIn("excess_cagr_pct", summary)
        self.assertEqual(summary["trade_count"], 1)

    def test_transaction_cost_reduces_strategy_equity(self) -> None:
        bars = make_bars()
        signals = Signals("fixture", [1, 0, 0, 0], [False] * 4, [90, None, None, None], [120, None, None, None], [1] * 4)
        gross = run_backtest(bars, signals, cost_bps=0)
        net = run_backtest(bars, signals, cost_bps=10)
        self.assertLess(net.equity[-1], gross.equity[-1])


class IntegrationTests(unittest.TestCase):
    def test_all_strategy_rules_run_on_valid_ohlc(self) -> None:
        bars = []
        start = date(2010, 1, 1)
        prior_close = 1000.0
        for index in range(420):
            close = 1000 + index * 0.15 + 25 * sin(index / 13) + 8 * sin(index / 3)
            open_price = prior_close + 2 * cos(index / 5)
            high = max(open_price, close) + 4
            low = min(open_price, close) - 4
            bars.append(Bar(start + timedelta(days=index), open_price, high, low, close, 1000))
            prior_close = close
        signals = build_strategies(bars)
        self.assertEqual(len(signals), 5)
        for strategy_signals in signals.values():
            self.assertEqual(len(strategy_signals.entries), len(bars))
            self.assertTrue(all(value in (-1, 0, 1) for value in strategy_signals.entries))
            result = run_backtest(bars, strategy_signals)
            self.assertEqual(len(result.equity), len(bars))
            self.assertTrue(all(value > 0 for value in result.equity))


class FuturesTests(unittest.TestCase):
    def test_front_month_rolls_five_sessions_before_expiry(self) -> None:
        sessions = []
        day = date(2026, 1, 1)
        while day <= date(2026, 1, 20):
            if day.weekday() < 5:
                sessions.append(day)
            day += timedelta(days=1)
        first_expiry, next_expiry = date(2026, 1, 15), date(2026, 2, 12)
        rows = [
            FuturesBar(session, expiry, 100, 102, 99, 101, 101, 100, 25, 100, 100)
            for session in sessions
            for expiry in (first_expiry, next_expiry)
        ]
        front, _ = front_month_bars(rows, roll_sessions=5)
        self.assertEqual(front[sessions.index(date(2026, 1, 7))].expiry, first_expiry)
        self.assertEqual(front[sessions.index(date(2026, 1, 8))].expiry, next_expiry)

    def test_contract_lot_sizing_costs_and_target_fill(self) -> None:
        start = date(2024, 1, 1)
        expiry = date(2025, 1, 30)
        spots = []
        futures = []
        for index in range(110):
            day = start + timedelta(days=index)
            spot_close = 100.0
            spots.append(Bar(day, 100, 102, 99, spot_close))
            opn, high, low, close = (101, 107, 100, 105) if index == 1 else (100, 102, 99, 100)
            futures.append(FuturesBar(day, expiry, opn, high, low, close, close, 100, 25, 1000, 100))
        entries = [0] * len(spots)
        entries[0] = 1
        stops = [None] * len(spots)
        targets = [None] * len(spots)
        stops[0], targets[0] = 98.0, 106.0
        signals = Signals("NIFTY future fixture", entries, [False] * len(spots), stops, targets, [1.0] * len(spots))
        result, meta = run_futures_backtest(futures, spots, signals, initial_capital=10_000, risk_per_trade=0.01, max_leverage=2, cost_bps_per_side=5)
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade["exit_reason"], "target")
        self.assertEqual(trade["entry_price"], 101)
        self.assertEqual(trade["exit_price"], 106.0)
        self.assertGreater(trade["estimated_cost_inr"], 0)
        self.assertGreater(trade["net_pnl_inr"], 0)
        self.assertLessEqual(meta["max_entry_leverage"], 2.0)


if __name__ == "__main__":
    unittest.main()
