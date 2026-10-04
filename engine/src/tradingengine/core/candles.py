from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

TIMEFRAME_MINUTES: dict[str, int] = {"4h": 240, "1d": 1440}


def timeframe_delta(timeframe: str) -> timedelta:
    return timedelta(minutes=TIMEFRAME_MINUTES[timeframe])


def floor_time(t: datetime, timeframe: str) -> datetime:
    """Beginn der Kerze, in die `t` fällt (UTC, an der Unix-Epoche ausgerichtet wie bei Kraken)."""
    step = TIMEFRAME_MINUTES[timeframe] * 60
    return datetime.fromtimestamp(int(t.timestamp()) // step * step, tz=UTC)


@dataclass(frozen=True, slots=True)
class Candle:
    """Abgeschlossene Kerze. Preise als Decimal; Indikatoren rechnen mit float-Kopien."""

    instrument_id: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trades: int | None
    source: str


def find_gaps(candles: list[Candle]) -> list[datetime]:
    """Fehlende Kerzenbeginne innerhalb einer aufsteigend sortierten Reihe."""
    if not candles:
        return []
    step = timeframe_delta(candles[0].timeframe)
    gaps: list[datetime] = []
    for prev, cur in zip(candles, candles[1:], strict=False):
        expected = prev.open_time + step
        while expected < cur.open_time:
            gaps.append(expected)
            expected += step
    return gaps
