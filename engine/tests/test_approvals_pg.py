"""Freigabe-Entscheide und Shadow-Betrieb (docs/01 3.5, docs/07 T11) gegen Postgres."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from helpers import FakeMarket, instrument, make_candles, random_walk, resample_daily

from tradingengine import cli
from tradingengine.adapters.pg_paper import PaperRepo
from tradingengine.adapters.pg_store import PgStore
from tradingengine.core.signals import StrategyVersion
from tradingengine.core.strategies import ACTIVE, from_version
from tradingengine.core.strategies import s1_trend_pullback as s1
from tradingengine.ports import Command
from tradingengine.services import approvals, strategy_registry

DSN = os.environ.get("TE_TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[2] / "web" / "drizzle"
pytestmark = [pytest.mark.pg, pytest.mark.skipif(not DSN, reason="TE_TEST_DATABASE_URL nicht gesetzt")]

OPT_ID = "s1-trend-pullback@1+opt-test0001"
NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)


@pytest.fixture()
def store() -> Iterator[PgStore]:
    assert DSN
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("drop schema if exists drizzle cascade; drop schema public cascade; create schema public;")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            for statement in path.read_text(encoding="utf-8").split("--> statement-breakpoint"):
                if statement.strip():
                    conn.execute(statement)  # type: ignore[arg-type]
    pg = PgStore(DSN)
    for s in ACTIVE:
        pg.ensure_strategy_version(s.version)
    pg.ensure_strategy_version(StrategyVersion(OPT_ID, "s1-trend-pullback", 1, {**s1.VERSION.params, "stop_atr": 2.5}, s1.VERSION.regime_rule_version))
    yield pg
    pg.reset()


def _sql(store: PgStore, q: str, p: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = store.connection().execute(q, p)  # type: ignore[arg-type]
    return cur.fetchall() if cur.description else []


def _proposal(store: PgStore) -> int:
    return int(_sql(store, "insert into approval (strategy_version_id, proposed_by) values (%s, 'engine:lab') returning id", (OPT_ID,))[0]["id"])


def _cmd(target: str, **params: Any) -> Command:
    return Command(1, "APPROVAL_DECIDE", target, params, "user:test")


def test_from_version_binds_its_own_parameters() -> None:
    version = StrategyVersion(OPT_ID, "s1-trend-pullback", 1, {**s1.VERSION.params, "stop_atr": 2.5}, s1.VERSION.regime_rule_version)
    sd = from_version(version)
    assert sd is not None and sd.version.id == OPT_ID
    assert from_version(StrategyVersion("x@1", "unbekannt", 1, {}, "regime@1")) is None
    assert from_version(StrategyVersion("y@1", "s1-trend-pullback", 1, {"gibts_nicht": 1}, "regime@1")) is None


def test_only_reject_or_shadow_and_only_once(store: PgStore) -> None:
    repo = PaperRepo(store.connection())
    approval_id = _proposal(store)
    status, result = approvals.decide(store.connection(), repo, _cmd(str(approval_id), decision="APPROVED_LIVE"), NOW)
    assert status == "REJECTED" and "Step-up" in result["reason"]
    assert _sql(store, "select decision from approval")[0]["decision"] == "PENDING"
    assert approvals.decide(store.connection(), repo, _cmd(str(approval_id), decision="REJECTED", note="zu dünn"), NOW)[0] == "DONE"
    assert approvals.decide(store.connection(), repo, _cmd(str(approval_id), decision="SHADOW"), NOW)[0] == "REJECTED"  # schon entschieden
    row = _sql(store, "select decision, decided_by, note from approval")[0]
    assert row == {"decision": "REJECTED", "decided_by": "user:test", "note": "zu dünn"}
    assert strategy_registry.released_versions(store.connection()) == []


def test_shadow_creates_own_paper_account_and_signals_for_that_version(store: PgStore) -> None:
    repo = PaperRepo(store.connection())
    approval_id = _proposal(store)
    status, result = approvals.decide(store.connection(), repo, _cmd(f"approval:{approval_id}", decision="SHADOW"), NOW)
    assert status == "DONE"
    account = _sql(store, "select a.id, a.mode, ap.state, e.strategy_version_ids from account a join autopilot ap on ap.account_id = a.id "
                          "join episode e on e.account_id = a.id")[0]
    assert account["id"] == result["shadow_account"] and account["mode"] == "PAPER" and account["state"] == "ACTIVE"
    assert account["strategy_version_ids"] == [OPT_ID]
    released = strategy_registry.released_versions(store.connection())
    assert [s.version.id for s in released] == [OPT_ID]
    # aktive Live-Strategie bleibt unverändert: die Lab-Version ersetzt keine bestehende Version (T11)
    assert _sql(store, "select params from strategy_version where id = %s", (s1.VERSION.id,))[0]["params"] == s1.VERSION.params

    # Signalbetrieb erzeugt nun auch Entscheidungen der freigegebenen Version
    inst = instrument("TEST:AAA/USD")
    store.upsert_instruments([inst])
    c4 = make_candles(random_walk(6 * 400, 11, drift=0.0006, vol=0.008), "4h", inst.id, spread=0.003)
    market = FakeMarket({(inst.id, "4h"): c4, (inst.id, "1d"): resample_daily(c4)})
    end = c4[-1].close_time
    result_cycle, _ = cli.cycle(store, market, end + timedelta(seconds=20), None, strategy_registry.all_strategies(store.connection()))
    assert result_cycle["signals_created"] == 2 * (len(ACTIVE) + 1)
    ids = {r["strategy_version_id"] for r in _sql(store, "select distinct strategy_version_id from signal")}
    assert OPT_ID in ids and len(ids) == len(ACTIVE) + 1
