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
from .adapters.pg_paper import PaperRepo
from .adapters.pg_store import PgStore
from .core.candles import timeframe_delta
from .core.strategies import ACTIVE
from .ports import Instrument, MarketData, Store
from .schema_version import SCHEMA_VERSION
from .services import commands, marketdata, paper, signals
from .universe import SEED_INSTRUMENTS, SIGNAL_TIMEFRAMES

log = logging.getLogger("tradingengine")

SERVICE = "engine"
TICK_S = 2.0
SYNC_INTERVAL_S = 60.0
HEARTBEAT_INTERVAL_S = 30.0


def open_keys(store: Store, now: datetime) -> set[str]:
    """Zeitebenen, deren letzte Kerze noch gültig ist und der Entscheidungen fehlen (aus der Datenbank
    abgeleitet – der Ablauf braucht keinen Zustand im Arbeitsspeicher)."""
    out: set[str] = set()
    for key, (last_close, count) in store.latest_signal_counts().items():
        timeframe = key.rsplit("|", 1)[1]
        if count < len(ACTIVE) and now < last_close + timeframe_delta(timeframe):
            out.add(key)
    return out


def cycle(store: Store, market: MarketData, now: datetime) -> dict[str, object]:
    """Ein Durchlauf: fällige Kerzen holen, Qualität bewerten, fehlende Entscheidungen erzeugen."""
    sync = marketdata.sync_once(store, market, SIGNAL_TIMEFRAMES, now)
    pending = open_keys(store, now) | sync.changed
    created, still = signals.run_once(store, SIGNAL_TIMEFRAMES, sync.status, pending, market.source, now)
    return {"feeds": sync.status, "signals_created": created, "pending": sorted(still)}


def ensure_universe(store: Store, market: KrakenPublic) -> None:
    """Stammdaten anlegen bzw. ergänzen; die Abfrage beim Handelsplatz nur, wenn etwas fehlt."""
    known = {i.id: i for i in store.universe()}
    if all(s.id in known and known[s.id].tick_size is not None and known[s.id].min_qty is not None for s in SEED_INSTRUMENTS):
        return
    store.upsert_instruments(seed_instruments(market))


def tick(dsn: str, healthcheck_url: str | None = None) -> dict[str, object]:
    """Ein vollständiger, zustandsloser Arbeitsschritt (für den Minuten-Cron): Befehle, Daten, Signale, Heartbeat."""
    store = PgStore(dsn)
    try:
        _check_schema(store)
        market = KrakenPublic()
        now = datetime.now(UTC)
        ensure_universe(store, market)
        repo = PaperRepo(store.connection())
        handled = commands.process_pending(store, now, repo)
        result = cycle(store, market, now)
        feeds = result["feeds"]
        assert isinstance(feeds, dict)
        result["paper"] = paper.run_accounts(repo, feeds, datetime.now(UTC))
        store.heartbeat(SERVICE, datetime.now(UTC), SCHEMA_VERSION, {"last_cycle": result, "runner": "tick"})
        _ping_healthcheck(healthcheck_url)
        return {**result, "commands": handled}
    finally:
        store.reset()


def seed_instruments(market: KrakenPublic) -> list[Instrument]:
    """Start-Universum mit Tick-Grössen vom Handelsplatz; ohne Antwort bleiben bekannte Werte erhalten."""
    try:
        specs = market.fetch_pair_specs([i.venue_symbol for i in SEED_INSTRUMENTS])
    except Exception as exc:
        log.warning("Handelsplatz-Spezifikationen nicht abrufbar: %s", exc)
        return SEED_INSTRUMENTS
    out: list[Instrument] = []
    for i in SEED_INSTRUMENTS:
        tick, min_qty, min_notional = specs.get(i.venue_symbol, (None, None, None))
        out.append(replace(i, tick_size=tick, min_qty=min_qty, min_notional=min_notional))
    return out


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
    ensure_universe(store, market)
    last_sync = 0.0
    last_beat = 0.0
    last_cycle: dict[str, object] = {}
    store.heartbeat(SERVICE, datetime.now(UTC), SCHEMA_VERSION, {"last_cycle": "startet"})
    while True:
        started = time.monotonic()
        now = datetime.now(UTC)
        try:
            repo = PaperRepo(store.connection())
            commands.process_pending(store, now, repo)
            if started - last_sync >= SYNC_INTERVAL_S:
                last_cycle = cycle(store, market, now)
                feeds = last_cycle["feeds"]
                assert isinstance(feeds, dict)
                last_cycle["paper"] = paper.run_accounts(repo, feeds, datetime.now(UTC))
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
    if args.cmd == "once":
        log.info("Durchlauf: %s", tick(dsn, os.environ.get("HEALTHCHECK_URL")))
        return 0
    _check_schema(store)
    now = datetime.now(UTC)
    candles = kraken_archive.load_csv(args.csv, args.instrument_id, args.timeframe)
    new = store.insert_candles(candles, available_at=now)
    log.info("%d Kerzen gelesen, %d neu gespeichert", len(candles), new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
