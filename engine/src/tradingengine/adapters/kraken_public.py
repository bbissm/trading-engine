"""Öffentliche Kraken-Marktdaten (ohne Konto, ohne API-Schlüssel). Kein Handels-Endpunkt.

REST OHLC liefert höchstens die letzten 720 Kerzen; der letzte Eintrag ist die noch laufende Kerze
und wird verworfen. Tiefere Historie kommt aus dem Download-Archiv (kraken_archive.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from ..core.candles import TIMEFRAME_MINUTES, Candle, timeframe_delta
from ..ports import Instrument

BASE_URL = "https://api.kraken.com/0/public"


class KrakenError(RuntimeError):
    pass


def parse_ohlc(payload: dict[str, Any], instrument: Instrument, timeframe: str, now: datetime, source: str) -> list[Candle]:
    if payload.get("error"):
        raise KrakenError(", ".join(payload["error"]))
    result = payload["result"]
    rows = next(v for k, v in result.items() if k != "last")
    step = timeframe_delta(timeframe)
    candles: list[Candle] = []
    for row in rows:
        open_time = datetime.fromtimestamp(int(row[0]), tz=UTC)
        close_time = open_time + step
        if close_time > now:  # laufende Kerze: nie als abgeschlossen behandeln
            continue
        candles.append(
            Candle(
                instrument_id=instrument.id,
                timeframe=timeframe,
                open_time=open_time,
                close_time=close_time,
                open=Decimal(row[1]),
                high=Decimal(row[2]),
                low=Decimal(row[3]),
                close=Decimal(row[4]),
                volume=Decimal(row[6]),
                trades=int(row[7]),
                source=source,
            )
        )
    candles.sort(key=lambda c: c.open_time)
    return candles


class KrakenPublic:
    source = "kraken-rest"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=15.0, headers={"User-Agent": "tradingengine/0.1"})

    def fetch_closed_candles(self, instrument: Instrument, timeframe: str, now: datetime) -> list[Candle]:
        resp = self._client.get(
            f"{BASE_URL}/OHLC", params={"pair": instrument.venue_symbol, "interval": TIMEFRAME_MINUTES[timeframe]}
        )
        resp.raise_for_status()
        return parse_ohlc(resp.json(), instrument, timeframe, now, self.source)
