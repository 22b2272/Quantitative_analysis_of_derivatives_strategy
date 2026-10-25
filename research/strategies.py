"""Daily-bar signal rules corresponding to the Pine examples plus one alpha candidate."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

from .data import Bar
from . import indicators as ind


@dataclass
class Signals:
    name: str
    entries: list[int]
    exits: list[bool]
    stops: list[float | None]
    targets: list[float | None]
    sizes: list[float]
    allow_reverse: bool = False


def _make(name: str, count: int) -> Signals:
    return Signals(name, [0] * count, [False] * count, [None] * count, [None] * count, [1.0] * count)


def build_strategies(bars: list[Bar]) -> dict[str, Signals]:
    """Generate close-of-bar events; the simulator executes them next session."""
    count = len(bars)
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    closes = [bar.close for bar in bars]
    sma25 = ind.sma(closes, 25)
    sma200 = ind.sma(closes, 200)
    ema20 = ind.ema(closes, 20)
    ema50 = ind.ema(closes, 50)
    rsi14 = ind.rsi(closes, 14)
    atr14 = ind.atr(highs, lows, closes, 14)
    bb_basis, bb_upper, bb_lower = ind.bollinger(closes, 20, 2.0)
    plus_di, minus_di, adx = ind.dmi_adx(highs, lows, closes, 14)

    candle = _make("Hammer + SMA", count)
    ema_rsi = _make("EMA + RSI trend", count)
    bollinger = _make("Bollinger + RSI mean reversion", count)
    donchian = _make("Donchian + ADX breakout", count)
    alpha = _make("126d momentum + 200d trend alpha", count)
    ema_rsi.allow_reverse = True
    alpha.allow_reverse = True

    # Original candle hypothesis with correct absolute body/wick geometry.
    for index, bar in enumerate(bars):
        if index < 25 or atr14[index] is None or sma25[index] is None:
            continue
        body = abs(bar.close - bar.open)
        effective_body = max(body, 0.05)  # NIFTY index minimum tick.
        lower_wick = min(bar.open, bar.close) - bar.low
        upper_wick = bar.high - max(bar.open, bar.close)
        bullish_hammer = bar.close > bar.open and lower_wick >= 2 * effective_body and upper_wick <= effective_body
        bearish_star = bar.close < bar.open and upper_wick >= 2 * effective_body and lower_wick <= effective_body
        if bullish_hammer and bar.close > sma25[index]:
            candle.entries[index] = 1
            risk = bar.close - (bar.low - float(atr14[index]) * 0.25)
            candle.stops[index] = bar.low - float(atr14[index]) * 0.25
            candle.targets[index] = bar.close + risk * 2.0
        elif bearish_star and bar.close < sma25[index]:
            candle.entries[index] = -1
            risk = (bar.high + float(atr14[index]) * 0.25) - bar.close
            candle.stops[index] = bar.high + float(atr14[index]) * 0.25
            candle.targets[index] = bar.close - risk * 2.0

    # EMA crossover must be confirmed by RSI. A reverse crossover closes an
    # existing trade; a new trade is only opened when already flat.
    for index in range(1, count):
        if None in (ema20[index], ema50[index], ema20[index - 1], ema50[index - 1], rsi14[index], atr14[index]):
            continue
        crossed_up = float(ema20[index]) > float(ema50[index]) and float(ema20[index - 1]) <= float(ema50[index - 1])
        crossed_down = float(ema20[index]) < float(ema50[index]) and float(ema20[index - 1]) >= float(ema50[index - 1])
        ema_rsi.exits[index] = crossed_up or crossed_down
        side = 1 if crossed_up and float(rsi14[index]) >= 50 else -1 if crossed_down and float(rsi14[index]) <= 50 else 0
        ema_rsi.entries[index] = side
        if side:
            distance = float(atr14[index]) * 2.0
            ema_rsi.stops[index] = closes[index] - side * distance
            ema_rsi.targets[index] = closes[index] + side * distance * 2.0

    # Enter on re-entry inside the outer band; exit at the basis or by ATR stop.
    for index in range(1, count):
        if None in (bb_basis[index], bb_upper[index], bb_lower[index], bb_lower[index - 1], bb_upper[index - 1], rsi14[index], adx[index], atr14[index]):
            continue
        prior_close = closes[index - 1]
        if closes[index] > float(bb_lower[index]) and prior_close <= float(bb_lower[index - 1]) and closes[index] < float(bb_basis[index]) and float(rsi14[index]) <= 45 and float(adx[index]) < 25:
            bollinger.entries[index] = 1
            bollinger.stops[index] = closes[index] - float(atr14[index]) * 1.5
            bollinger.targets[index] = float(bb_basis[index])
        elif closes[index] < float(bb_upper[index]) and prior_close >= float(bb_upper[index - 1]) and closes[index] > float(bb_basis[index]) and float(rsi14[index]) >= 55 and float(adx[index]) < 25:
            bollinger.entries[index] = -1
            bollinger.stops[index] = closes[index] + float(atr14[index]) * 1.5
            bollinger.targets[index] = float(bb_basis[index])

    # Today's close must exceed a channel built only from earlier bars.
    channel = 20
    for index in range(channel, count):
        if adx[index] is None or atr14[index] is None or plus_di[index] is None or minus_di[index] is None:
            continue
        prior_high = max(highs[index - channel : index])
        prior_low = min(lows[index - channel : index])
        side = 1 if closes[index] > prior_high and float(adx[index]) >= 20 and float(plus_di[index]) > float(minus_di[index]) else -1 if closes[index] < prior_low and float(adx[index]) >= 20 and float(minus_di[index]) > float(plus_di[index]) else 0
        if side:
            donchian.entries[index] = side
            distance = float(atr14[index]) * 2.0
            donchian.stops[index] = closes[index] - side * distance
            donchian.targets[index] = closes[index] + side * distance * 2.0

    # A fixed, predeclared time-series momentum candidate, not an optimized rule:
    # 126-session return sign + 200-session trend filter, with 10% vol targeting.
    alpha_target_vol = 0.10
    alpha_max_exposure = 1.0
    returns = [0.0] + [closes[i] / closes[i - 1] - 1.0 for i in range(1, count)]
    for index in range(200, count):
        momentum = closes[index] / closes[index - 126] - 1.0
        trend = float(sma200[index]) if sma200[index] is not None else float("nan")
        regime = 1 if momentum > 0 and closes[index] > trend else -1 if momentum < 0 and closes[index] < trend else 0
        previous_momentum = closes[index - 1] / closes[index - 127] - 1.0
        previous_trend = float(sma200[index - 1]) if sma200[index - 1] is not None else float("nan")
        previous_regime = 1 if previous_momentum > 0 and closes[index - 1] > previous_trend else -1 if previous_momentum < 0 and closes[index - 1] < previous_trend else 0
        if regime == previous_regime:
            continue
        alpha.exits[index] = previous_regime != 0
        alpha.entries[index] = regime
        if regime:
            window = returns[max(1, index - 19) : index + 1]
            if len(window) < 20:
                alpha.entries[index] = 0
                continue
            mean = sum(window) / len(window)
            daily_vol = sqrt(sum((value - mean) ** 2 for value in window) / (len(window) - 1))
            annual_vol = daily_vol * sqrt(252)
            alpha.sizes[index] = min(alpha_max_exposure, alpha_target_vol / annual_vol) if annual_vol > 0 else 0.0

    return {signal.name: signal for signal in (candle, ema_rsi, bollinger, donchian, alpha)}
