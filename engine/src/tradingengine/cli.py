"""Einstiegspunkt der Engine.

    tradingengine run            Dauerlauf: Marktdaten, Signalbetrieb, Befehle, Heartbeat
    tradingengine once           ein Durchlauf (für Tests und Betriebskontrolle)
    tradingengine import-archive <instrument_id> <timeframe> <csv>

Umgebungsvariablen: DATABASE_URL (Pflicht), HEALTHCHECK_URL (optional, externer Heartbeat-Dienst).
In dieser Etappe gibt es keinen Orderweg und keine Zugangsdaten zu Handelskonten.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .adapters import kraken_archive
from .adapters.kraken_public import KrakenPublic
from .adapters.pg_store import PgStore
from .ports import Instrument, MarketData, Store
from .schema_version import SCHEMA_VERSION
from .services import commands, marketdata, signals
from .universe import SEED_INSTRUMENTS, SIGNAL_TIMEFRAMES

log = logging.getLogger("tradingengine")

SERVICE = "engine"
TICK_S = 2.0
SYNC_INTERVAL_S = 60.0
HEARTBEAT_INTERVAL_S = 30.0


def all_keys(store: Store) -> set[str]:
    return {marketdata.key_of(i.id, tf) for i in store.universe() for tf in SIGNAL_TIMEFRAMES}


def cycle(store: Store, market: MarketData, pending: set[str], now: datetime) -> tuple[dict[str, object], set[str]]:
    """Ein Durchlauf: Daten holen, Qualität bewerten, Signale erzeugen.

    `pending` sind Zeitebenen, deren letzte Kerze noch auf eine Entscheidung wartet; zurück kommen
    die weiterhin offenen."""
    sync = marketdata.sync_once(store, market, SIGNAL_TIMEFRAMES, now)
    created, still = signals.run_once(store, SIGNAL_TIMEFRAMES, sync.status, pending | sync.changed, market.source, now)
    return {"feeds": sync.status, "signals_created": created, "pending": sorted(still)}, still


def seed_instruments(market: KrakenPublic) -> list[Instrument]:
    """Start-Universum mit Tick-Grössen vom Handelsplatz; ohne Antwort bleiben bekannte Werte erhalten."""
    try:
        ticks = market.fetch_tick_sizes([i.venue_symbol for i in SEED_INSTRUMENTS])
    except Exception as exc:
        log.warning("Tick-Grössen nicht abrufbar: %s", exc)
        return SEED_INSTRUMENTS
    return [replace(i, tick_size=ticks.get(i.venue_symbol)) for i in SEED_INSTRUMENTS]


def _check_schema(store: Store) -> None:
    version = store.schema_version()
    if version != SCHEMA_VERSION:
        raise SystemExit(
            f"Schema-Version der Datenbank ist {version}, die Engine erwartet {SCHEMA_VERSION}. "
            "Migrationen ausführen (web: pnpm db:migrate) bzw. passende Engine-Version ausrollen."
        )


def _ping_healthcheck(url: str | None) -> None:
    if not url:
        return
    try:
        httpx.get(url, timeout=5.0)
    except httpx.HTTPError as exc:
        log.warning("Externer Heartbeat nicht erreichbar: %s", exc)


def run(store: PgStore, market: KrakenPublic, healthcheck_url: str | None) -> None:
    _check_schema(store)
    store.upsert_instruments(seed_instruments(market))
    last_sync = 0.0
    last_beat = 0.0
    last_cycle: dict[str, object] = {}
    # Nach einem Neustart alles einmal prüfen: eine noch gültige letzte Kerze erhält ihre Entscheidung.
    pending = all_keys(store)
    store.heartbeat(SERVICE, datetime.now(UTC), SCHEMA_VERSION, {"last_cycle": "startet"})
    while True:
        started = time.monotonic()
        now = datetime.now(UTC)
        try:
            commands.process_pending(store, now)
            if started - last_sync >= SYNC_INTERVAL_S:
                last_cycle, pending = cycle(store, market, pending, now)
                last_sync = started
            if started - last_beat >= HEARTBEAT_INTERVAL_S:
                store.heartbeat(SERVICE, now, SCHEMA_VERSION, {"last_cycle": last_cycle})
                _ping_healthcheck(healthcheck_url)
                last_beat = started
        except Exception:
            # Datenbank- oder Netzfehler: Verbindung verwerfen und im nächsten Takt erneut versuchen.
            # Ohne Heartbeat schlägt die externe Überwachung an.
            log.exception("Durchlauf fehlgeschlagen")
            store.reset()
            time.sleep(5.0)
        time.sleep(max(0.0, TICK_S - (time.monotonic() - started)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tradingengine")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("once")
    imp = sub.add_parser("import-archive")
    imp.add_argument("instrument_id")
    imp.add_argument("timeframe")
    imp.add_argument("csv", type=Path)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL fehlt", file=sys.stderr)
        return 2
    store = PgStore(dsn)

    if args.cmd == "run":
        run(store, KrakenPublic(), os.environ.get("HEALTHCHECK_URL"))
        return 0
    _check_schema(store)
    now = datetime.now(UTC)
    if args.cmd == "once":
        market = KrakenPublic()
        store.upsert_instruments(seed_instruments(market))
        result, _ = cycle(store, market, all_keys(store), now)
        commands.process_pending(store, now)
        store.heartbeat(SERVICE, now, SCHEMA_VERSION, {"last_cycle": result})
        log.info("Durchlauf: %s", result)
        return 0
    candles = kraken_archive.load_csv(args.csv, args.instrument_id, args.timeframe)
    new = store.insert_candles(candles, available_at=now)
    log.info("%d Kerzen gelesen, %d neu gespeichert", len(candles), new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
