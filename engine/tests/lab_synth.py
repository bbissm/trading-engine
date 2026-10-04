"""Synthetische Märkte für die Lernlabor-Tests (kein Netz).

- `trend_market`: Phasen mit Aufwärtstrend (Drift, ruhige Vola), Abwärtsphasen mit hoher Vola (Stress/Abwärts)
  und Seitwärtsphasen. Trendfolge nach Rücksetzern hat hier einen **bekannten Vorteil** (Trendpersistenz).
- `noise_market`: reiner Zufallspfad ohne Drift – kein Vorteil vorhanden.
Jedes Instrument hat eigenes Rauschen und einen eigenen Phasenversatz (geringe Korrelation untereinander).
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tradingengine.core.candles import Candle
from tradingengine.research.data import Dataset, build_dataset

START = datetime(2014, 1, 1, tzinfo=UTC)
END = datetime(2025, 10, 1, tzinfo=UTC)


def _candles(inst: str, closes: list[float], vols: list[float], seed: int, start: datetime = START) -> list[Candle]:
    rng = random.Random(seed + 1000)
    out: list[Candle] = []
    prev = closes[0]
    for i, c in enumerate(closes):
        t = start + timedelta(days=i)
        wick = vols[i] * 0.6
        hi = max(prev, c) * (1 + abs(rng.gauss(0, wick)))
        lo = min(prev, c) * (1 - abs(rng.gauss(0, wick)))
        out.append(Candle(inst, "1d", t, t + timedelta(days=1), Decimal(f"{prev:.4f}"), Decimal(f"{hi:.4f}"), Decimal(f"{lo:.4f}"),
                          Decimal(f"{c:.4f}"), Decimal(f"{rng.uniform(2000, 6000):.2f}"), None, "test"))
        prev = c
    return out


def trend_closes(n: int, seed: int, offset: int = 0) -> tuple[list[float], list[float]]:
    rng = random.Random(seed)
    cycle = [(160, 0.006, 0.012), (70, -0.007, 0.035), (60, 0.0, 0.015)]  # (Tage, Drift, Vola)
    schedule: list[tuple[float, float]] = []
    while len(schedule) < n + offset:
        for days, drift, vol in cycle:
            schedule += [(drift, vol)] * days
    schedule = schedule[offset : offset + n]
    closes, vols, p = [], [], 100.0
    for drift, vol in schedule:
        p *= math.exp(rng.gauss(drift, vol))
        closes.append(p)
        vols.append(vol)
    return closes, vols


def noise_closes(n: int, seed: int) -> tuple[list[float], list[float]]:
    rng = random.Random(seed)
    closes, p = [], 100.0
    for _ in range(n):
        p *= math.exp(rng.gauss(0.0, 0.025))
        closes.append(p)
    return closes, [0.025] * n


def market(kind: str, instruments: list[str], seed: int = 1, start: datetime = START, end: datetime = END) -> dict[str, list[Candle]]:
    n = (end - start).days
    out = {}
    for k, inst in enumerate(instruments):
        closes, vols = trend_closes(n, seed * 100 + k, offset=k * 37) if kind == "trend" else noise_closes(n, seed * 100 + k)
        out[inst] = _candles(inst, closes, vols, seed * 100 + k, start)
    return out


def dataset(series: dict[str, list[Candle]], leader: str) -> Dataset:
    first = min(c[0].open_time for c in series.values())
    last = max(c[-1].close_time for c in series.values())
    return build_dataset(series, dict(series), {i: leader for i in series}, "1d", first, last, last)
