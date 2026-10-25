"""Self-contained SVG charts; no plotting library is required."""

from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path
from .backtest import BacktestResult
from .data import Bar

COLORS = ["#2563eb", "#ea580c", "#16a34a", "#9333ea", "#dc2626", "#475569"]


def _path(values: list[float], left: float, top: float, width: float, height: float, low: float, high: float) -> str:
    span = high - low or 1.0
    return " ".join(
        f"{left + index * width / max(1, len(values) - 1):.1f},{top + height - (value - low) / span * height:.1f}"
        for index, value in enumerate(values)
    )


def _write_multiline_chart(path: Path, title: str, subtitle: str, dates: list[date], series: list[tuple[str, list[float], str]], *, percent: bool = False, split_index: int | None = None) -> None:
    width, height = 1200, 650
    left, right, top, bottom = 88, 28, 80, 90
    plot_width, plot_height = width - left - right, height - top - bottom
    all_values = [value for _, values, _ in series for value in values]
    low, high = min(all_values), max(all_values)
    if percent:
        low, high = min(low, 0.0), max(high, 0.0)
    if high == low:
        high, low = high + 1.0, low - 1.0
    padding = (high - low) * 0.06
    low -= padding
    high += padding
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">',
             '<rect width="100%" height="100%" fill="#ffffff"/>',
             f'<text x="{left}" y="34" font-family="Arial,sans-serif" font-size="22" font-weight="700" fill="#0f172a">{escape(title)}</text>',
             f'<text x="{left}" y="58" font-family="Arial,sans-serif" font-size="12" fill="#475569">{escape(subtitle)}</text>']
    for tick in range(5):
        value = low + (high - low) * tick / 4
        y = top + plot_height - tick * plot_height / 4
        label = f"{value * 100:.0f}%" if percent else f"{value:,.0f}"
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#e2e8f0"/>')
        parts.append(f'<text x="{left-10}" y="{y+4:.1f}" text-anchor="end" font-family="Arial,sans-serif" font-size="11" fill="#475569">{label}</text>')
    for tick in range(5):
        index = round((len(dates) - 1) * tick / 4)
        x = left + plot_width * tick / 4
        parts.append(f'<text x="{x:.1f}" y="{height-48}" text-anchor="middle" font-family="Arial,sans-serif" font-size="11" fill="#475569">{dates[index].isoformat()}</text>')
    if split_index is not None and 0 <= split_index < len(dates):
        x = left + plot_width * split_index / max(1, len(dates) - 1)
        parts.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top+plot_height}" stroke="#0f172a" stroke-dasharray="5 5"/>')
        parts.append(f'<text x="{x+6:.1f}" y="{top+14}" font-family="Arial,sans-serif" font-size="11" fill="#0f172a">Out-of-sample</text>')
    for name, values, color in series:
        points = _path(values, left, top, plot_width, plot_height, low, high)
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    legend_x, legend_y = left, height - 20
    for index, (name, _, color) in enumerate(series):
        x = legend_x + (index % 3) * 350
        y = legend_y - (index // 3) * 20
        parts.append(f'<line x1="{x}" y1="{y-4}" x2="{x+24}" y2="{y-4}" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text x="{x+31}" y="{y}" font-family="Arial,sans-serif" font-size="12" fill="#334155">{escape(name)}</text>')
    parts.append('</svg>')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")


def save_equity_and_drawdown(results: list[BacktestResult], out_dir: str | Path, split_index: int) -> None:
    target = Path(out_dir)
    dates = results[0].dates
    if len({len(result.dates) for result in results}) != 1:
        raise ValueError("Backtest results must share the same date index")
    colors = COLORS[:len(results)]
    _write_multiline_chart(
        target / "equity_curves.svg",
        "NIFTY 50 daily strategy equity",
        "Starting equity = 1.0; net of configured turnover costs; dashed line marks the chronological holdout split.",
        dates,
        [(result.name, result.equity, color) for result, color in zip(results, colors)],
        split_index=split_index,
    )
    drawdowns = []
    for result in results:
        peak = 1.0
        values = []
        for value in result.equity:
            peak = max(peak, value)
            values.append(value / peak - 1.0)
        drawdowns.append(values)
    _write_multiline_chart(
        target / "drawdowns.svg",
        "Strategy drawdowns",
        "Peak-to-trough decline from each strategy's running equity high.",
        dates,
        [(result.name, values, color) for result, values, color in zip(results, drawdowns, colors)],
        percent=True,
        split_index=split_index,
    )


def save_alpha_price_chart(bars: list[Bar], alpha: BacktestResult, split_index: int, out_dir: str | Path, lookback: int = 504) -> None:
    """Plot recent NIFTY closes and actual simulated alpha-candidate entries."""
    start = max(0, len(bars) - lookback)
    selected = bars[start:]
    dates = [bar.date for bar in selected]
    closes = [bar.close for bar in selected]
    width, height = 1200, 620
    left, right, top, bottom = 82, 30, 72, 66
    plot_width, plot_height = width - left - right, height - top - bottom
    low, high = min(bar.low for bar in selected), max(bar.high for bar in selected)
    padding = (high - low) * 0.05 or 1.0
    low -= padding
    high += padding
    points = _path(closes, left, top, plot_width, plot_height, low, high)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">',
             '<rect width="100%" height="100%" fill="#ffffff"/>',
             f'<text x="{left}" y="32" font-family="Arial,sans-serif" font-size="21" font-weight="700" fill="#0f172a">NIFTY 50 with momentum-alpha entries</text>',
             f'<text x="{left}" y="54" font-family="Arial,sans-serif" font-size="12" fill="#475569">Filled triangles show next-session simulated entries for the 126-session momentum + 200-session trend candidate.</text>']
    for tick in range(5):
        value = low + (high - low) * tick / 4
        y = top + plot_height - tick * plot_height / 4
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#e2e8f0"/>')
        parts.append(f'<text x="{left-8}" y="{y+4:.1f}" text-anchor="end" font-family="Arial,sans-serif" font-size="11" fill="#475569">{value:,.0f}</text>')
        day_index = round((len(dates) - 1) * tick / 4)
        x = left + plot_width * tick / 4
        parts.append(f'<text x="{x:.1f}" y="{height-32}" text-anchor="middle" font-family="Arial,sans-serif" font-size="11" fill="#475569">{dates[day_index].isoformat()}</text>')
    parts.append(f'<polyline points="{points}" fill="none" stroke="#2563eb" stroke-width="1.7"/>')
    if start <= split_index < len(bars):
        local_split = split_index - start
        x = left + plot_width * local_split / max(1, len(selected) - 1)
        parts.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top+plot_height}" stroke="#0f172a" stroke-dasharray="5 5"/>')
    bar_index = {bar.date: index for index, bar in enumerate(bars)}
    for trade in alpha.trades:
        entry_day = date.fromisoformat(trade["entry_date"])
        index = bar_index.get(entry_day, -1)
        if index < start:
            continue
        local_index = index - start
        x = left + plot_width * local_index / max(1, len(selected) - 1)
        marker_price = float(trade.get("signal_spot_price") or trade["entry_price"])
        y = top + plot_height - (marker_price - low) / (high - low) * plot_height
        color = "#16a34a" if trade["side"] == "long" else "#dc2626"
        if trade["side"] == "long":
            points_marker = f"{x:.1f},{y-7:.1f} {x-6:.1f},{y+5:.1f} {x+6:.1f},{y+5:.1f}"
        else:
            points_marker = f"{x:.1f},{y+7:.1f} {x-6:.1f},{y-5:.1f} {x+6:.1f},{y-5:.1f}"
        parts.append(f'<polygon points="{points_marker}" fill="{color}" stroke="#ffffff" stroke-width="1"><title>{trade["side"].title()} {entry_day.isoformat()} at {float(trade["entry_price"]):.2f}</title></polygon>')
    parts.append(f'<text x="{left}" y="{height-18}" font-family="Arial,sans-serif" font-size="12" fill="#16a34a">▲ Long entry</text>')
    parts.append(f'<text x="{left+120}" y="{height-18}" font-family="Arial,sans-serif" font-size="12" fill="#dc2626">▼ Short entry</text>')
    parts.append('</svg>')
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / "nifty_alpha_entries.svg").write_text("\n".join(parts), encoding="utf-8")
