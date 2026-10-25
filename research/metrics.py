"""Performance and benchmark-relative metric calculations."""

from __future__ import annotations

from datetime import date
from math import sqrt
from statistics import mean, stdev
from typing import Any

from .backtest import BacktestResult


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def summarize(result: BacktestResult, start: int, end: int, *, period: str) -> dict[str, Any]:
    """Summarize [start, end) observations; end is exclusive."""
    sample = result.returns[start:end]
    benchmark = result.benchmark_returns[start:end]
    if not sample:
        raise ValueError(f"No observations available for {period}")
    ending_equity = result.equity[end - 1]
    beginning_equity = result.equity[start - 1] if start else 1.0
    period_return = ending_equity / beginning_equity - 1.0
    first_day = result.dates[start]
    last_day = result.dates[end - 1]
    calendar_years = max((last_day - first_day).days / 365.25, 1.0 / 252.0)
    cagr = (ending_equity / beginning_equity) ** (1.0 / calendar_years) - 1.0 if ending_equity > 0 else -1.0
    volatility = stdev(sample) * sqrt(252) if len(sample) > 1 else 0.0
    sharpe = _safe_ratio(mean(sample) * sqrt(252), stdev(sample)) if len(sample) > 1 else None

    peak = beginning_equity
    max_drawdown = 0.0
    for value in result.equity[start:end]:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1.0)

    if len(sample) > 1:
        sample_mean = mean(sample)
        benchmark_mean = mean(benchmark)
        bench_var = sum((value - benchmark_mean) ** 2 for value in benchmark) / (len(benchmark) - 1)
        covariance = sum((left - sample_mean) * (right - benchmark_mean) for left, right in zip(sample, benchmark)) / (len(sample) - 1)
        beta = _safe_ratio(covariance, bench_var)
        alpha_annual = (sample_mean - float(beta) * benchmark_mean) * 252 if beta is not None else None
        active = [left - right for left, right in zip(sample, benchmark)]
        active_volatility = stdev(active)
        information_ratio = _safe_ratio(mean(active) * sqrt(252), active_volatility) if active_volatility else None
    else:
        beta, alpha_annual, information_ratio = None, None, None

    relevant_trades = [
        trade for trade in result.trades
        if result.dates[start] <= date.fromisoformat(trade["entry_date"])
        and date.fromisoformat(trade["exit_date"]) <= last_day
    ]
    wins = [float(trade["net_return_pct"]) for trade in relevant_trades if float(trade["net_return_pct"]) > 0]
    losses = [float(trade["net_return_pct"]) for trade in relevant_trades if float(trade["net_return_pct"]) < 0]
    gross_wins = sum(wins)
    gross_losses = abs(sum(losses))
    exposure = result.positions[start:end]
    total_turnover = sum(result.turnover[start:end])
    bh_growth = 1.0
    for value in benchmark:
        bh_growth *= 1.0 + value
    benchmark_cagr = (bh_growth ** (1.0 / calendar_years) - 1.0) if bh_growth > 0 else -1.0

    return {
        "strategy": result.name,
        "period": period,
        "start": first_day.isoformat(),
        "end": last_day.isoformat(),
        "observations": len(sample),
        "total_return_pct": period_return * 100,
        "cagr_pct": cagr * 100,
        "buy_hold_cagr_pct": benchmark_cagr * 100,
        "excess_cagr_pct": (cagr - benchmark_cagr) * 100,
        "annual_volatility_pct": volatility * 100,
        "sharpe_rf0": sharpe,
        "max_drawdown_pct": max_drawdown * 100,
        "beta_vs_index": beta,
        "regression_alpha_annual_pct": alpha_annual * 100 if alpha_annual is not None else None,
        "information_ratio": information_ratio,
        "trade_count": len(relevant_trades),
        "trade_win_rate_pct": len(wins) / len(relevant_trades) * 100 if relevant_trades else None,
        "trade_profit_factor": _safe_ratio(gross_wins, gross_losses),
        "time_in_market_pct": sum(1 for value in exposure if value != 0) / len(exposure) * 100,
        "annual_turnover_x": total_turnover / len(sample) * 252,
    }
