"""Paper-Autopilot gegen echtes Postgres (Schema aus web/drizzle): T1, T3, T4, T7, T10, T14 aus docs/07."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import psycopg
import pytest
from helpers import instrument
from psycopg.types.json import Jsonb

from tradingengine.adapters.pg_paper import PaperRepo
from tradingengine.adapters.pg_store import PgStore
from tradingengine.core.candles import Candle
from tradingengine.core.strategies import ACTIVE
from tradingengine.ports import Command
from tradingengine.services import commands, paper

DSN = os.environ.get("TE_TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[2] / "web" / "drizzle"
pytestmark = [pytest.mark.pg, pytest.mark.skipif(not DSN, reason="TE_TEST_DATABASE_URL nicht gesetzt")]

T0 = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)
H4 = timedelta(hours=4)
AAA, BBB = "TEST:AAA/USD", "TEST:BBB/USD"
S2 = "s2-volume-breakout@1"
FEEDS = {f"{AAA}|4h": "OK", f"{BBB}|4h": "OK"}


def c4(instrument_id: str, start: datetime, o: str, h: str, low: str, c: str, volume: str = "100000") -> Candle:
    return Candle(instrument_id, "4h", start, start + H4, D(o), D(h), D(low), D(c), D(volume), 10, "test")


def near(a: D, b: D) -> bool:
    """Gleich bis auf die Rundung der numeric(28,10)-Spalten."""
    return abs(a - b) < D("0.000001")


class Env:
    def __init__(self, store: PgStore) -> None:
        self.store = store
        self.repo = PaperRepo(store.connection())
        self.next_command = 1

    def sql(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cur = self.store.connection().execute(query, params)  # type: ignore[arg-type]
        return cur.fetchall() if cur.description else []

    def command(self, type_: str, target: str | None = None, **params: Any) -> tuple[str, dict[str, Any]]:
        cmd = Command(self.next_command, type_, target, params, "user:test")
        self.next_command += 1
        return paper.handle_command(self.repo, cmd, self.now)

    now: datetime = T0 + timedelta(minutes=1)

    def tick(self, now: datetime) -> dict[str, Any]:
        self.now = now
        return paper.run_accounts(self.repo, FEEDS, now)

    def signal(self, instrument_id: str, created: datetime, entry: str = "100", stop: str = "96", strategy: str = S2, target: str | None = None) -> str:
        candle_close = created.replace(minute=0, second=0, microsecond=0)
        row = self.sql(
            """
            insert into signal (instrument_id, timeframe, candle_close, strategy_version_id, action, regime, score, entry, stop, target,
                                max_hold_bars, valid_until, triggers, counter, data_source, data_age_s, created_at, ref_level)
            values (%s, '4h', %s, %s, 'BUY', 'UP', 70, %s, %s, %s, 30, %s, %s, %s, 'test', 60, %s, %s) returning id
            """,
            (instrument_id, candle_close, strategy, D(entry), D(stop), None if target is None else D(target), candle_close + H4,
             Jsonb(["test"]), Jsonb([]), created, D(entry) - 1),
        )
        return str(row[0]["id"])

    def state(self, account_id: str) -> str:
        return str(self.sql("select state from autopilot where account_id = %s", (account_id,))[0]["state"])


@pytest.fixture()
def env() -> Iterator[Env]:
    assert DSN
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("drop schema if exists drizzle cascade; drop schema public cascade; create schema public;")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            for statement in path.read_text(encoding="utf-8").split("--> statement-breakpoint"):
                if statement.strip():
                    conn.execute(statement)  # type: ignore[arg-type]
    store = PgStore(DSN)
    insts = [
        instrument(AAA).__class__(AAA, "CRYPTO_SPOT", "TEST", "AAAUSD", AAA, "AAA", "USD", None, True, D("0.01"), D("0.0001"), D("5")),
        instrument(BBB).__class__(BBB, "CRYPTO_SPOT", "TEST", "BBBUSD", BBB, "BBB", "USD", None, True, D("0.01"), D("0.0001"), D("5")),
    ]
    store.upsert_instruments(insts)
    for s in ACTIVE:
        store.ensure_strategy_version(s.version)
    # ruhige Vorgeschichte: 40 flache 4h-Kerzen um 100 bis T0
    history = [c4(i, T0 - H4 * (40 - n), "100", "100.5", "99.5", "100") for i in (AAA, BBB) for n in range(40)]
    store.insert_candles(history, T0)
    e = Env(store)
    yield e
    store.reset()


def _create_and_start(env: Env, cash: str = "10000") -> str:
    status, result = env.command("PAPER_CREATE", name="Test Konto", start_cash=cash)
    assert status == "DONE"
    account_id = result["account_id"]
    assert env.state(account_id) == "READY"
    assert env.command("PAPER_START", account_id)[0] == "DONE"
    return str(account_id)


def test_t1_paper_starts_without_deposit_and_trades_full_cycle(env: Env) -> None:
    account_id = _create_and_start(env)
    assert account_id == "paper-test-konto" and env.state(account_id) == "ACTIVE"

    # Signal kurz nach Kerzenschluss T0 → Order mit Reservierung
    sig = env.signal(AAA, T0 + timedelta(seconds=90))
    out = env.tick(T0 + timedelta(minutes=2))
    assert out[account_id]["orders"] == 1
    order = env.sql("select * from trade_order")[0]
    assert (order["mode"], order["role"], order["state"], order["limit_price"]) == ("PAPER", "ENTRY", "ACCEPTED", D(100))
    res = env.sql("select * from reservation")[0]
    assert res["qty"] == order["qty"] and res["cash"] > 0 and res["risk"] <= D(50)  # 0.5 % von 10 000
    outcome = env.sql("select * from signal_outcome")[0]
    assert (str(outcome["signal_id"]), outcome["status"]) == (sig, "ORDERED")

    # T4: Wiederholung ohne neue Daten erzeugt weder eine zweite Order noch ein zweites Ergebnis
    env.tick(T0 + timedelta(minutes=3))
    assert len(env.sql("select 1 from trade_order")) == 1 and len(env.sql("select 1 from signal_outcome")) == 1

    # Folgekerze handelt durch das Limit → Fill, Trade offen, Schutzorder über die gefüllte Menge
    env.store.insert_candles([c4(AAA, T0, "100.4", "101", "99.2", "100.8"), c4(BBB, T0, "100", "100.5", "99.5", "100")], T0 + H4)
    env.tick(T0 + H4 + timedelta(minutes=1))
    trade = env.sql("select * from trade")[0]
    assert trade["status"] == "OPEN" and trade["qty"] == order["qty"]
    fill = env.sql("select * from fill")[0]
    assert fill["price"] == D(100) and fill["simulated"] is True and near(fill["fee"], order["qty"] * 100 * D("0.004"))
    stop = env.sql("select * from trade_order where role = 'PROTECT'")[0]
    assert (stop["state"], stop["qty"], stop["stop_price"]) == ("ACCEPTED", trade["qty"], D(96))
    assert not env.sql("select 1 from reservation")
    ep = env.sql("select * from episode")[0]
    assert near(ep["cash"], D(10000) - order["qty"] * 100 - fill["fee"])
    snap = env.sql("select * from equity_snapshot order by ts desc limit 1")[0]
    assert snap["ts"] == T0 + H4 and near(snap["equity"], ep["cash"] + trade["qty"] * D("100.8"))

    # Wiederholung desselben Kerzenschlusses verbucht nichts doppelt
    env.tick(T0 + H4 + timedelta(minutes=2))
    assert len(env.sql("select 1 from fill")) == 1 and len(env.sql("select 1 from equity_snapshot")) == 1

    # Kurslücke unter den Stop: Ausführung zur Eröffnung abzüglich Slippage, nicht zum geplanten Stop
    env.store.insert_candles([c4(AAA, T0 + H4, "95", "95.5", "94", "94.5"), c4(BBB, T0 + H4, "100", "100.5", "99.5", "100")], T0 + 2 * H4)
    env.tick(T0 + 2 * H4 + timedelta(minutes=1))
    closed = env.sql("select * from trade")[0]
    exit_fill = env.sql("select * from fill order by time desc limit 1")[0]
    assert closed["status"] == "CLOSED" and closed["exit_reason"] == "Stop"
    assert near(exit_fill["price"], D(95) * (1 - D("0.0005")))
    assert near(closed["net"], closed["exit_value"] - closed["entry_value"] - closed["entry_fees"] - closed["exit_fees"])
    assert closed["net"] < -closed["planned_risk"]  # schlechter als geplant: Stop ist keine Garantie
    ep = env.sql("select * from episode")[0]
    assert near(ep["cash"], D(10000) + closed["net"])
    assert near(ep["fees_paid"], closed["entry_fees"] + closed["exit_fees"])
    assert not env.sql("select 1 from trade_order where state not in ('FILLED','CANCELED')")
    assert env.state(account_id) == "ACTIVE"


def test_t3_paper_order_cannot_reference_a_live_account_mode(env: Env) -> None:
    account_id = _create_and_start(env)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        env.sql(
            """
            insert into trade_order (id, intent_key, mode, account_id, episode_id, instrument_id, strategy_version_id, timeframe, side, type,
                                     role, qty, state, created_at)
            values ('x', 'x', 'LIVE', %s, 1, %s, %s, '4h', 'BUY', 'LIMIT', 'ENTRY', 1, 'PREPARED', now())
            """,
            (account_id, AAA, S2),
        )
    assert not env.sql("select 1 from account where mode = 'LIVE'")


def test_t6_two_simultaneous_signals_share_budget_and_instrument_limit(env: Env) -> None:
    account_id = _create_and_start(env, cash="1000")
    env.signal(AAA, T0 + timedelta(seconds=70), stop="99.5")  # enger Stop → Kapitalgrenze bindet (20 % je Instrument)
    env.signal(BBB, T0 + timedelta(seconds=71), stop="99.5")
    env.signal(AAA, T0 + timedelta(seconds=72), stop="99.5", strategy="s1-trend-pullback@1")  # zweite Strategie, gleiches Instrument
    env.tick(T0 + timedelta(minutes=2))
    outcomes = env.sql("select status, reasons from signal_outcome order by created_at, status")
    assert sorted(o["status"] for o in outcomes) == ["BLOCKED", "ORDERED", "ORDERED"]
    blocked = next(o for o in outcomes if o["status"] == "BLOCKED")
    assert any("bereits ein Einstieg reserviert" in r for r in blocked["reasons"])
    reserved = env.sql("select sum(cash) as cash from reservation")[0]["cash"]
    assert reserved <= D(1000) * D("0.4") * D("1.004") and reserved <= D(900)  # zwei Instrumente à max. 20 %, Reserve bleibt frei
    assert env.state(account_id) == "ACTIVE"


def _open_position(env: Env) -> str:
    account_id = _create_and_start(env)
    env.signal(AAA, T0 + timedelta(seconds=90))
    env.tick(T0 + timedelta(minutes=2))
    env.store.insert_candles([c4(AAA, T0, "100.4", "101", "99.2", "100.8"), c4(BBB, T0, "100", "100.5", "99.5", "100")], T0 + H4)
    env.tick(T0 + H4 + timedelta(minutes=1))
    env.signal(BBB, T0 + H4 + timedelta(seconds=90))  # zusätzlich eine offene Einstiegsorder
    env.tick(T0 + H4 + timedelta(minutes=2))
    assert len(env.sql("select 1 from trade where status = 'OPEN'")) == 1
    assert len(env.sql("select 1 from trade_order where role = 'ENTRY' and state = 'ACCEPTED'")) == 1
    return account_id


def test_t10_pause_keeps_position_and_protection(env: Env) -> None:
    account_id = _open_position(env)
    status, result = env.command("PAPER_PAUSE", account_id)
    assert status == "DONE" and result["affected"] == 1 and env.state(account_id) == "ENTRIES_PAUSED"
    assert env.sql("select state, reason from trade_order where role = 'ENTRY' and instrument_id = %s", (BBB,))[0]["state"] == "CANCELED"
    assert not env.sql("select 1 from reservation")
    assert env.sql("select state from trade_order where role = 'PROTECT'")[0]["state"] == "ACCEPTED"  # Schutz bleibt
    # pausiert: neue Signale erzeugen keine Order, die Position wird aber weiter betreut (Stop löst aus)
    env.signal(BBB, T0 + 2 * H4 + timedelta(seconds=90))
    env.store.insert_candles([c4(AAA, T0 + H4, "100", "100.5", "95", "95.5"), c4(BBB, T0 + H4, "100", "100.5", "99.5", "100")], T0 + 2 * H4)
    env.tick(T0 + 2 * H4 + timedelta(minutes=2))
    assert env.sql("select status, exit_reason from trade")[0] == {"status": "CLOSED", "exit_reason": "Stop"}
    assert len(env.sql("select 1 from trade_order where role = 'ENTRY'")) == 2  # keine neue Einstiegsorder
    assert env.command("PAPER_START", account_id)[0] == "DONE" and env.state(account_id) == "ACTIVE"


def test_t10_orderly_stop_winds_down_then_stops(env: Env) -> None:
    account_id = _open_position(env)
    assert env.command("PAPER_STOP", account_id)[0] == "DONE"
    assert env.state(account_id) == "WINDING_DOWN"
    assert env.sql("select state from trade_order where role = 'PROTECT'")[0]["state"] == "ACCEPTED"
    assert len(env.sql("select 1 from trade where status = 'OPEN'")) == 1  # Position bleibt bis zu ihrem Exit
    env.tick(T0 + H4 + timedelta(minutes=5))
    assert env.state(account_id) == "WINDING_DOWN"  # nicht «gestoppt», solange Bestand existiert
    env.store.insert_candles([c4(AAA, T0 + H4, "100", "100.5", "95", "95.5"), c4(BBB, T0 + H4, "100", "100.5", "99.5", "100")], T0 + 2 * H4)
    env.tick(T0 + 2 * H4 + timedelta(minutes=1))
    assert env.state(account_id) == "STOPPED"


def test_t10_close_all_exits_at_next_candle_and_removes_protection_only_after_fill(env: Env) -> None:
    account_id = _open_position(env)
    env.now = T0 + H4 + timedelta(minutes=30)  # Bedienhandlung mitten in der laufenden Kerze
    status, result = env.command("PAPER_CLOSE_ALL", account_id)
    assert status == "DONE" and result["affected"] == 1 and env.state(account_id) == "WINDING_DOWN"
    exit_order = env.sql("select * from trade_order where role = 'EXIT' and type = 'MARKET'")[0]
    assert exit_order["state"] == "ACCEPTED"
    assert len(env.sql("select 1 from trade where status = 'OPEN'")) == 1  # «geschlossen» erst nach dem Fill
    # Summe offener Verkaufsorders ≤ Bestand: der Stop wurde durch den Exit ersetzt
    working_sells = env.sql("select sum(qty) as q from trade_order where side = 'SELL' and state = 'ACCEPTED'")[0]["q"]
    assert working_sells == env.sql("select qty from trade")[0]["qty"]
    # Die laufende Kerze zählt nicht mehr (Order kam später als die Karenz); die folgende füllt zur Eröffnung
    env.store.insert_candles([c4(AAA, T0 + H4, "100.8", "103", "100", "102")], T0 + 2 * H4)
    env.tick(T0 + 2 * H4 + timedelta(minutes=1))
    assert len(env.sql("select 1 from trade where status = 'OPEN'")) == 1
    env.store.insert_candles([c4(AAA, T0 + 2 * H4, "102", "103", "101", "102.5")], T0 + 3 * H4)
    env.tick(T0 + 3 * H4 + timedelta(minutes=1))
    trade = env.sql("select * from trade")[0]
    assert trade["status"] == "CLOSED" and trade["exit_reason"] == "Manuell geschlossen"
    assert near(trade["exit_value"], trade["qty"] * D(102) * (1 - D("0.0005")))
    assert env.state(account_id) == "STOPPED"


def test_t14_reset_keeps_old_episodes_untouched(env: Env) -> None:
    account_id = _open_position(env)
    status, result = env.command("PAPER_RESET", account_id, start_cash="5000")
    assert status == "REJECTED" and "Bestand" in result["reason"]  # Reset löscht keine offenen Positionen weg

    env.now = T0 + H4 + timedelta(minutes=30)
    env.command("PAPER_CLOSE_ALL", account_id)
    env.store.insert_candles([c4(AAA, T0 + H4, "100.8", "103", "100", "102")], T0 + 2 * H4)
    env.store.insert_candles([c4(AAA, T0 + 2 * H4, "102", "103", "101", "102.5")], T0 + 3 * H4)
    env.tick(T0 + 3 * H4 + timedelta(minutes=1))
    before = {
        "episode": env.sql("select number, start_cash, cash, realized, fees_paid from episode where number = 1"),
        "trades": env.sql("select * from trade order by id"),
        "fills": env.sql("select * from fill order by id"),
        "snapshots": env.sql("select * from equity_snapshot order by ts"),
    }
    status, result = env.command("PAPER_RESET", account_id, start_cash="5000")
    assert status == "DONE" and result["episode"] == 2 and env.state(account_id) == "READY"
    episodes = env.sql("select number, reason, start_cash, cash, ended_at from episode order by number")
    assert [(e["number"], e["reason"]) for e in episodes] == [(1, "NEW"), (2, "RESET")]
    assert episodes[0]["ended_at"] is not None and episodes[1]["ended_at"] is None and episodes[1]["cash"] == D(5000)
    assert env.sql("select number, start_cash, cash, realized, fees_paid from episode where number = 1") == before["episode"]
    assert env.sql("select * from trade order by id") == before["trades"]
    assert env.sql("select * from fill order by id") == before["fills"]
    assert env.sql("select * from equity_snapshot order by ts") == before["snapshots"]
    # neue Episode startet leer
    assert env.command("PAPER_START", account_id)[0] == "DONE"
    env.tick(T0 + 3 * H4 + timedelta(minutes=5))
    state = env.repo.load_state(account_id)
    assert not state.trades and not state.orders and state.account.cash == D(5000)


def test_commands_are_validated_and_audited(env: Env) -> None:
    env.sql("insert into command (type, target, params, issued_by) values ('PAPER_CREATE', null, %s, 'user:t')", (Jsonb({"name": "A", "start_cash": "-5"}),))
    env.sql("insert into command (type, target, params, issued_by) values ('PAPER_START', 'paper-gibts-nicht', '{}', 'user:t')")
    env.sql("insert into command (type, target, params, issued_by) values ('LIVE_BUY', null, '{}', 'user:t')")
    env.sql("insert into command (type, target, params, issued_by) values ('PAPER_CREATE', null, %s, 'user:t')", (Jsonb({"name": "Mein Konto", "start_cash": "2500"}),))
    assert commands.process_pending(env.store, T0, env.repo) == 4
    rows = env.sql("select type, status, result from command order by id")
    assert [r["status"] for r in rows] == ["REJECTED", "REJECTED", "REJECTED", "DONE"]
    assert rows[3]["result"]["account_id"] == "paper-mein-konto"
    assert len(env.sql("select 1 from audit_event")) == 4
    assert env.sql("select mode, currency from account") == [{"mode": "PAPER", "currency": "USD"}]
