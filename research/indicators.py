"""Dependency-free daily technical indicators used by the research runner."""

from __future__ import annotations

import math
from collections.abc import Sequence


def sma(values: Sequence[float], length: int) -> list[float | None]:
    if length < 1:
        raise ValueError("length must be positive")
    result: list[float | None] = [None] * len(values)
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= length:
            running -= values[index - length]
        if index >= length - 1:
            result[index] = running / length
    return result


def ema(values: Sequence[float], length: int) -> list[float | None]:
    if length < 1:
        raise ValueError("length must be positive")
    result: list[float | None] = [None] * len(values)
    if len(values) < length:
        return result
    seed = sum(values[:length]) / length
    result[length - 1] = seed
    alpha = 2.0 / (length + 1.0)
    previous = seed
    for index in range(length, len(values)):
        previous = alpha * values[index] + (1.0 - alpha) * previous
        result[index] = previous
    return result


def rsi(values: Sequence[float], length: int = 14) -> list[float | None]:
    if length < 1:
        raise ValueError("length must be positive")
    result: list[float | None] = [None] * len(values)
    if len(values) <= length:
        return result
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values, values[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:length]) / length
    avg_loss = sum(losses[:length]) / length

    def score(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[length] = score(avg_gain, avg_loss)
    for close_index in range(length + 1, len(values)):
        change_index = close_index - 1
        avg_gain = (avg_gain * (length - 1) + gains[change_index]) / length
        avg_loss = (avg_loss * (length - 1) + losses[change_index]) / length
        result[close_index] = score(avg_gain, avg_loss)
    return result


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> list[float]:
    result = []
    for index, (bar_high, bar_low) in enumerate(zip(high, low)):
        if index == 0:
            result.append(bar_high - bar_low)
        else:
            result.append(max(bar_high - bar_low, abs(bar_high - close[index - 1]), abs(bar_low - close[index - 1])))
    return result


def wilder_average(values: Sequence[float], length: int) -> list[float | None]:
    """Wilder's smoothed moving average, seeded with the first window mean."""
    if length < 1:
        raise ValueError("length must be positive")
    result: list[float | None] = [None] * len(values)
    if len(values) < length:
        return result
    previous = sum(values[:length]) / length
    result[length - 1] = previous
    for index in range(length, len(values)):
        previous = (previous * (length - 1) + values[index]) / length
        result[index] = previous
    return result


def atr(high: Sequence[float], low: Sequence[float], close: Sequence[float], length: int = 14) -> list[float | None]:
    return wilder_average(true_range(high, low, close), length)


def bollinger(values: Sequence[float], length: int = 20, deviations: float = 2.0) -> tuple[list[float | None], list[float | None], list[float | None]]:
    if length < 1 or deviations < 0:
        raise ValueError("length must be positive and deviations non-negative")
    basis: list[float | None] = [None] * len(values)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1 : index + 1]
        mean = sum(window) / length
        variance = sum((value - mean) ** 2 for value in window) / length
        width = math.sqrt(variance) * deviations
        basis[index], upper[index], lower[index] = mean, mean + width, mean - width
    return basis, upper, lower


def dmi_adx(high: Sequence[float], low: Sequence[float], close: Sequence[float], length: int = 14) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Return +DI, -DI, and ADX using Wilder smoothing and equal DI/ADX lengths."""
    if length < 1:
        raise ValueError("length must be positive")
    count = len(close)
    plus_dm = [0.0] * count
    minus_dm = [0.0] * count
    for index in range(1, count):
        up = high[index] - high[index - 1]
        down = low[index - 1] - low[index]
        if up > down and up > 0:
            plus_dm[index] = up
        elif down > up and down > 0:
            minus_dm[index] = down
    tr = true_range(high, low, close)
    smoothed_tr = wilder_average(tr, length)
    smoothed_plus = wilder_average(plus_dm, length)
    smoothed_minus = wilder_average(minus_dm, length)
    plus_di: list[float | None] = [None] * count
    minus_di: list[float | None] = [None] * count
    dx: list[float | None] = [None] * count
    for index in range(count):
        if smoothed_tr[index] is None or smoothed_tr[index] == 0:
            continue
        plus_di[index] = 100.0 * float(smoothed_plus[index]) / float(smoothed_tr[index])
        minus_di[index] = 100.0 * float(smoothed_minus[index]) / float(smoothed_tr[index])
        denominator = plus_di[index] + minus_di[index]
        dx[index] = 0.0 if denominator == 0 else 100.0 * abs(plus_di[index] - minus_di[index]) / denominator
    first_dx_index = length - 1
    valid_dx = [value for value in dx[first_dx_index:] if value is not None]
    adx: list[float | None] = [None] * count
    if len(valid_dx) < length:
        return plus_di, minus_di, adx
    first_adx_index = first_dx_index + length - 1
    previous = sum(valid_dx[:length]) / length
    adx[first_adx_index] = previous
    for index in range(first_adx_index + 1, count):
        assert dx[index] is not None
        previous = (previous * (length - 1) + float(dx[index])) / length
        adx[index] = previous
    return plus_di, minus_di, adx
