"""Öffentliche Bitstamp-Marktdaten als **Forschungsquelle** (ohne Konto, ohne API-Schlüssel, kein Handels-Endpunkt).

Warum Bitstamp: Krakens REST-OHLC liefert nur die letzten 720 Kerzen. Bitstamp gibt über
`/api/v2/ohlc/{pair}/?step=14400|86400&limit=1000&start=<unix>` lange Historien frei (BTC/USD ab 2015,
ETH/USD und XRP/USD ab etwa 2017, SOL/USD ab 2022-08, LINK/USD nur kurz).

Wichtig für die Auswertung:
- Bitstamp ist ein **Stellvertreter** für die Kursentwicklung. Gehandelt (Paper/Live) und kalkuliert wird mit
  Kraken (Kostenmodell `KRAKEN_SPOT_TIER1`); Kursunterschiede zwischen den Handelsplätzen sind klein, aber
  nicht null. Volumen ist Bitstamp-Volumen und taugt nur als relative Grösse (Volumenfilter, Teilfüllungen).
- **Survivorship:** Die Paare sind die heute liquiden. Für BTC/ETH ist der Bias klein, für Altcoins nicht
  vernachlässigbar (Paare, die verschwunden sind, fehlen).
- Antworten enthalten die noch laufende Kerze; sie wird verworfen. Liegt `start` vor dem ersten Handelstag
  eines Paars, liefert Bitstamp eine leere Liste (kein Sprung zum Beginn) – der Aufrufer schiebt den Cursor.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from ..core.candles import TIMEFRAME_MINUTES, Candle, timeframe_delta

BASE_URL = "https://www.bitstamp.net/api/v2"
SOURCE = "bitstamp"
PAGE_LIMIT = 1000


class BitstampError(RuntimeError):
    pass


def parse_ohlc(payload: dict[str, Any], instrument_id: str, timeframe: str, now: datetime) -> list[Candle]:
    data = payload.get("data")
    if not isinstance(data, dict) or "ohlc" not in data:
        raise BitstampError(str(payload.get("errors") or payload.get("reason") or payload.get("code") or "unerwartete Antwort"))
    step = timeframe_delta(timeframe)
    out: list[Candle] = []
    for row in data["ohlc"]:
        open_time = datetime.fromtimestamp(int(row["timestamp"]), tz=UTC)
        close_time = open_time + step
        if close_time > now:  # laufende Kerze: nie als abgeschlossen behandeln
            continue
        out.append(
            Candle(
                instrument_id=instrument_id,
                timeframe=timeframe,
                open_time=open_time,
                close_time=close_time,
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
                trades=None,
                source=SOURCE,
            )
        )
    out.sort(key=lambda c: c.open_time)
    return out


class BitstampPublic:
    source = SOURCE

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=20.0, headers={"User-Agent": "tradingengine-research/0.1"})

    def fetch_page(self, pair: str, instrument_id: str, timeframe: str, start: datetime | None, now: datetime) -> list[Candle]:
        """Bis zu 1000 abgeschlossene Kerzen ab `start` (ohne `start`: die jüngsten)."""
        params: dict[str, str | int] = {"step": TIMEFRAME_MINUTES[timeframe] * 60, "limit": PAGE_LIMIT}
        if start is not None:
            params["start"] = int(start.timestamp())
        resp = self._client.get(f"{BASE_URL}/ohlc/{pair}/", params=params)
        resp.raise_for_status()
        return parse_ohlc(resp.json(), instrument_id, timeframe, now)
