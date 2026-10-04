from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tradingengine.core.candles import Candle, timeframe_delta
from tradingengine.ports import Instrument

START = datetime(2024, 1, 1, tzinfo=UTC)


def make_candles(
    closes: list[float],
    timeframe: str = "1d",
    instrument_id: str = "TEST:AAA/USD",
    start: datetime = START,
    spread: float = 0.01,
    volumes: list[float] | None = None,
) -> list[Candle]:
    """Kerzen aus Schlusskursen; Hoch/Tief liegen `spread` (relativ) um Eröffnung/Schluss."""
    step = timeframe_delta(timeframe)
    out: list[Candle] = []
    prev = closes[0]
    for i, close in enumerate(closes):
        open_time = start + i * step
        hi = max(prev, close) * (1 + spread)
        lo = min(prev, close) * (1 - spread)
        vol = volumes[i] if volumes else 100.0
        out.append(
            Candle(
                instrument_id, timeframe, open_time, open_time + step,
                Decimal(str(round(prev, 6))), Decimal(str(round(hi, 6))), Decimal(str(round(lo, 6))),
                Decimal(str(round(close, 6))), Decimal(str(vol)), 10, "test",
            )
        )
        prev = close
    return out


def random_walk(n: int, seed: int, drift: float = 0.0005, vol: float = 0.02, start: float = 100.0) -> list[float]:
    rng = random.Random(seed)
    out = [start]
    for _ in range(n - 1):
        out.append(out[-1] * math.exp(rng.gauss(drift, vol)))
    return out


def instrument(instrument_id: str = "TEST:AAA/USD", leader_id: str | None = None) -> Instrument:
    symbol = instrument_id.split(":")[1].replace("/", "")
    return Instrument(instrument_id, "CRYPTO_SPOT", "TEST", symbol, instrument_id, "AAA", "USD", leader_id)


class FakeMarket:
    """Marktdatenquelle mit vorgegebenen Kerzen; liefert nur, was bis `now` abgeschlossen ist."""

    source = "fake"

    def __init__(self, series: dict[tuple[str, str], list[Candle]]) -> None:
        self.series = series
        self.fail: set[tuple[str, str]] = set()

    def fetch_closed_candles(self, instrument: Instrument, timeframe: str, now: datetime) -> list[Candle]:
        if (instrument.id, timeframe) in self.fail:
            raise RuntimeError("Feed nicht erreichbar")
        return [c for c in self.series[(instrument.id, timeframe)] if c.close_time <= now]


def resample_daily(candles_4h: list[Candle]) -> list[Candle]:
    """Tageskerzen aus sechs 4h-Kerzen (beginnend bei 00:00 UTC)."""
    out: list[Candle] = []
    for i in range(0, len(candles_4h) - len(candles_4h) % 6, 6):
        chunk = candles_4h[i : i + 6]
        out.append(
            Candle(
                chunk[0].instrument_id, "1d", chunk[0].open_time, chunk[0].open_time + timedelta(days=1),
                chunk[0].open, max(c.high for c in chunk), min(c.low for c in chunk), chunk[-1].close,
                sum((c.volume for c in chunk), Decimal(0)), 60, "test",
            )
        )
    return out
