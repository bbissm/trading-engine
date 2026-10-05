"""Persistenz des Live-Orderwegs auf den bestehenden Tabellen (Schema v3, unverändert).

Konventionen (Schema-Lücken siehe live/README im Bericht):
- Live-Datensätze tragen `episode_id = 0` (Episoden gibt es nur im Paper-Handel; die Spalte ist Pflicht).
- `trade_order.id` = `cl_ord_id` (deterministisch aus dem Idempotenzschlüssel `intent_key`).
- Kraken-txid steht im Auditprotokoll (`live.order.ack`), nicht in einer Spalte.
- Fremdbestände und Kontostand stehen im Abgleich (`reconciliation.diffs`, Eintrag `kind = SNAPSHOT`).
- Buchhaltung je Trade, die das Schema nicht vorsieht (verkaufter Einstandswert, manuelle Verkäufe), liegt unter
  `exit_plan._live`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ..adapters.pg_paper import plan_from_json, plan_to_json
from ..core.execution import TERMINAL, Fill, Mode, Order, OrderRole, OrderState, OrderType, Side
from ..core.exit_plan import ExitPlan
from ..core.paper import TradeRec
from .locks import Mandate

Conn = psycopg.Connection[dict[str, Any]]
LIVE_EPISODE = 0
ACTOR = "engine:live"
LOCK_ID = 7_351_004  # Advisory-Lock: höchstens ein Live-Durchlauf gleichzeitig


@dataclass(slots=True)
class LiveOrder:
    order: Order
    strategy_version_id: str
    signal_id: str | None
    timeframe: str
    trade_id: str | None
    plan: ExitPlan | None = None
    reason: str | None = None


@dataclass(slots=True)
class LiveTrade:
    rec: TradeRec
    sold_cost: Decimal = Decimal(0)
    manual_qty: Decimal = Decimal(0)  # ausserhalb von TradingEngine verkaufte Menge (Erlös unbekannt)
    protect_failures: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InstrumentRow:
    id: str
    venue_symbol: str
    base_asset: str
    quote_currency: str
    tick: Decimal | None
    min_qty: Decimal | None
    min_notional: Decimal | None


class LiveRepo:
    def __init__(self, conn: Conn, now: datetime | None = None) -> None:
        self.c = conn
        # Zeit des Durchlaufs: Auditzeitpunkte (z. B. «Order gesendet») folgen der Uhr der Engine, nicht der DB-Uhr,
        # sonst hängt die Deadline-Prüfung verlorener Orders von der Abweichung beider Uhren ab.
        self.now = now

    # --- Sperre gegen parallele Durchläufe ------------------------------------------------------------
    def try_lock(self) -> bool:
        row = self.c.execute("select pg_try_advisory_lock(%s) as ok", (LOCK_ID,)).fetchone()
        return bool(row and row["ok"])

    def unlock(self) -> None:
        self.c.execute("select pg_advisory_unlock(%s)", (LOCK_ID,))

    # --- Konto, Autopilot, Mandat -----------------------------------------------------------------------
    def live_accounts(self) -> list[dict[str, Any]]:
        return self.c.execute(
            "select a.*, ap.state as ap_state, ap.reason as ap_reason from account a left join autopilot ap on ap.account_id = a.id "
            "where a.mode = 'LIVE' and a.provider = 'KRAKEN' order by a.created_at, a.id"
        ).fetchall()

    def set_autopilot(self, account_id: str, state: str, reason: str, now: datetime) -> None:
        row = self.c.execute("select state from autopilot where account_id = %s", (account_id,)).fetchone()
        before = None if row is None else row["state"]
        if before == state:
            self.c.execute("update autopilot set reason = %s, updated_at = %s where account_id = %s and reason is distinct from %s",
                           (reason, now, account_id, reason))
            return
        self.c.execute(
            "insert into autopilot (account_id, state, reason, updated_at) values (%s, %s, %s, %s) "
            "on conflict (account_id) do update set state = excluded.state, reason = excluded.reason, updated_at = excluded.updated_at",
            (account_id, state, reason, now),
        )
        self.audit("live.state", f"account:{account_id}", {"from": before, "to": state, "reason": reason})

    def autopilot_state(self, account_id: str) -> str:
        row = self.c.execute("select state from autopilot where account_id = %s", (account_id,)).fetchone()
        return "SETUP" if row is None else str(row["state"])

    def active_mandates(self, account_id: str) -> list[Mandate]:
        rows = self.c.execute("select * from mandate where account_id = %s and status = 'ACTIVE' order by activated_at desc nulls last, id desc",
                              (account_id,)).fetchall()
        return [_mandate(r) for r in rows]

    def set_mandate_status(self, mandate_id: int, status: str, reason: str, now: datetime) -> None:
        self.c.execute("update mandate set status = %s, ended_at = case when %s in ('ENDED','SUSPENDED') then %s else ended_at end, end_reason = %s "
                       "where id = %s", (status, status, now, reason, mandate_id))
        self.audit("live.mandate." + status.lower(), f"mandate:{mandate_id}", {"reason": reason})

    def mandate_accepted(self, mandate_id: int) -> bool:
        return self.c.execute("select 1 from audit_event where kind = 'live.mandate.accepted' and object = %s limit 1",
                              (f"mandate:{mandate_id}",)).fetchone() is not None

    # --- Orders, Fills, Trades, Reservierungen ----------------------------------------------------------
    def working_orders(self, account_id: str) -> dict[str, LiveOrder]:
        terminal = [s.value for s in TERMINAL]
        rows = self.c.execute("select * from trade_order where account_id = %s and mode = 'LIVE' and state <> all(%s) order by created_at, id",
                              (account_id, terminal)).fetchall()
        return {r["id"]: self._order(r) for r in rows}

    def order(self, order_id: str) -> LiveOrder | None:
        r = self.c.execute("select * from trade_order where id = %s", (order_id,)).fetchone()
        return None if r is None else self._order(r)

    def _order(self, r: dict[str, Any]) -> LiveOrder:
        o = Order(
            id=r["id"], intent_key=r["intent_key"], mode=Mode(r["mode"]), account_id=r["account_id"], instrument_id=r["instrument_id"],
            side=Side(r["side"]), type=OrderType(r["type"]), role=OrderRole(r["role"]), qty=r["qty"], created_at=r["created_at"],
            limit_price=r["limit_price"], stop_price=r["stop_price"], valid_until=r["valid_until"], state=OrderState(r["state"]),
        )
        for f in self.c.execute("select * from fill where order_id = %s order by time, id", (r["id"],)).fetchall():
            o.fills.append(Fill(f["id"], f["order_id"], f["qty"], f["price"], f["fee"], f["fee_currency"], f["time"], f["simulated"]))
        return LiveOrder(o, r["strategy_version_id"], None if r["signal_id"] is None else str(r["signal_id"]), r["timeframe"], r["trade_id"],
                         None if r["exit_plan"] is None else plan_from_json(r["exit_plan"]), r["reason"])

    def insert_intent(self, lo: LiveOrder, now: datetime) -> bool:
        """Absicht speichern. False: dieselbe Absicht existiert schon (zweiter Prozess, Wiederholung)."""
        o = lo.order
        row = self.c.execute(
            """
            insert into trade_order (id, intent_key, mode, account_id, episode_id, instrument_id, strategy_version_id, signal_id, trade_id,
                                     timeframe, side, type, role, qty, limit_price, stop_price, state, exit_plan, reason, created_at,
                                     valid_until, updated_at)
            values (%s, %s, 'LIVE', %s, 0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing returning id
            """,
            (o.id, o.intent_key, o.account_id, o.instrument_id, lo.strategy_version_id, lo.signal_id, lo.trade_id, lo.timeframe, o.side.value,
             o.type.value, o.role.value, o.qty, o.limit_price, o.stop_price, o.state.value, None if lo.plan is None else Jsonb(plan_to_json(lo.plan)),
             lo.reason, o.created_at, o.valid_until, now),
        ).fetchone()
        return row is not None

    def save_order(self, lo: LiveOrder, now: datetime) -> None:
        o = lo.order
        self.c.execute(
            "update trade_order set qty = %s, stop_price = %s, limit_price = %s, state = %s, reason = %s, updated_at = %s where id = %s",
            (o.qty, o.stop_price, o.limit_price, o.state.value, lo.reason, now, o.id),
        )

    def insert_fill(self, f: Fill) -> bool:
        row = self.c.execute(
            "insert into fill (id, order_id, qty, price, fee, fee_currency, time, simulated) values (%s, %s, %s, %s, %s, %s, %s, false) "
            "on conflict (id) do nothing returning id",
            (f.id, f.order_id, f.qty, f.price, f.fee, f.fee_currency, f.time),
        ).fetchone()
        return row is not None

    def fill_exists(self, fill_id: str) -> bool:
        return self.c.execute("select 1 from fill where id = %s", (fill_id,)).fetchone() is not None

    def open_trades(self, account_id: str) -> dict[str, LiveTrade]:
        rows = self.c.execute("select * from trade where account_id = %s and status = 'OPEN' order by opened_at, id", (account_id,)).fetchall()
        out: dict[str, LiveTrade] = {}
        for t in rows:
            meta = dict((t["exit_plan"] or {}).get("_live") or {})
            rec = TradeRec(
                id=t["id"], instrument_id=t["instrument_id"], strategy_version_id=t["strategy_version_id"],
                signal_id=None if t["signal_id"] is None else str(t["signal_id"]), timeframe=t["timeframe"], opened_at=t["opened_at"],
                qty=t["qty"], entry_value=t["entry_value"], entry_fees=t["entry_fees"], planned_stop=t["planned_stop"], planned_risk=t["planned_risk"],
                plan=plan_from_json(t["exit_plan"]), stop=t["current_stop"], highest_close=t["highest_close"], bars_held=t["bars_held"],
                managed_through=t["managed_through"], status=t["status"], exit_value=t["exit_value"], exit_fees=t["exit_fees"],
                exit_reason=t["exit_reason"],
            )
            out[t["id"]] = LiveTrade(rec, Decimal(str(meta.get("sold_cost", "0"))), Decimal(str(meta.get("manual_qty", "0"))),
                                     int(meta.get("protect_failures", 0)), meta)
        return out

    def save_trade(self, account_id: str, lt: LiveTrade) -> None:
        t = lt.rec
        plan = plan_to_json(t.plan)
        plan["_live"] = {**lt.meta, "sold_cost": str(lt.sold_cost), "manual_qty": str(lt.manual_qty), "protect_failures": lt.protect_failures}
        self.c.execute(
            """
            insert into trade (id, account_id, episode_id, instrument_id, strategy_version_id, signal_id, timeframe, status, opened_at,
                               closed_at, qty, entry_value, entry_fees, exit_value, exit_fees, net, planned_stop, planned_risk,
                               current_stop, highest_close, bars_held, exit_plan, exit_reason, managed_through)
            values (%s, %s, 0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (id) do update set status = excluded.status, closed_at = excluded.closed_at, qty = excluded.qty,
                entry_value = excluded.entry_value, entry_fees = excluded.entry_fees, exit_value = excluded.exit_value,
                exit_fees = excluded.exit_fees, net = excluded.net, planned_risk = excluded.planned_risk, current_stop = excluded.current_stop,
                highest_close = excluded.highest_close, bars_held = excluded.bars_held, exit_plan = excluded.exit_plan,
                exit_reason = excluded.exit_reason, managed_through = excluded.managed_through
            """,
            (t.id, account_id, t.instrument_id, t.strategy_version_id, t.signal_id, t.timeframe, t.status, t.opened_at, t.closed_at, t.qty,
             t.entry_value, t.entry_fees, t.exit_value, t.exit_fees, t.net, t.planned_stop, t.planned_risk, t.stop, t.highest_close, t.bars_held,
             Jsonb(plan), t.exit_reason, t.managed_through),
        )

    def reservations(self, account_id: str) -> dict[str, dict[str, Any]]:
        rows = self.c.execute("select * from reservation where account_id = %s and episode_id = 0", (account_id,)).fetchall()
        return {r["intent_key"]: r for r in rows}

    def reserve(self, intent_key: str, account_id: str, instrument_id: str, qty: Decimal, cash: Decimal, risk: Decimal) -> None:
        self.c.execute("insert into reservation (intent_key, account_id, episode_id, instrument_id, qty, cash, risk) values (%s, %s, 0, %s, %s, %s, %s)",
                       (intent_key, account_id, instrument_id, qty, cash, risk))

    def update_reservation(self, intent_key: str, qty: Decimal, cash: Decimal, risk: Decimal) -> None:
        if qty <= 0:
            self.release(intent_key)
        else:
            self.c.execute("update reservation set qty = %s, cash = %s, risk = %s where intent_key = %s", (qty, cash, risk, intent_key))

    def release(self, intent_key: str) -> None:
        self.c.execute("delete from reservation where intent_key = %s", (intent_key,))

    def lock_account(self, account_id: str) -> None:
        self.c.execute("select id from account where id = %s for update", (account_id,))

    def count_protect_orders(self, trade_id: str, role: str) -> int:
        row = self.c.execute("select count(*) as n from trade_order where trade_id = %s and role = %s", (trade_id, role)).fetchone()
        return 0 if row is None else int(row["n"])

    def latest_entry_fill_time(self, trade_id: str) -> datetime | None:
        row = self.c.execute("select max(f.time) as t from fill f join trade_order o on o.id = f.order_id where o.trade_id = %s and o.role = 'ENTRY'",
                             (trade_id,)).fetchone()
        return None if row is None else row["t"]

    # --- Abgleich -----------------------------------------------------------------------------------
    def insert_reconciliation(self, account_id: str, status: str, diffs: list[dict[str, Any]], now: datetime) -> None:
        self.c.execute("insert into reconciliation (account_id, at, status, diffs) values (%s, %s, %s, %s)", (account_id, now, status, Jsonb(diffs)))

    def last_reconciliation(self, account_id: str) -> dict[str, Any] | None:
        return self.c.execute("select * from reconciliation where account_id = %s order by at desc, id desc limit 1", (account_id,)).fetchone()

    def negative_answers(self, order_id: str) -> int:
        row = self.c.execute("select count(*) as n from audit_event where kind = 'live.unknown.negative' and object = %s", (f"order:{order_id}",)).fetchone()
        return 0 if row is None else int(row["n"])

    # --- Stammdaten und Marktdaten ------------------------------------------------------------------
    def instruments(self) -> dict[str, InstrumentRow]:
        rows = self.c.execute("select id, venue_symbol, base_asset, quote_currency, tick_size, min_qty, min_notional from instrument "
                              "where base_asset is not null").fetchall()
        return {r["id"]: InstrumentRow(r["id"], r["venue_symbol"], r["base_asset"], r["quote_currency"], r["tick_size"], r["min_qty"], r["min_notional"])
                for r in rows}

    def feed_ok(self, instrument_id: str, timeframe: str) -> bool:
        row = self.c.execute("select bool_or(status = 'OK') as ok from feed_status where instrument_id = %s and timeframe = %s",
                             (instrument_id, timeframe)).fetchone()
        return bool(row and row["ok"])

    def test_ack_at(self) -> datetime | None:
        row = self.c.execute("select max(last_test_ack_at) as t from channel_status").fetchone()
        return None if row is None else row["t"]

    def fx_date(self) -> str | None:
        row = self.c.execute("select max(date) as d from fx_rate where (base = 'USD' and quote = 'CHF') or (base = 'CHF' and quote = 'USD')").fetchone()
        return None if row is None else row["d"]

    def approved_live(self, strategy_version_id: str) -> bool:
        row = self.c.execute("select decision from approval where strategy_version_id = %s and decided_at is not null "
                             "order by decided_at desc, id desc limit 1", (strategy_version_id,)).fetchone()
        return row is not None and row["decision"] == "APPROVED_LIVE"

    def gates(self, strategy_version_id: str) -> dict[str, str]:
        rows = self.c.execute("select distinct on (gate) gate, result from gate_evaluation where strategy_version_id = %s "
                              "order by gate, evaluated_at desc, id desc", (strategy_version_id,)).fetchall()
        return {r["gate"]: r["result"] for r in rows}

    def candles_after(self, instrument_id: str, timeframe: str, after: datetime, until: datetime) -> list[dict[str, Any]]:
        return self.c.execute("select * from candle where instrument_id = %s and timeframe = %s and close_time > %s and close_time <= %s "
                              "order by close_time", (instrument_id, timeframe, after, until)).fetchall()

    # --- Signale, Freigaben, Ergebnisse ---------------------------------------------------------------
    def new_buy_signals(self, account_id: str, since: datetime, strategy_ids: list[str]) -> list[dict[str, Any]]:
        return self.c.execute(
            """
            select s.* from signal s
            where s.action = 'BUY' and s.created_at > %s and s.strategy_version_id = any(%s)
              and s.entry is not null and s.stop is not null and s.valid_until is not null
              and not exists (select 1 from signal_outcome so where so.signal_id = s.id and so.account_id = %s and so.episode_id = 0)
              and not exists (select 1 from order_approval oa where oa.signal_id = s.id and oa.account_id = %s)
            order by s.created_at
            """,
            (since, strategy_ids, account_id, account_id),
        ).fetchall()

    def signal(self, signal_id: str) -> dict[str, Any] | None:
        return self.c.execute("select * from signal where id = %s", (signal_id,)).fetchone()

    def outcome(self, signal_id: str, account_id: str, status: str, reasons: list[str], values: dict[str, str], now: datetime) -> None:
        self.c.execute("insert into signal_outcome (signal_id, account_id, episode_id, status, reasons, values, created_at) "
                       "values (%s, %s, 0, %s, %s, %s, %s) on conflict do nothing", (signal_id, account_id, status, Jsonb(reasons), Jsonb(values), now))

    def create_order_approval(self, account_id: str, signal_id: str, intent: dict[str, Any], expires_at: datetime, now: datetime) -> int:
        row = self.c.execute("insert into order_approval (account_id, signal_id, intent, created_at, expires_at, status) "
                             "values (%s, %s, %s, %s, %s, 'PENDING') returning id", (account_id, signal_id, Jsonb(intent), now, expires_at)).fetchone()
        assert row is not None
        return int(row["id"])

    def approvals(self, account_id: str, status: str) -> list[dict[str, Any]]:
        return self.c.execute("select * from order_approval where account_id = %s and status = %s order by created_at", (account_id, status)).fetchall()

    def set_approval(self, approval_id: int, status: str, now: datetime, by: str | None) -> None:
        self.c.execute("update order_approval set status = %s, decided_at = %s, decided_by = %s where id = %s", (status, now, by, approval_id))

    # --- Risikoeingaben ---------------------------------------------------------------------------------
    def entries_since(self, account_id: str, since: datetime) -> int:
        row = self.c.execute("select count(*) as n from trade_order where account_id = %s and mode = 'LIVE' and role = 'ENTRY' and created_at >= %s",
                             (account_id, since)).fetchone()
        return 0 if row is None else int(row["n"])

    def recent_closed(self, account_id: str, strategy_id: str, limit: int) -> list[tuple[Decimal | None, datetime]]:
        rows = self.c.execute("select net, closed_at from trade where account_id = %s and strategy_version_id = %s and status = 'CLOSED' "
                              "order by closed_at desc limit %s", (account_id, strategy_id, limit)).fetchall()
        return [(r["net"], r["closed_at"]) for r in rows]

    def snapshot(self, account_id: str, ts: datetime, equity: Decimal, cash: Decimal, invested: Decimal) -> None:
        self.c.execute("insert into equity_snapshot (account_id, episode_id, ts, equity, cash, invested) values (%s, 0, %s, %s, %s, %s) "
                       "on conflict do nothing", (account_id, ts, equity, cash, invested))

    def first_equity_since(self, account_id: str, since: datetime) -> Decimal | None:
        row = self.c.execute("select equity from equity_snapshot where account_id = %s and episode_id = 0 and ts >= %s order by ts limit 1",
                             (account_id, since)).fetchone()
        return None if row is None else row["equity"]

    def peak_equity(self, account_id: str) -> Decimal | None:
        row = self.c.execute("select max(equity) as m from equity_snapshot where account_id = %s and episode_id = 0", (account_id,)).fetchone()
        return None if row is None else row["m"]

    # --- Protokoll, Kennzahlen, Lebenszeichen ---------------------------------------------------------
    def audit(self, kind: str, obj: str | None, data: dict[str, Any] | None = None) -> None:
        self.c.execute("insert into audit_event (actor, kind, object, data, ts) values (%s, %s, %s, %s, coalesce(%s, now()))",
                       (ACTOR, kind, obj, None if data is None else Jsonb(data), self.now))

    def metric(self, account_id: str, order_id: str, now: datetime, signal_to_order_ms: int | None = None, order_to_ack_ms: int | None = None,
               fill_to_protect_ms: int | None = None, slippage_bps: Decimal | None = None) -> None:
        self.c.execute("insert into execution_metric (account_id, order_id, signal_to_order_ms, order_to_ack_ms, fill_to_protect_ms, slippage_bps, at) "
                       "values (%s, %s, %s, %s, %s, %s, %s)", (account_id, order_id, signal_to_order_ms, order_to_ack_ms, fill_to_protect_ms, slippage_bps, now))

    def heartbeat(self) -> dict[str, Any] | None:
        return self.c.execute("select * from heartbeat where service = 'live'").fetchone()

    def set_heartbeat(self, now: datetime, schema_version: int, detail: dict[str, Any]) -> None:
        self.c.execute("insert into heartbeat (service, last_seen, schema_version, detail) values ('live', %s, %s, %s) "
                       "on conflict (service) do update set last_seen = excluded.last_seen, schema_version = excluded.schema_version, detail = excluded.detail",
                       (now, schema_version, Jsonb(detail)))


def _mandate(r: dict[str, Any]) -> Mandate:
    return Mandate(
        id=int(r["id"]), account_id=r["account_id"], autonomy_level=int(r["autonomy_level"]),
        strategy_version_ids=tuple(r["strategy_version_ids"] or ()), instrument_ids=tuple(r["instrument_ids"] or ()),
        budget=Decimal(r["budget"]), policy=dict(r["policy"] or {}), status=r["status"], activated_at=r["activated_at"],
        activated_by=r["activated_by"], step_up_at=r["step_up_at"], valid_until=r["valid_until"],
    )
