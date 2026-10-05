"""Live-Orderweg gegen echtes Postgres (Schema aus web/drizzle) und den Fake-Anbieter: T2–T5, T7, T8, T16, T24–T26
aus docs/07 sowie jede harte Sperre einzeln. Kein Netzwerk, keine echten Zugangsdaten."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN
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
from tradingengine.live import manager
from tradingengine.live.fake_exchange import FakeKraken, FakeOrder
from tradingengine.live.locks import key_fingerprint
from tradingengine.live.manager import LiveConfig, cl_ord_id, run_tick
from tradingengine.live.repo import LiveRepo
from tradingengine.ports import Command
from tradingengine.schema_version import SCHEMA_VERSION
from tradingengine.services import commands as paper_commands
from tradingengine.services import paper

DSN = os.environ.get("TE_TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[2] / "web" / "drizzle"
pytestmark = [pytest.mark.pg, pytest.mark.skipif(not DSN, reason="TE_TEST_DATABASE_URL nicht gesetzt")]

T0 = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)  # Montag
H4 = timedelta(hours=4)
AAA, BBB = "TEST:AAA/USD", "TEST:BBB/USD"
S2, S3 = "s2-volume-breakout@1", "s3-mean-reversion@1"
ACC = "live-kraken"
FAKE_KEY = "FAKEKEY-T24-0123456789abcdefghijklmnopqrstuvwxyzABCDEF"
FAKE_SECRET = "T24SECRET-c2VjcmV0LXNlY3JldC1zZWNyZXQtc2VjcmV0LXNlY3JldA=="
Q = D("0.00000001")


def c4(instrument_id: str, start: datetime, o: str, h: str, low: str, c: str) -> Candle:
    return Candle(instrument_id, "4h", start, start + H4, D(o), D(h), D(low), D(c), D(100000), 10, "test")


class Env:
    def __init__(self, store: PgStore) -> None:
        self.store = store
        self.conn = store.connection()
        self.now = T0
        self.seq = 0
        self.fake = FakeKraken(clock=lambda: self.now, balances={"ZUSD": D(10000)})
        self.fake.set_quote("AAAUSD", "100", "100.05")
        self.fake.set_quote("BBBUSD", "100", "100.05")
        self.cfg = LiveConfig(enabled=True, keys=True, fingerprint=key_fingerprint(FAKE_KEY), schema_version=SCHEMA_VERSION)

    def sql(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cur = self.conn.execute(query, params)  # type: ignore[arg-type]
        return cur.fetchall() if cur.description else []

    def tick(self, minutes: float = 1, ex: Any = "fake", cfg: LiveConfig | None = None) -> dict[str, Any]:
        self.now += timedelta(minutes=minutes)
        return run_tick(self.conn, self.fake if ex == "fake" else ex, self.now, cfg or self.cfg)

    def command(self, type_: str, target: str | None = ACC, **params: Any) -> int:
        row = self.sql("insert into command (type, target, params, issued_by, issued_at) values (%s, %s, %s, 'user:test', %s) returning id",
                       (type_, target, Jsonb(params), self.now))
        return int(row[0]["id"])

    def result(self, cmd_id: int) -> dict[str, Any]:
        return self.sql("select status, result from command where id = %s", (cmd_id,))[0]

    def signal(self, instrument_id: str = AAA, entry: str = "100", stop: str = "96", strategy: str = S2, target: str | None = None,
               age_s: int = 20) -> str:
        created = self.now - timedelta(seconds=age_s)
        self.seq += 1  # eindeutiger Kerzenschluss je Testsignal (Unique-Index Version/Instrument/Zeitebene/Kerze)
        candle_close = created.replace(second=0, microsecond=0) - timedelta(seconds=self.seq)
        row = self.sql(
            """
            insert into signal (instrument_id, timeframe, candle_close, strategy_version_id, action, regime, score, entry, stop, target,
                                max_hold_bars, valid_until, triggers, counter, data_source, data_age_s, created_at, ref_level)
            values (%s, '4h', %s, %s, 'BUY', 'UP', 70, %s, %s, %s, 30, %s, %s, %s, 'test', 20, %s, %s) returning id
            """,
            (instrument_id, candle_close, strategy, D(entry), D(stop), None if target is None else D(target), candle_close + H4,
             Jsonb(["test"]), Jsonb([]), created, D(entry) - 1),
        )
        return str(row[0]["id"])

    def outcome(self, signal_id: str) -> dict[str, Any] | None:
        rows = self.sql("select status, reasons from signal_outcome where signal_id = %s and account_id = %s", (signal_id, ACC))
        return rows[0] if rows else None

    def state(self) -> str:
        return str(self.sql("select state from autopilot where account_id = %s", (ACC,))[0]["state"])

    def entry_order(self, signal_id: str) -> dict[str, Any]:
        return self.sql("select * from trade_order where signal_id = %s and role = 'ENTRY'", (signal_id,))[0]

    def adds(self, cl: str | None = None) -> int:
        """AddOrder-Aufrufe ohne die Rechteprüfung (validate=true) bei der Registrierung."""
        return sum(1 for m, c in self.fake.calls if m == "AddOrder" and c != "permcheck" and (cl is None or c == cl))

    def open_trade(self) -> dict[str, Any]:
        return self.sql("select * from trade where account_id = %s and status = 'OPEN'", (ACC,))[0]

    def stops_at_fake(self) -> list[Any]:
        return [o for o in self.fake.working() if o.ordertype == "stop-loss"]

    def mandate(self, level: int = 3, strategies: tuple[str, ...] = (S2, S3), instruments: tuple[str, ...] = (AAA, BBB), budget: str = "1000",
                policy: dict[str, Any] | None = None, step_up_age: timedelta = timedelta(minutes=1)) -> int:
        row = self.sql(
            "insert into mandate (account_id, autonomy_level, strategy_version_ids, instrument_ids, budget, policy, status, created_at, activated_at, "
            "activated_by, step_up_at) values (%s, %s, %s, %s, %s, %s, 'ACTIVE', %s, %s, 'user:test', %s) returning id",
            (ACC, level, Jsonb(list(strategies)), Jsonb(list(instruments)), D(budget), Jsonb(policy or {}), self.now, self.now, self.now - step_up_age),
        )
        return int(row[0]["id"])


def _migrate() -> None:
    assert DSN
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("drop schema if exists drizzle cascade; drop schema public cascade; create schema public;")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            for statement in path.read_text(encoding="utf-8").split("--> statement-breakpoint"):
                if statement.strip():
                    conn.execute(statement)  # type: ignore[arg-type]


@pytest.fixture()
def base() -> Iterator[Env]:
    _migrate()
    assert DSN
    store = PgStore(DSN)
    cls = instrument(AAA).__class__
    store.upsert_instruments([cls(i, "CRYPTO_SPOT", "TEST", f"{i[5:8]}USD", i, i[5:8], "USD", None, True, D("0.01"), D("0.0001"), D("5")) for i in (AAA, BBB)])
    for s in ACTIVE:
        store.ensure_strategy_version(s.version)
    store.insert_candles([c4(i, T0 - H4 * (40 - n), "100", "100.5", "99.5", "100") for i in (AAA, BBB) for n in range(40)], T0)
    for i in (AAA, BBB):
        store.set_feed_status("test", i, "4h", "OK", None, T0, T0)
    env = Env(store)
    env.sql("insert into channel_status (channel, configured, ok, last_test_sent_at, last_test_ack_at) values ('PUSHOVER', true, true, %s, %s)",
            (T0 - timedelta(days=1), T0 - timedelta(days=1)))
    env.sql("insert into fx_rate (base, quote, date, rate, source) values ('USD', 'CHF', %s, 0.8, 'test')", (T0.date().isoformat(),))
    for s in (S2, S3):
        env.sql("insert into approval (strategy_version_id, proposed_by, decision, decided_at, decided_by) values (%s, 'engine:lab', 'APPROVED_LIVE', %s, 'user:test')",
                (s, T0 - timedelta(days=1)))
        for g in ("G1", "G2", "G3"):
            env.sql("insert into gate_evaluation (strategy_version_id, gate, result, criteria, reasons, gate_config_version, evaluated_at) "
                    "values (%s, %s, 'PASSED', '[]', '[]', 'gates@1', %s)", (s, g, T0 - timedelta(days=2)))
    yield env
    store.reset()


@pytest.fixture()
def ready(base: Env) -> Env:
    """Konto registriert, Mandat Stufe 3 aktiv, Autopilot AKTIV, Wiederherstellungsdurchlauf hinter uns."""
    cmd = base.command("LIVE_ACCOUNT_REGISTER", None, name="Kraken Test")
    base.tick()
    assert base.result(cmd)["status"] == "DONE", base.result(cmd)
    base.mandate()
    base.tick()
    assert base.state() == "ACTIVE"
    return base


def _enter(env: Env, instrument_id: str = AAA, **kw: Any) -> tuple[str, str, D]:
    """Signal → Einstiegsorder → vollständiger Fill → Stop bei Kraken. Rückgabe: Signal-ID, cl_ord_id, Menge."""
    sid = env.signal(instrument_id, **kw)
    env.tick()
    order = env.entry_order(sid)
    env.fake.fill(order["id"], order["qty"], "100")
    env.tick()
    return sid, str(order["id"]), D(order["qty"])


# ───────────────────────────── Grundablauf ─────────────────────────────


def test_entry_fill_and_protective_stop_on_filled_quantity(ready: Env) -> None:
    env = ready
    sid = env.signal()
    env.tick()
    assert env.outcome(sid)["status"] == "ORDERED", env.outcome(sid)  # type: ignore[index]
    order = env.entry_order(sid)
    assert (order["mode"], order["state"], order["episode_id"], order["type"]) == ("LIVE", "ACCEPTED", 0, "LIMIT")
    assert order["id"] == cl_ord_id(order["intent_key"]) and order["intent_key"].endswith(f":{sid}:ENTRY")
    [ex] = env.fake.working()
    assert (ex.cl_ord_id, ex.side, ex.ordertype, ex.vol, ex.timeinforce) == (order["id"], "buy", "limit", order["qty"], "GTD")
    res = env.sql("select * from reservation where account_id = %s", (ACC,))[0]
    assert res["qty"] == order["qty"] and res["risk"] <= D(5) * D("1.0001")  # 0.5 % des Mandatsbudgets 1000
    assert env.outcome(sid)["status"] == "ORDERED"  # type: ignore[index]

    env.fake.fill(order["id"], order["qty"], "100")
    env.tick()
    trade = env.open_trade()
    assert trade["qty"] == order["qty"] and trade["episode_id"] == 0
    [stop] = env.stops_at_fake()
    assert stop.vol == trade["qty"] and stop.price == D(96)
    fill = env.sql("select * from fill")[0]
    assert fill["simulated"] is False and fill["id"].startswith("kraken:")
    assert not env.sql("select 1 from reservation")
    metrics = env.sql("select * from execution_metric")
    assert any(m["order_to_ack_ms"] is not None for m in metrics) and any(m["fill_to_protect_ms"] is not None for m in metrics)
    assert any(m["signal_to_order_ms"] is not None for m in metrics) and any(m["slippage_bps"] is not None for m in metrics)
    assert env.sql("select 1 from alert where title like '[LIVE] Ausgeführt:%%'")
    assert env.sql("select 1 from reconciliation where status = 'OK'")


# ───────────────────────────── harte Sperren einzeln ─────────────────────────────


def _withdraw_right(env: Env) -> None:
    env.sql("update account set permissions = permissions || '{\"withdraw\": true}' where id = %s", (ACC,))


def _other_key(env: Env) -> None:
    env.sql("update account set permissions = permissions || '{\"key_fingerprint\": \"other\"}' where id = %s", (ACC,))


def _suspend(env: Env) -> None:
    env.sql("update mandate set status = 'SUSPENDED'")


def _no_approval(env: Env) -> None:
    env.sql("insert into approval (strategy_version_id, proposed_by, decision, decided_at, decided_by) values (%s, 'x', 'WITHDRAWN', %s, 'user:test')",
            (S2, env.now))


def _gate_failed(env: Env) -> None:
    env.sql("insert into gate_evaluation (strategy_version_id, gate, result, criteria, reasons, gate_config_version, evaluated_at) "
            "values (%s, 'G3', 'FAILED', '[]', '[]', 'gates@1', %s)", (S2, env.now))


def _old_test_alarm(env: Env) -> None:
    env.sql("update channel_status set last_test_ack_at = %s", (env.now - timedelta(days=8),))


def _no_fx(env: Env) -> None:
    env.sql("delete from fx_rate")


def _stale_quote(env: Env) -> None:
    env.fake.set_quote("AAAUSD", "100", "100.05")
    env.fake.stale_quotes = True


def _feed_error(env: Env) -> None:
    env.sql("update feed_status set status = 'ERROR'")


def _recon_error(env: Env) -> None:
    # Order mit eigener Kennung beim Anbieter, die lokal fehlt → nicht auflösbar → FEHLER
    env.fake.orders["OX-ghost"] = FakeOrder("OX-ghost", "te00000000ghost000", "AAAUSD", "buy", "limit", D(1), D(50), env.now)


def _small_budget(env: Env) -> None:
    env.sql("update mandate set budget = 10")


def _wide_spread(env: Env) -> None:
    env.fake.set_quote("AAAUSD", "100", "101")


def _paused(env: Env) -> None:
    env.sql("update autopilot set state = 'ENTRIES_PAUSED'")


def _level1(env: Env) -> None:
    env.sql("update mandate set autonomy_level = 1")


@pytest.mark.parametrize(
    ("breaker", "fragment"),
    [
        (_withdraw_right, "Sperre 3"),
        (_other_key, "Sperre 3"),
        (_suspend, "Sperre 4"),
        (_level1, "Autonomiestufe 1"),
        (_no_approval, "APPROVED_LIVE"),
        (_gate_failed, "G3"),
        (_old_test_alarm, "Testalarm"),
        (_no_fx, "FX"),
        (_stale_quote, "nicht frisch"),
        (_feed_error, "nicht frisch"),
        (_recon_error, "Abgleich"),
        (_small_budget, "Mindestgrösse"),
        (_wide_spread, "Spread"),
        (_paused, "ENTRIES_PAUSED"),
    ],
)
def test_each_hard_lock_blocks_entries(ready: Env, breaker: Callable[[Env], None], fragment: str) -> None:
    env = ready
    breaker(env)
    sid = env.signal()
    env.tick()
    out = env.outcome(sid)
    if breaker is _suspend:
        assert out is None and env.state() == "SETUP"  # ohne Mandat werden Signale gar nicht erst für Live betrachtet
        assert "Kein aktives Mandat" in env.sql("select reason from autopilot")[0]["reason"]
    else:
        assert out is not None and out["status"] == "BLOCKED", out
        assert any(fragment in r for r in out["reasons"]), out["reasons"]
    assert env.adds() == 0 and not env.sql("select 1 from trade_order where role = 'ENTRY'")


def test_lock1_flag_off_and_lock2_no_keys_do_nothing(ready: Env) -> None:
    env = ready
    sid = env.signal()
    calls = len(env.fake.calls)
    assert env.tick(cfg=LiveConfig(False, True, env.cfg.fingerprint, SCHEMA_VERSION)) == {"live": "disabled"}
    assert env.tick(ex=None) == {"live": "no-keys"}
    assert len(env.fake.calls) == calls and env.outcome(sid) is None


def test_mandate_with_stale_step_up_is_not_accepted(base: Env) -> None:
    env = base
    env.command("LIVE_ACCOUNT_REGISTER", None)
    env.tick()
    mid = env.mandate(step_up_age=timedelta(minutes=6))
    env.tick()
    assert env.sql("select status from mandate where id = %s", (mid,))[0]["status"] == "SUSPENDED"
    assert env.state() == "SETUP"


def test_level2_prepares_approval_that_expires_without_answer(ready: Env) -> None:
    env = ready
    env.sql("update mandate set autonomy_level = 2")
    sid = env.signal()
    env.tick()
    [a] = env.sql("select * from order_approval")
    assert a["status"] == "PENDING" and env.adds() == 0
    assert env.sql("select 1 from alert where title like '%%wartet auf deine Freigabe%%'")
    env.now = a["expires_at"]
    env.tick()
    assert env.sql("select status from order_approval")[0]["status"] == "EXPIRED"
    assert env.outcome(sid)["status"] == "BLOCKED" and env.adds() == 0  # type: ignore[index]


def test_level2_approved_order_is_rechecked_and_sent(ready: Env) -> None:
    env = ready
    env.sql("update mandate set autonomy_level = 2")
    sid = env.signal()
    env.tick()
    [a] = env.sql("select id from order_approval")
    cmd = env.command("ORDER_APPROVAL_DECIDE", approval_id=a["id"], decision="APPROVED")
    env.tick()
    assert env.result(cmd)["status"] == "DONE"
    assert env.entry_order(sid)["state"] == "ACCEPTED" and env.adds() == 1


# ───────────────────────────── T2 ─────────────────────────────


def test_t2_live_trails_and_exits_while_paper_is_stopped(ready: Env) -> None:
    env = ready
    # Paper-Konto anlegen und stoppen: für Live keine Eingangsgrösse
    prepo = PaperRepo(env.conn)
    status, res = paper.handle_command(prepo, Command(9001, "PAPER_CREATE", None, {"name": "P", "start_cash": "1000"}, "user:test"), env.now)
    assert status == "DONE"
    paper.handle_command(prepo, Command(9002, "PAPER_START", res["account_id"], {}, "user:test"), env.now)
    paper.handle_command(prepo, Command(9003, "PAPER_PAUSE", res["account_id"], {}, "user:test"), env.now)

    _sid, _cl, qty = _enter(env)
    start = env.open_trade()["managed_through"]
    env.store.insert_candles([c4(AAA, start, "100", "111", "100", "110"), c4(AAA, start + H4, "110", "121", "110", "120")], env.now)
    env.now = start + 2 * H4
    env.tick()
    trade = env.open_trade()
    [stop] = env.stops_at_fake()
    assert trade["current_stop"] > D(96) and stop.price == trade["current_stop"] and stop.vol == qty  # nur enger
    assert ("AmendOrder", stop.cl_ord_id) in env.fake.calls
    env.fake.trigger_stops("AAAUSD", str(trade["current_stop"] - 1))
    env.tick()
    closed = env.sql("select * from trade where account_id = %s", (ACC,))[0]
    assert closed["status"] == "CLOSED" and closed["exit_reason"] == "Stop-Loss bei Kraken ausgelöst" and closed["net"] is not None
    assert env.state() == "ACTIVE"
    live_orders = env.sql("select * from trade_order where mode = 'LIVE'")
    assert live_orders and all(o["account_id"] == ACC and o["episode_id"] == 0 for o in live_orders)
    # umgekehrt: Live gestoppt → Paper arbeitet weiter
    env.command("LIVE_STOP")
    env.tick()
    assert paper.run_accounts(prepo, {}, env.now) is not None


# ───────────────────────────── T3 ─────────────────────────────


def test_t3_fuzz_paper_and_lab_events_never_reach_live(base: Env) -> None:
    import random

    env = base
    env.command("LIVE_ACCOUNT_REGISTER", None)
    env.tick()  # Konto registriert, aber kein Mandat
    rng = random.Random(7)
    prepo = PaperRepo(env.conn)
    paper.handle_command(prepo, Command(1, "PAPER_CREATE", None, {"name": "F", "start_cash": "5000"}, "user:test"), env.now)
    pid = prepo.accounts()[0]["id"]
    for n in range(400):
        kind = rng.choice(["signal", "paper_cmd", "lab", "tick"])
        if kind == "signal":
            env.signal(rng.choice([AAA, BBB]), strategy=rng.choice([S2, S3]), age_s=rng.randint(1, 50))
        elif kind == "paper_cmd":
            paper.handle_command(prepo, Command(100 + n, rng.choice(["PAPER_START", "PAPER_PAUSE", "PAPER_STOP"]), pid, {}, "user:test"), env.now)
        elif kind == "lab":
            env.sql("insert into experiment (kind, strategy, hypothesis, config, status, issued_by) values ('BACKTEST', 's2', 'h', '{}', 'RUNNING', 'engine:lab')")
        else:
            paper.run_accounts(prepo, {f"{AAA}|4h": "OK", f"{BBB}|4h": "OK"}, env.now)
            env.tick(0.5)
    assert env.adds() == 0
    assert not env.sql("select 1 from trade_order where mode = 'LIVE'")


# ───────────────────────────── T4 ─────────────────────────────


def test_t4_duplicate_signal_delivery_and_parallel_process(ready: Env) -> None:
    env = ready
    sid = env.signal()
    assert DSN
    with psycopg.connect(DSN, autocommit=True) as other:
        other.execute("select pg_advisory_lock(%s)", (7_351_004,))
        assert run_tick(env.conn, env.fake, env.now, env.cfg) == {"live": "busy"}  # zweiter Prozess wartet
        other.execute("select pg_advisory_unlock(%s)", (7_351_004,))
    for _ in range(3):
        env.tick()
    cl = env.entry_order(sid)["id"]
    assert env.adds(cl) == 1 and len(env.fake.working(cl)) == 1
    assert len(env.sql("select 1 from trade_order where role = 'ENTRY'")) == 1 and len(env.sql("select 1 from reservation")) == 1


def test_t4_crash_after_storing_intent_sends_once_after_restart(ready: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env = ready
    sid = env.signal()
    real = manager._send_checked
    calls = {"n": 0}

    def crash(ctx: Any, mandate: Any, reasons: list[str]) -> int:
        calls["n"] += 1
        if calls["n"] == 2:  # zweiter Aufruf im Durchlauf = nach dem Speichern der neuen Absicht
            raise RuntimeError("Prozess beendet")
        return real(ctx, mandate, reasons)

    monkeypatch.setattr(manager, "_send_checked", crash)
    with pytest.raises(RuntimeError):
        env.tick()
    monkeypatch.setattr(manager, "_send_checked", real)
    assert env.entry_order(sid)["state"] == "CHECKED" and env.adds() == 0
    env.tick()  # Wiederherstellung: Abgleich und Schutz, keine Einstiege
    assert env.entry_order(sid)["state"] == "CHECKED"
    env.tick()
    cl = env.entry_order(sid)["id"]
    assert env.entry_order(sid)["state"] == "ACCEPTED" and env.adds(cl) == 1
    env.tick()
    assert env.adds(cl) == 1 and len(env.sql("select 1 from reservation")) == 1


def test_t4_crash_after_send_is_resolved_without_resend(ready: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env = ready
    sid = env.signal()
    real_add = env.fake.add_order

    def add_then_crash(req: Any) -> str:
        real_add(req)
        raise RuntimeError("Prozess nach dem Senden beendet")

    monkeypatch.setattr(env.fake, "add_order", add_then_crash)
    with pytest.raises(RuntimeError):
        env.tick()
    monkeypatch.setattr(env.fake, "add_order", real_add)
    assert env.entry_order(sid)["state"] == "SUBMITTED"
    env.tick()
    env.tick()
    cl = env.entry_order(sid)["id"]
    assert env.entry_order(sid)["state"] == "ACCEPTED"
    assert sum(1 for o in env.fake.orders.values() if o.cl_ord_id == cl) == 1


# ───────────────────────────── T5 ─────────────────────────────


def test_t5_timeout_after_accept_resolves_to_accepted_and_blocks_meanwhile(ready: Env) -> None:
    env = ready
    env.fake.script["AddOrder"] = ["timeout_after_accept"]
    s1, s2 = env.signal(AAA), env.signal(BBB, strategy=S3, target="112")
    env.tick()
    o1 = env.entry_order(s1)
    assert o1["state"] == "UNKNOWN" and env.adds() == 1  # zweite Order wartet, keine zweite Übermittlung
    assert env.sql("select 1 from alert where kind = 'live.order_unknown' and status = 'OPEN'")
    env.tick()
    assert env.entry_order(s1)["state"] == "ACCEPTED" and env.adds(o1["id"]) == 1
    assert env.entry_order(s2)["state"] == "ACCEPTED"


def test_t5_lost_order_closed_as_not_placed_after_two_negative_answers(ready: Env) -> None:
    env = ready
    env.fake.script["AddOrder"] = ["lost"]
    sid = env.signal()
    env.tick()
    cl = env.entry_order(sid)["id"]
    assert env.entry_order(sid)["state"] == "UNKNOWN" and env.sql("select 1 from reservation")
    later = env.signal(BBB)
    env.tick()
    assert env.entry_order(sid)["state"] == "UNKNOWN"  # erst eine Negativabfrage; währenddessen keine Einstiege
    env.tick()
    order = env.entry_order(sid)
    assert order["state"] == "REJECTED" and "Nicht platziert" in order["reason"]
    assert not env.sql("select 1 from reservation where intent_key = %s", (order["intent_key"],))
    for _ in range(3):
        env.tick()
    assert env.adds(cl) == 1  # kein automatischer Neuversuch
    out = env.outcome(later)
    assert out is not None and out["status"] == "BLOCKED" and any("unbekanntem Status" in r for r in out["reasons"])


# ───────────────────────────── T7 ─────────────────────────────


def test_t7_partial_fills_cancel_after_confirmation_and_late_fill_race(ready: Env) -> None:
    env = ready
    sid = env.signal()
    env.tick()
    order = env.entry_order(sid)
    total = D(order["qty"])
    q1, q2 = (total * D("0.3")).quantize(Q, ROUND_DOWN), (total * D("0.4")).quantize(Q, ROUND_DOWN)
    env.fake.fill(order["id"], q1, "100")
    env.fake.fill(order["id"], q2, "99.9")
    env.tick()
    assert env.open_trade()["qty"] == q1 + q2
    [stop] = env.stops_at_fake()
    assert stop.vol == q1 + q2
    res = env.sql("select qty from reservation")[0]
    assert res["qty"] == total - q1 - q2

    # Pause → Storno des Rests; Bestätigung kommt verzögert, dazwischen noch ein Fill (Rennen)
    env.fake.cancel_mode = "pending"
    env.command("LIVE_PAUSE")
    env.tick()
    assert env.entry_order(sid)["state"] == "CANCEL_REQUESTED"
    assert env.sql("select qty from reservation")[0]["qty"] == total - q1 - q2  # erst Bestätigung gibt frei
    q3 = (total * D("0.1")).quantize(Q, ROUND_DOWN)
    env.fake.fill(order["id"], q3, "100")
    env.fake.confirm_pending_cancels()
    env.tick()
    assert env.entry_order(sid)["state"] == "CANCELED"
    held = q1 + q2 + q3
    assert env.open_trade()["qty"] == held and not env.sql("select 1 from reservation")
    [stop] = env.stops_at_fake()
    assert stop.vol == held
    fills = env.sql("select qty, price, fee from fill")
    assert sum(f["qty"] for f in fills) == held
    assert env.fake.balance["ZUSD"] == D(10000) - sum(f["qty"] * f["price"] + f["fee"] for f in fills)
    # doppelt geliefertes Fill-Event (Kraken liefert dieselben Trades bei jedem Abgleich) ändert nichts
    env.tick()
    assert len(env.sql("select 1 from fill")) == 3 and env.open_trade()["qty"] == held


def test_t7_late_fill_between_cancel_request_and_confirmation(ready: Env) -> None:
    env = ready
    sid = env.signal()
    env.tick()
    order = env.entry_order(sid)
    total = D(order["qty"])
    half = (total / 2).quantize(Q, ROUND_DOWN)
    env.fake.fill(order["id"], half, "100")
    env.fake.late_fill_on_cancel = (total / 4).quantize(Q, ROUND_DOWN)
    env.command("LIVE_PAUSE")
    env.tick()
    held = half + (total / 4).quantize(Q, ROUND_DOWN)
    assert env.entry_order(sid)["state"] == "CANCELED" and env.open_trade()["qty"] == held
    [stop] = env.stops_at_fake()
    assert stop.vol == held and not env.sql("select 1 from reservation")


def test_t7_stop_partially_filled_while_close_all_never_oversells(ready: Env) -> None:
    env = ready
    _sid, _cl, qty = _enter(env)
    [stop] = env.stops_at_fake()
    part = (qty * D("0.5")).quantize(Q, ROUND_DOWN)
    env.fake.fill(stop.txid, part, "96")  # Stop teilweise ausgeführt
    env.command("LIVE_CLOSE_ALL", step_up_at=env.now.isoformat())
    env.tick()
    trades = env.sql("select * from trade where account_id = %s", (ACC,))
    assert trades[0]["status"] == "CLOSED" and env.fake._bal("AAA") == 0
    sells = env.sql("select o.role, sum(f.qty) as q from fill f join trade_order o on o.id = f.order_id where o.side = 'SELL' group by o.role")
    assert sum(s["q"] for s in sells) == qty
    names = [m for m, _ in env.fake.calls]
    assert names.index("CancelOrder") < len(names) - 1 - names[::-1].index("AddOrder")  # Storno vor dem Exit
    assert env.state() in ("WINDING_DOWN", "STOPPED")
    env.tick()
    assert env.state() == "STOPPED"


def test_close_all_requires_fresh_step_up(ready: Env) -> None:
    env = ready
    no = env.command("LIVE_CLOSE_ALL")
    old = env.command("LIVE_CLOSE_ALL", step_up_at=(env.now - timedelta(minutes=6)).isoformat())
    env.tick()
    assert env.result(no)["status"] == "REJECTED" and env.result(old)["status"] == "REJECTED"
    assert env.state() == "ACTIVE"


# ───────────────────────────── T8 ─────────────────────────────


def test_t8_blocked_entries_keep_protection_and_stop_exit_runs(ready: Env) -> None:
    env = ready
    _enter(env)
    _no_fx(env)
    _stale_quote(env)
    sid = env.signal(BBB)
    env.tick()
    out = env.outcome(sid)
    assert out is not None and out["status"] == "BLOCKED" and any("FX" in r for r in out["reasons"])
    assert len(env.stops_at_fake()) == 1
    env.fake.trigger_stops("AAAUSD", "95")
    env.tick()
    assert env.sql("select status from trade where account_id = %s", (ACC,))[0]["status"] == "CLOSED"


def test_unreachable_exchange_enters_recovery_and_returns(ready: Env) -> None:
    env = ready
    _enter(env)
    env.fake.status = "maintenance"
    sid = env.signal(BBB)
    out = env.tick()
    assert out[ACC]["ok"] is False and env.state() == "RECOVERY"
    assert env.sql("select 1 from alert where kind = 'live.unreachable' and status = 'OPEN'")
    assert env.outcome(sid) is None and len(env.stops_at_fake()) == 1
    env.fake.status = "online"
    env.tick()
    assert env.state() == "ACTIVE"
    assert env.outcome(sid)["status"] == "BLOCKED"  # type: ignore[index]  # verpasstes Signal wird nicht nachgeholt


# ───────────────────────────── Schutz ─────────────────────────────


def test_protection_repair_fails_twice_triggers_critical_alert_and_emergency_exit(ready: Env) -> None:
    env = ready
    sid = env.signal()
    env.tick()
    order = env.entry_order(sid)
    env.fake.fill(order["id"], order["qty"], "100")
    env.fake.script["AddOrder"] = ["reject:EOrder:Invalid price", "reject:EOrder:Invalid price"]
    env.tick()
    assert env.sql("select 1 from alert where kind = 'live.protection' and level = 'CRITICAL'")
    trade = env.sql("select * from trade where account_id = %s", (ACC,))[0]
    assert trade["status"] == "CLOSED" and trade["exit_reason"].startswith("Notfall")
    assert env.fake._bal("AAA") == 0 and env.state() == "ENTRIES_PAUSED"


def test_stop_canceled_at_exchange_is_replaced_within_tick(ready: Env) -> None:
    env = ready
    _sid, _cl, qty = _enter(env)
    [stop] = env.stops_at_fake()
    env.fake.cancel_order(stop.cl_ord_id or "")
    env.tick()
    [new] = env.stops_at_fake()
    assert new.cl_ord_id != stop.cl_ord_id and new.vol == qty
    assert env.sql("select 1 from alert where kind = 'live.stop_canceled'")


def test_maintenance_cancel_only_blocks_new_orders_but_keeps_stop(ready: Env) -> None:
    env = ready
    _enter(env)
    env.fake.status = "cancel_only"
    sid = env.signal(BBB)
    env.tick()
    out = env.outcome(sid)
    assert out is not None and any("cancel_only" in r for r in out["reasons"])
    assert len(env.stops_at_fake()) == 1


def test_synthetic_target_cancels_stop_before_exit(ready: Env) -> None:
    env = ready
    _sid, _cl, qty = _enter(env, strategy=S3, target="112")
    [stop] = env.stops_at_fake()
    env.fake.set_quote("AAAUSD", "112.5", "112.6")
    env.tick()
    trade = env.sql("select * from trade where account_id = %s", (ACC,))[0]
    assert trade["status"] == "CLOSED" and trade["exit_reason"].startswith("Ziel")
    names = [(m, c) for m, c in env.fake.calls if m in ("CancelOrder", "AddOrder")]
    assert names.index(("CancelOrder", stop.cl_ord_id)) < len(names) - 1
    assert env.fake._bal("AAA") == 0


def test_emergency_with_close_policy_exits_and_hold_policy_keeps_stop(ready: Env) -> None:
    env = ready
    _enter(env)
    env.command("LIVE_EMERGENCY")
    env.tick()
    assert env.state() == "EMERGENCY" and len(env.stops_at_fake()) == 1  # Standard: halten mit Schutz
    env.sql("update mandate set policy = '{\"emergency\": \"CLOSE\"}'")
    env.command("LIVE_EMERGENCY")
    env.tick()
    assert env.sql("select status from trade where account_id = %s", (ACC,))[0]["status"] == "CLOSED" and env.fake._bal("AAA") == 0


def test_mandate_suspend_blocks_entries_but_protection_continues(ready: Env) -> None:
    env = ready
    _enter(env)
    env.command("MANDATE_SUSPEND")
    sid = env.signal(BBB)
    env.tick()
    assert env.state() == "ENTRIES_PAUSED"
    assert env.outcome(sid) is None or env.outcome(sid)["status"] == "BLOCKED"  # type: ignore[index]
    assert env.adds() == 2 and len(env.stops_at_fake()) == 1


# ───────────────────────────── T16 ─────────────────────────────


def test_t16_foreign_position_counted_never_traded(ready: Env) -> None:
    env = ready
    env.fake.deposit_foreign("BBB", "5")
    foreign_txid = env.fake.add_foreign_order("AAAUSD", "buy", "1", "50")
    env.tick()
    rec = env.sql("select * from reconciliation order by id desc limit 1")[0]
    assert rec["status"] == "OK"
    kinds = {d["kind"] for d in rec["diffs"]}
    assert {"FOREIGN_POSITION", "FOREIGN_ORDER", "SNAPSHOT"} <= kinds
    assert env.sql("select 1 from alert where kind = 'live.foreign_position'")
    sid = env.signal(BBB)
    env.tick()
    out = env.outcome(sid)
    assert out is not None and out["status"] == "BLOCKED" and any("fremd" in r for r in out["reasons"])
    assert env.fake._bal("BBB") == 5 and env.fake.orders[foreign_txid].status == "open"
    assert ("CancelOrder", foreign_txid) not in env.fake.calls


def test_t16_manual_partial_sell_shrinks_position_and_stop(ready: Env) -> None:
    env = ready
    _sid, _cl, qty = _enter(env)
    part = (qty / 3).quantize(Q, ROUND_DOWN)
    # Kraken würde den Verkauf wegen des Stops ablehnen, ausser der Nutzer hat den Stop angepasst – hier simuliert
    env.fake.manual_sell("AAA", str(part))
    env.tick()
    trade = env.open_trade()
    assert trade["qty"] == qty - part
    [stop] = env.stops_at_fake()
    assert stop.vol == qty - part
    assert env.sql("select 1 from alert where kind = 'live.manual_sell'")
    assert env.sql("select status from reconciliation order by id desc limit 1")[0]["status"] == "DIFF"
    env.tick()
    assert env.sql("select status from reconciliation order by id desc limit 1")[0]["status"] == "OK"


def test_t16_full_manual_sell_cancels_leftover_stop_before_rebuy(ready: Env) -> None:
    """Wird eine verwaltete Position ausserhalb von TradingEngine ganz verkauft, darf ihr Stop nicht bei Kraken
    liegen bleiben – sonst verkaufte er einen späteren manuellen Rückkauf."""
    env = ready
    _sid, _cl, qty = _enter(env)
    assert env.stops_at_fake()
    env.fake.manual_sell("AAA", str(qty))
    env.tick()
    assert env.sql("select status, exit_reason from trade order by opened_at desc limit 1")[0]["status"] == "CLOSED"
    env.tick()  # Storno bestätigen lassen
    assert env.stops_at_fake() == []
    env.fake.deposit_foreign("AAA", str(qty))
    env.tick()
    assert env.fake._bal("AAA") == qty  # Rückkauf bleibt unangetastet (fremd, nicht verwaltet)
    assert not [o for o in env.fake.working() if o.side == "sell" and o.pair == "AAAUSD"]


def _assign(env: Env, instrument_id: str = BBB, strategy: str = S2, stop: str = "95", step_up: bool = True) -> int:
    params: dict[str, Any] = {"instrument_id": instrument_id, "strategy_version_id": strategy, "stop": stop}
    if step_up:
        params["step_up_at"] = env.now.isoformat()
    return env.command("LIVE_ASSIGN_POSITION", **params)


def _never_short(env: Env, asset: str = "BBB") -> None:
    """Summe offener Verkaufsorders bei Kraken ≤ Bestand – es entsteht nie eine Short-Position."""
    sells = sum((o.vol - o.vol_exec for o in env.fake.working() if o.side == "sell" and o.pair == f"{asset}USD"), D(0))
    assert env.fake._bal(asset) >= 0 and sells <= env.fake._bal(asset)


def test_t16_assign_foreign_position_places_stop_for_exact_quantity_and_trails(ready: Env) -> None:
    env = ready
    env.fake.deposit_foreign("BBB", "0.5")
    env.tick()
    assert env.sql("select 1 from alert where kind = 'live.foreign_position' and status <> 'RESOLVED'")
    assert not env.stops_at_fake()  # fremd: nie gehandelt

    cmd = _assign(env)
    env.tick()
    res = env.result(cmd)
    assert res["status"] == "DONE", res
    assert res["result"]["qty"] == "0.5" and res["result"]["cost_basis_unknown"] is True
    trade = env.open_trade()
    assert (trade["instrument_id"], trade["strategy_version_id"], trade["qty"], trade["signal_id"]) == (BBB, S2, D("0.5"), None)
    assert trade["entry_value"] == D(50) and trade["entry_fees"] == 0 and trade["planned_stop"] == D(95) and trade["current_stop"] == D(95)
    live = trade["exit_plan"]["_live"]
    assert live["adopted_cost_basis_unknown"] is True and live["adopted_at"] and trade["exit_plan"]["trail_atr"] == 2.5
    # Schutz im selben Durchlauf: genau ein Stop-Loss über genau die übernommene Menge
    [stop] = env.stops_at_fake()
    assert (stop.pair, stop.vol, stop.price) == ("BBBUSD", D("0.5"), D(95))
    assert not env.sql("select 1 from alert where kind = 'live.foreign_position' and status <> 'RESOLVED'")
    assert env.sql("select 1 from alert where kind = 'live.position_assigned'")
    assert env.sql("select 1 from audit_event where kind = 'live.position.assigned' and object = %s", (f"trade:{trade['id']}",))
    _never_short(env)

    env.tick()
    rec = env.sql("select * from reconciliation order by id desc limit 1")[0]
    snap = next(d for d in rec["diffs"] if d["kind"] == "SNAPSHOT")
    assert rec["status"] == "OK" and BBB not in snap["foreign"] and D(snap["managed"][BBB]) == D("0.5")
    assert not [d for d in rec["diffs"] if d["kind"] == "FOREIGN_POSITION"]
    assert len(env.stops_at_fake()) == 1

    # Trailing nur enger
    start = trade["managed_through"]
    env.store.insert_candles([c4(BBB, start, "100", "111", "100", "110"), c4(BBB, start + H4, "110", "121", "110", "120")], env.now)
    env.now = start + 2 * H4
    env.tick()
    tightened = env.open_trade()["current_stop"]
    [stop] = env.stops_at_fake()
    assert tightened > D(95) and stop.price == tightened and stop.vol == D("0.5")
    env.store.insert_candles([c4(BBB, start + 2 * H4, "120", "120", "100", "101")], env.now)
    env.now = start + 3 * H4
    env.tick()
    assert env.open_trade()["current_stop"] >= tightened and env.stops_at_fake()[0].price >= tightened
    _never_short(env)

    # Ergebnis ab Übernahmewert, Kostenbasis davor unbekannt (aber net berechenbar)
    env.fake.trigger_stops("BBBUSD", str(env.open_trade()["current_stop"] - 1))
    env.tick()
    closed = env.sql("select * from trade where id = %s", (trade["id"],))[0]
    assert closed["status"] == "CLOSED" and closed["net"] is not None and closed["entry_value"] == D(50)
    assert closed["exit_plan"]["_live"]["adopted_cost_basis_unknown"] is True
    _never_short(env)


def test_t16_assign_rejections(ready: Env) -> None:
    env = ready
    env.fake.deposit_foreign("BBB", "0.5")
    env.tick()
    cases = {
        "Step-up": _assign(env, step_up=False),
        "nicht für Live freigegeben": _assign(env, strategy="s1-trend-pullback@1"),
        "keine fremde Menge": _assign(env, instrument_id=AAA),
        "unter dem aktuellen Geldkurs": _assign(env, stop="100"),
        "Tick-Grösse": _assign(env, stop="95.001"),
        "positive Zahl": _assign(env, stop="-1"),
    }
    old = env.command("LIVE_ASSIGN_POSITION", instrument_id=BBB, strategy_version_id=S2, stop="95", step_up_at=(env.now - timedelta(minutes=6)).isoformat())
    env.tick()
    for fragment, cmd in {**cases, "Step-up ": old}.items():
        r = env.result(cmd)
        assert r["status"] == "REJECTED" and fragment.strip() in r["result"]["reason"], (fragment, r)
    assert not env.sql("select 1 from trade where account_id = %s", (ACC,))
    assert not env.stops_at_fake()

    # Risiko je Trade zu gross → Ablehnung mit Zahlen und Vorschlag für einen engeren Stop
    env.fake.deposit_foreign("BBB", "4.5")
    env.tick()
    big = _assign(env, stop="95")
    env.tick()
    r = env.result(big)
    assert r["status"] == "REJECTED" and "Grenze je Trade von 5.00 USD" in r["result"]["reason"] and "Stop ab etwa" in r["result"]["reason"], r
    hint = D(r["result"]["reason"].rsplit("Stop ab etwa ", 1)[1].split(" ")[0])
    tight = _assign(env, stop=str(hint))
    env.tick()
    assert env.result(tight)["status"] == "DONE", env.result(tight)
    assert env.open_trade()["qty"] == D(5) and env.stops_at_fake()[0].vol == D(5)

    # bereits verwaltet
    again = _assign(env)
    env.tick()
    assert env.result(again)["status"] == "REJECTED" and "bereits verwaltet" in env.result(again)["result"]["reason"]
    _never_short(env)


def test_t16_assign_rejected_when_total_open_risk_too_large_or_quote_unknown(ready: Env) -> None:
    env = ready
    env.fake.deposit_foreign("BBB", "0.5")
    env.sql("update mandate set policy = policy || '{\"risk\": {\"max_open_risk\": \"0.002\"}}'")
    env.tick()
    cmd = _assign(env)
    env.tick()
    r = env.result(cmd)
    assert r["status"] == "REJECTED" and "Offenes Risiko wäre 2.90 USD > Grenze 2.00 USD" in r["result"]["reason"], r
    env.sql("update mandate set policy = '{}'")
    del env.fake.quotes["BBBUSD"]
    cmd = _assign(env)
    env.tick()
    assert env.result(cmd)["status"] == "REJECTED" and "Kurs unbekannt" in env.result(cmd)["result"]["reason"]
    assert not env.sql("select 1 from trade where account_id = %s", (ACC,))


def test_t16_manual_partial_sell_after_assignment_shrinks_quantity_and_stop(ready: Env) -> None:
    env = ready
    env.fake.deposit_foreign("BBB", "0.5")
    env.tick()
    cmd = _assign(env)
    env.tick()
    assert env.result(cmd)["status"] == "DONE"
    env.fake.manual_sell("BBB", "0.2")
    env.tick()
    trade = env.open_trade()
    assert trade["qty"] == D("0.3")
    [stop] = env.stops_at_fake()
    assert stop.vol == D("0.3")
    _never_short(env)
    env.tick()
    assert env.sql("select status from reconciliation order by id desc limit 1")[0]["status"] == "OK"
    _never_short(env)


def test_paper_tick_leaves_assign_command_pending(ready: Env) -> None:
    env = ready
    cmd = _assign(env)
    paper_commands.process_pending(env.store, env.now, PaperRepo(env.conn))
    assert env.result(cmd)["status"] == "PENDING"


def test_t16_unsupported_protection_keeps_autopilot_in_setup(base: Env) -> None:
    env = base
    env.command("LIVE_ACCOUNT_REGISTER", None)
    env.tick()
    env.mandate(policy={"protection": "OCO_AT_EXCHANGE"})
    env.tick()
    assert env.state() == "SETUP"
    reason = env.sql("select reason from autopilot")[0]["reason"]
    assert "nicht unterstützt" in reason
    sid = env.signal()
    env.tick()
    assert env.adds() == 0 and env.outcome(sid)["status"] == "BLOCKED"  # type: ignore[index]


# ───────────────────────────── Registrierung ─────────────────────────────


def test_register_refuses_key_with_withdraw_right_and_unclear_without_confirmation(base: Env) -> None:
    env = base
    env.fake.withdraw_probe = "ALLOWED"
    c1 = env.command("LIVE_ACCOUNT_REGISTER", None)
    env.tick()
    assert env.result(c1)["status"] == "REJECTED" and not env.sql("select 1 from account where mode = 'LIVE'")
    env.fake.withdraw_probe = "UNCLEAR"
    c2 = env.command("LIVE_ACCOUNT_REGISTER", None)
    env.tick()
    assert env.result(c2)["status"] == "REJECTED"
    c3 = env.command("LIVE_ACCOUNT_REGISTER", None, withdraw_absent_confirmed=True, step_up_at=env.now.isoformat())
    env.tick()
    assert env.result(c3)["status"] == "DONE"
    perms = env.sql("select permissions from account where id = %s", (ACC,))[0]["permissions"]
    assert perms["withdraw"] is False and perms["withdraw_check"] == "USER_CONFIRMED" and perms["trade"] is True


# ───────────────────────────── T24 ─────────────────────────────


def test_t24_secrets_never_in_database_alerts_or_logs(ready: Env, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    env = ready
    env.fake.script["AddOrder"] = ["timeout_after_accept"]
    env.signal()
    env.tick()
    env.tick()
    dump = []
    for table in ("audit_event", "alert", "command", "heartbeat", "reconciliation", "account", "trade_order", "execution_metric"):
        dump += [str(r) for r in env.sql(f"select * from {table}")]  # noqa: S608 – feste Tabellennamen
    blob = "\n".join(dump) + caplog.text
    assert FAKE_KEY not in blob and FAKE_SECRET not in blob
    assert key_fingerprint(FAKE_KEY) in blob  # nur die gekürzte Kennung erscheint


# ───────────────────────────── T25 / T26 ─────────────────────────────


def test_t25_recovery_order_reconcile_then_protect_then_entries(ready: Env) -> None:
    env = ready
    sid = env.signal()
    env.tick()
    order = env.entry_order(sid)
    half = (D(order["qty"]) / 2).quantize(Q, ROUND_DOWN)
    env.fake.fill(order["id"], half, "100")
    env.tick()
    [stop] = env.stops_at_fake()
    # Ausfall: 10 Minuten keine Durchläufe; inzwischen wird der Stop extern storniert, der Rest gefüllt, ein Signal verpasst
    env.fake.cancel_order(stop.cl_ord_id or "")
    env.fake.fill(order["id"], D(order["qty"]) - half, "100")
    env.now += timedelta(minutes=10)
    missed = env.signal(BBB, age_s=300)
    mark = env.sql("select max(id) as m from audit_event")[0]["m"]
    env.tick()
    events = env.sql("select kind, object from audit_event where id > %s order by id", (mark,))
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "live.state"  # → RECOVERY
    first_fill = kinds.index("live.fill")
    first_stop = next(i for i, e in enumerate(events) if e["kind"] == "live.order.submit")
    assert first_fill < first_stop  # Abgleich (verpasster Fill) vor Schutz
    [new] = env.stops_at_fake()
    assert new.vol == D(order["qty"])
    out = env.outcome(missed)
    assert out is not None and out["status"] == "BLOCKED"  # verpasste Signale werden nicht nachgeholt
    assert env.state() == "ACTIVE"
    fresh = env.signal(BBB)
    env.tick()
    assert env.entry_order(fresh)["state"] == "ACCEPTED"


def test_t26_database_failure_after_send_is_resolved_by_client_order_id(ready: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env = ready
    sid = env.signal()
    real_save = LiveRepo.save_order

    def failing_save(self: LiveRepo, lo: Any, now: datetime) -> None:
        if lo.order.state.value in ("ACCEPTED", "UNKNOWN"):
            raise psycopg.OperationalError("Datenbank nicht erreichbar")
        real_save(self, lo, now)

    monkeypatch.setattr(LiveRepo, "save_order", failing_save)
    with pytest.raises(psycopg.OperationalError):
        env.tick()
    monkeypatch.setattr(LiveRepo, "save_order", real_save)
    assert env.entry_order(sid)["state"] == "SUBMITTED"
    other = env.signal(BBB)
    env.tick()  # Wiederherstellung: Status per cl_ord_id, keine neuen Einstiege
    assert env.entry_order(sid)["state"] == "ACCEPTED"
    assert env.outcome(other)["status"] == "BLOCKED"  # type: ignore[index]
    cl = env.entry_order(sid)["id"]
    assert sum(1 for o in env.fake.orders.values() if o.cl_ord_id == cl) == 1


def test_paper_tick_leaves_live_commands_pending(ready: Env) -> None:
    env = ready
    cmd = env.command("LIVE_PAUSE")
    paper_commands.process_pending(env.store, env.now, PaperRepo(env.conn))
    assert env.result(cmd)["status"] == "PENDING"
    env.tick()
    assert env.result(cmd)["status"] == "DONE" and env.state() == "ENTRIES_PAUSED"
