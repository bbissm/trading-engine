"""Vertragstest Engine ↔ Drizzle-Schema: wendet die SQL-Migrationen aus web/drizzle an und fährt den
kompletten Durchlauf gegen echtes Postgres. Läuft nur mit TE_TEST_DATABASE_URL (leere Wegwerf-Datenbank)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from helpers import FakeMarket, instrument, make_candles, random_walk, resample_daily

from tradingengine.adapters.pg_store import PgStore
from tradingengine.core.signals import StrategyVersion
from tradingengine.core.strategies import ACTIVE
from tradingengine.core.strategies import s1_trend_pullback as s1
from tradingengine.schema_version import SCHEMA_VERSION
from tradingengine.services import commands, marketdata, signals

DSN = os.environ.get("TE_TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[2] / "web" / "drizzle"
TFS = ["4h", "1d"]

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not DSN, reason="TE_TEST_DATABASE_URL nicht gesetzt")]


@pytest.fixture()
def store() -> Iterator[PgStore]:
    assert DSN
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("drop schema public cascade; create schema public;")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            for statement in path.read_text(encoding="utf-8").split("--> statement-breakpoint"):
                if statement.strip():
                    conn.execute(statement)  # type: ignore[arg-type]
    pg = PgStore(DSN)
    yield pg
    pg.reset()


def test_schema_version_matches_engine(store: PgStore) -> None:
    assert store.schema_version() == SCHEMA_VERSION


def test_full_cycle_against_postgres(store: PgStore) -> None:
    lead, other = instrument("TEST:LEAD/USD"), instrument("TEST:AAA/USD", leader_id="TEST:LEAD/USD")
    series = {}
    for inst, seed in ((lead, 11), (other, 12)):
        c4 = make_candles(random_walk(6 * 400, seed, drift=0.0006, vol=0.008), "4h", inst.id, spread=0.003)
        series[(inst.id, "4h")] = c4
        series[(inst.id, "1d")] = resample_daily(c4)
    market = FakeMarket(series)
    store.upsert_instruments([lead, other])
    assert [i.id for i in store.universe()] == ["TEST:AAA/USD", "TEST:LEAD/USD"]

    end = series[(lead.id, "4h")][-1].close_time
    now = end + timedelta(seconds=20)
    sync = marketdata.sync_once(store, market, TFS, now)
    assert set(sync.status.values()) == {"OK"}
    loaded = store.load_candles(other.id, "4h")
    assert loaded == series[(other.id, "4h")]  # Decimal und Zeitstempel überstehen den Rundlauf unverändert
    assert store.load_candles(other.id, "4h", 5) == loaded[-5:]

    created, pending = signals.run_once(store, TFS, sync.status, sync.changed, market.source, now)
    assert (created, pending) == (4 * len(ACTIVE), set())
    # Wiederholung nach „Neustart“: weder neue Kerzen noch ein zweites Signal
    sync2 = marketdata.sync_once(store, market, TFS, now + timedelta(seconds=60))
    assert sync2.changed == set()
    again, _ = signals.run_once(store, TFS, sync2.status, set(sync.status), market.source, now + timedelta(seconds=60))
    assert again == 0

    with psycopg.connect(DSN) as conn:  # type: ignore[arg-type]
        assert conn.execute("select count(*) from signal").fetchone() == (4 * len(ACTIVE),)
        rows = conn.execute("select distinct data_age_s, valid_until > created_at, jsonb_array_length(triggers) > 0 from signal").fetchall()
        assert rows == [(20, True, True)]
        assert conn.execute("select count(*) from feature_snapshot").fetchone()[0] > 1000  # type: ignore[index]
        assert conn.execute("select count(distinct status) from feed_status").fetchone() == (1,)

        conn.execute("insert into command (type, issued_by) values ('PING', 'user:test'), ('NOPE', 'user:test')")
        conn.commit()
        assert commands.process_pending(store, now) == 2
        rows = conn.execute("select type, status, result ->> 'pong' from command order by id").fetchall()
        assert rows == [("PING", "DONE", "true"), ("NOPE", "REJECTED", None)]
        assert conn.execute("select kind from audit_event").fetchall() == [("command.rejected",)]

    store.heartbeat("engine", now, SCHEMA_VERSION, {"ok": True})
    store.heartbeat("engine", now + timedelta(seconds=30), SCHEMA_VERSION, {"ok": True})


def test_strategy_version_is_immutable(store: PgStore) -> None:
    store.ensure_strategy_version(s1.VERSION)
    store.ensure_strategy_version(s1.VERSION)  # gleiche Parameter: in Ordnung
    changed = StrategyVersion(s1.VERSION.id, s1.VERSION.strategy, 1, {**s1.VERSION.params, "stop_atr": 9.0}, s1.VERSION.regime_rule_version)
    with pytest.raises(ValueError, match="neue Version"):
        store.ensure_strategy_version(changed)
