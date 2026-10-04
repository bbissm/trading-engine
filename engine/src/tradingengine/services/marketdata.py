"""Marktdaten-Abgleich mit Qualitätsstatus je Instrument und Zeitebene."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..core.candles import find_gaps, floor_time, timeframe_delta
from ..ports import Instrument, MarketData, Store

log = logging.getLogger(__name__)

# So lange nach Kerzenschluss darf die neue Kerze fehlen, bevor der Feed als veraltet gilt.
STALE_GRACE = timedelta(minutes=2)
# Lücken werden im für Signale verwendeten Fenster als Sperrgrund gewertet.
GAP_CHECK_CANDLES = 600


@dataclass(slots=True)
class SyncResult:
    """Status je "instrument|timeframe" und die Schlüssel, für die neue Kerzen eingetroffen sind."""

    status: dict[str, str] = field(default_factory=dict)
    changed: set[str] = field(default_factory=set)


def key_of(instrument_id: str, timeframe: str) -> str:
    return f"{instrument_id}|{timeframe}"


def assess(store: Store, instrument: Instrument, timeframe: str, now: datetime) -> tuple[str, str | None, datetime | None]:
    """Status OK | STALE | GAP anhand der gespeicherten Kerzen."""
    candles = store.load_candles(instrument.id, timeframe, GAP_CHECK_CANDLES)
    if not candles:
        return "STALE", "Keine Kerzen vorhanden", None
    last_close = candles[-1].close_time
    expected_close = floor_time(now, timeframe)
    if last_close < expected_close and now - expected_close > STALE_GRACE:
        return "STALE", f"Letzte Kerze {last_close.isoformat()}, erwartet {expected_close.isoformat()}", last_close
    gaps = find_gaps(candles)
    if gaps:
        return "GAP", f"{len(gaps)} fehlende Kerze(n), erste {gaps[0].isoformat()}", last_close
    return "OK", None, last_close


def sync_once(store: Store, market: MarketData, timeframes: list[str], now: datetime) -> SyncResult:
    """Holt abgeschlossene Kerzen für das Universum und speichert nur neue."""
    result = SyncResult()
    known = store.feed_statuses(market.source)
    for instrument in store.universe():
        for timeframe in timeframes:
            key = key_of(instrument.id, timeframe)
            try:
                last_open = store.last_candle_open(instrument.id, timeframe)
                # Die nächste Kerze schliesst bei last_open + 2 Perioden; vorher gibt es nichts Neues abzurufen.
                if last_open is not None and key in known and known[key] == "OK" and now < last_open + 2 * timeframe_delta(timeframe):
                    result.status[key] = "OK"
                    continue
                fetched = market.fetch_closed_candles(instrument, timeframe, now)
                fresh = [c for c in fetched if last_open is None or c.open_time > last_open]
                if store.insert_candles(fresh, available_at=now):
                    result.changed.add(key)
                status, detail, last_close = assess(store, instrument, timeframe, now)
                store.set_feed_status(
                    market.source, instrument.id, timeframe, status, detail, last_close, now if status == "OK" else None
                )
                if fresh:
                    log.info("%s: %d neue Kerze(n), Status %s", key, len(fresh), status)
                result.status[key] = status
            except Exception as exc:  # Feed-Fehler dürfen andere Instrumente nicht blockieren
                log.warning("%s: Abruf fehlgeschlagen: %s", key, exc)
                store.set_feed_status(market.source, instrument.id, timeframe, "ERROR", str(exc)[:300], None, None)
                result.status[key] = "ERROR"
    return result
