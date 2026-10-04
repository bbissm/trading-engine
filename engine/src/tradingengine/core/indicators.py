"""Indikatoren. Alle Funktionen sind kausal: der Wert an Index i nutzt nur Daten bis einschliesslich i.

Rückgabe jeweils eine Liste gleicher Länge; `None`, solange die Aufwärmphase nicht erfüllt ist.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Series = list[float | None]


def sma(values: Sequence[float], n: int) -> Series:
    out: Series = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def ema(values: Sequence[float], n: int) -> Series:
    """EMA mit SMA der ersten n Werte als Startwert."""
    out: Series = [None] * len(values)
    if len(values) < n:
        return out
    k = 2.0 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> list[float]:
    out: list[float] = []
    for i in range(len(close)):
        if i == 0:
            out.append(high[i] - low[i])
        else:
            out.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    return out


def _wilder(values: Sequence[float], n: int, start: int = 0) -> Series:
    """Wilder-Glättung ab Index `start`; erster Wert = Mittel der ersten n Werte."""
    out: Series = [None] * len(values)
    if len(values) - start < n:
        return out
    prev = sum(values[start : start + n]) / n
    out[start + n - 1] = prev
    for i in range(start + n, len(values)):
        prev = (prev * (n - 1) + values[i]) / n
        out[i] = prev
    return out


def atr(high: Sequence[float], low: Sequence[float], close: Sequence[float], n: int = 14) -> Series:
    return _wilder(true_range(high, low, close), n)


def rsi(close: Sequence[float], n: int = 14) -> Series:
    out: Series = [None] * len(close)
    if len(close) <= n:
        return out
    gains = [0.0] + [max(close[i] - close[i - 1], 0.0) for i in range(1, len(close))]
    losses = [0.0] + [max(close[i - 1] - close[i], 0.0) for i in range(1, len(close))]
    avg_gain = _wilder(gains, n, start=1)
    avg_loss = _wilder(losses, n, start=1)
    for i in range(len(close)):
        g, lo = avg_gain[i], avg_loss[i]
        if g is None or lo is None:
            continue
        out[i] = 100.0 if lo == 0 else 100.0 - 100.0 / (1.0 + g / lo)
    return out


def adx(high: Sequence[float], low: Sequence[float], close: Sequence[float], n: int = 14) -> Series:
    size = len(close)
    out: Series = [None] * size
    if size < 2 * n:
        return out
    plus_dm = [0.0] * size
    minus_dm = [0.0] * size
    for i in range(1, size):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
    tr_s = _wilder(true_range(high, low, close), n, start=1)
    plus_s = _wilder(plus_dm, n, start=1)
    minus_s = _wilder(minus_dm, n, start=1)
    dx: list[float] = [0.0] * size
    first = n  # erster Index mit geglätteten Werten
    for i in range(first, size):
        tr_v, p, m = tr_s[i], plus_s[i], minus_s[i]
        if tr_v is None or p is None or m is None or tr_v == 0:
            continue
        plus_di = 100.0 * p / tr_v
        minus_di = 100.0 * m / tr_v
        denom = plus_di + minus_di
        dx[i] = 0.0 if denom == 0 else 100.0 * abs(plus_di - minus_di) / denom
    smoothed = _wilder(dx, n, start=first)
    for i in range(size):
        out[i] = smoothed[i]
    return out


def realized_vol(close: Sequence[float], n: int = 20) -> Series:
    """Standardabweichung der Log-Renditen über n Perioden (nicht annualisiert)."""
    out: Series = [None] * len(close)
    rets = [0.0] + [math.log(close[i] / close[i - 1]) for i in range(1, len(close))]
    for i in range(n, len(close)):
        window = rets[i - n + 1 : i + 1]
        mean = sum(window) / n
        out[i] = math.sqrt(sum((r - mean) ** 2 for r in window) / (n - 1))
    return out


def percentile_rank(values: Series, window: int, min_obs: int) -> Series:
    """Anteil der Werte im rückblickenden Fenster (inkl. aktuellem), die ≤ dem aktuellen Wert sind."""
    out: Series = [None] * len(values)
    for i, cur in enumerate(values):
        if cur is None:
            continue
        hist = [v for v in values[max(0, i - window + 1) : i + 1] if v is not None]
        if len(hist) < min_obs:
            continue
        out[i] = sum(1 for v in hist if v <= cur) / len(hist)
    return out


def rolling_max(values: Sequence[float], n: int) -> Series:
    out: Series = [None] * len(values)
    for i in range(n - 1, len(values)):
        out[i] = max(values[i - n + 1 : i + 1])
    return out


def rolling_median(values: Sequence[float], n: int) -> Series:
    out: Series = [None] * len(values)
    for i in range(n - 1, len(values)):
        window = sorted(values[i - n + 1 : i + 1])
        mid = n // 2
        out[i] = window[mid] if n % 2 else (window[mid - 1] + window[mid]) / 2
    return out
