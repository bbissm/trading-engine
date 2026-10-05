"""T3 auf Datenbankebene: te_engine (Signale, Paper, Lernlabor) schreibt nie Live-Daten, te_live nie Paper-Daten.
Wendet die Migrationen und web/scripts/roles.sql an und verbindet sich danach mit den beiden Rollen."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg.rows import dict_row

from tradingengine.deployment import engine_role, runs_live, runs_worker

DSN = os.environ.get("TE_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "web" / "drizzle"
ROLES_SQL = ROOT / "web" / "scripts" / "roles.sql"
ENGINE_PW = "enginetestpassword0123456789abcdef"
LIVE_PW = "livetestpassword0123456789abcdefgh"

pg = [pytest.mark.pg, pytest.mark.skipif(not DSN, reason="TE_TEST_DATABASE_URL nicht gesetzt")]


def _as(role: str, password: str) -> str:
    assert DSN
    parts = urlsplit(DSN)
    host = parts.netloc.split("@", 1)[1]
    return urlunsplit(parts._replace(netloc=f"{role}:{password}@{host}"))


@pytest.fixture()
def roles() -> Iterator[dict[str, psycopg.Connection[dict[str, Any]]]]:
    assert DSN
    with psycopg.connect(DSN, autocommit=True) as owner:
        owner.execute("drop schema if exists drizzle cascade; drop schema public cascade; create schema public;")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            for statement in path.read_text(encoding="utf-8").split("--> statement-breakpoint"):
                if statement.strip():
                    owner.execute(statement)  # type: ignore[arg-type]
        sql = ROLES_SQL.read_text(encoding="utf-8").replace("{{ENGINE_PASSWORD}}", ENGINE_PW).replace("{{LIVE_PASSWORD}}", LIVE_PW)
        for statement in re.split(r"^-- @@.*$", sql, flags=re.M):
            if re.sub(r"^--.*$", "", statement, flags=re.M).strip():
                owner.execute(statement)  # type: ignore[arg-type]
        # zweimal: idempotent
        for statement in re.split(r"^-- @@.*$", sql, flags=re.M):
            if re.sub(r"^--.*$", "", statement, flags=re.M).strip():
                owner.execute(statement)  # type: ignore[arg-type]
        owner.execute("insert into instrument (id, kind, venue, venue_symbol, name, quote_currency, in_universe) "
                      "values ('TEST:AAA/USD', 'CRYPTO_SPOT', 'TEST', 'AAAUSD', 'A', 'USD', true)")
        owner.execute("insert into strategy_version (id, strategy, version, params, regime_rule_version) values ('s@1', 's', 1, '{}', 'r')")
        owner.execute("insert into account (id, mode, name, currency) values ('paper-x', 'PAPER', 'P', 'USD'), ('live-x', 'LIVE', 'L', 'USD')")
        owner.execute("insert into autopilot (account_id, state) values ('paper-x', 'ACTIVE'), ('live-x', 'SETUP')")
    engine = psycopg.connect(_as("te_engine", ENGINE_PW), autocommit=True, row_factory=dict_row)
    live = psycopg.connect(_as("te_live", LIVE_PW), autocommit=True, row_factory=dict_row)
    yield {"engine": engine, "live": live}
    engine.close()
    live.close()


def _order(conn: psycopg.Connection[dict[str, Any]], order_id: str, mode: str, account: str) -> None:
    conn.execute(
        "insert into trade_order (id, intent_key, mode, account_id, episode_id, instrument_id, strategy_version_id, timeframe, side, type, role, qty, state, created_at) "
        "values (%s, %s, %s, %s, 0, 'TEST:AAA/USD', 's@1', '4h', 'BUY', 'LIMIT', 'ENTRY', 1, 'PREPARED', now())",
        (order_id, order_id, mode, account),
    )


def test_engine_role_selection() -> None:
    assert engine_role({}) == "worker" and runs_worker({}) and not runs_live({})
    assert runs_live({"ENGINE_ROLE": "live"}) and not runs_worker({"ENGINE_ROLE": "LIVE"})
    assert engine_role({"ENGINE_ROLE": "irgendwas"}) == "unknown"
    assert not runs_worker({"ENGINE_ROLE": "irgendwas"}) and not runs_live({"ENGINE_ROLE": "irgendwas"})


@pytest.mark.pg
@pytest.mark.skipif(not DSN, reason="TE_TEST_DATABASE_URL nicht gesetzt")
def test_t3_engine_role_cannot_write_live(roles: dict[str, psycopg.Connection[dict[str, Any]]]) -> None:
    engine = roles["engine"]
    engine.execute("insert into account (id, mode, name, currency) values ('paper-y', 'PAPER', 'P', 'USD')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        engine.execute("insert into account (id, mode, name, currency) values ('live-y', 'LIVE', 'L', 'USD')")
    _order(engine, "p-1", "PAPER", "paper-x")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _order(engine, "l-1", "LIVE", "live-x")
    # Live-Zeilen sind für Änderungen unsichtbar
    assert engine.execute("update autopilot set state = 'ACTIVE' where account_id = 'live-x'").rowcount == 0
    assert engine.execute("update autopilot set state = 'ENTRIES_PAUSED' where account_id = 'paper-x'").rowcount == 1
    # lesen darf sie alles (Dashboard, Wächter)
    assert engine.execute("select count(*) as n from account").fetchone() == {"n": 3}
    for forbidden in ("update mandate set status = 'ACTIVE'", "insert into reconciliation (account_id, status, diffs) values ('live-x', 'OK', '[]')",
                      "select * from auth_session"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            engine.execute(forbidden)  # type: ignore[arg-type]


@pytest.mark.pg
@pytest.mark.skipif(not DSN, reason="TE_TEST_DATABASE_URL nicht gesetzt")
def test_t3_live_role_cannot_write_paper_or_research(roles: dict[str, psycopg.Connection[dict[str, Any]]]) -> None:
    live, engine = roles["live"], roles["engine"]
    live.execute("insert into account (id, mode, name, currency) values ('live-y', 'LIVE', 'L', 'USD')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        live.execute("insert into account (id, mode, name, currency) values ('paper-z', 'PAPER', 'P', 'USD')")
    _order(live, "l-1", "LIVE", "live-x")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _order(live, "p-2", "PAPER", "paper-x")
    _order(engine, "p-1", "PAPER", "paper-x")
    # Fills nur zur Order des eigenen Modus
    live.execute("insert into fill (id, order_id, qty, price, fee, fee_currency, time, simulated) values ('f-l', 'l-1', 1, 1, 0, 'USD', now(), false)")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        live.execute("insert into fill (id, order_id, qty, price, fee, fee_currency, time, simulated) values ('f-p', 'p-1', 1, 1, 0, 'USD', now(), false)")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        engine.execute("insert into fill (id, order_id, qty, price, fee, fee_currency, time, simulated) values ('f-x', 'l-1', 1, 1, 0, 'USD', now(), true)")
    assert live.execute("update trade_order set state = 'CHECKED' where id = 'p-1'").rowcount == 0
    assert live.execute("update autopilot set state = 'STOPPED' where account_id = 'paper-x'").rowcount == 0
    live.execute("update account set permissions = '{\"withdraw\": false}' where id = 'live-x'")
    for forbidden in ("insert into signal (instrument_id, timeframe, candle_close, strategy_version_id, action, regime, triggers, counter, data_source, data_age_s) "
                      "values ('TEST:AAA/USD', '4h', now(), 's@1', 'BUY', 'UP', '[]', '[]', 'x', 0)",
                      "insert into experiment (kind, strategy, hypothesis, config, status, issued_by) values ('BACKTEST', 's', 'h', '{}', 'PLANNED', 'x')",
                      "update strategy_version set lifecycle_status = 'LIVE_AKTIV'",
                      "update approval set decision = 'APPROVED_LIVE'",
                      "select * from auth_user"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            live.execute(forbidden)  # type: ignore[arg-type]
