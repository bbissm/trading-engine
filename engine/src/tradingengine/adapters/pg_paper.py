"""Persistenz des Paper-Handels (Tabellen aus Schema v2). Laden → reine Logik in core/paper.py → Speichern.

Gespeichert wird in einer Transaktion: entweder ist ein Simulationsschritt ganz verbucht oder gar nicht.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ..core.account import Account, Reservation
from ..core.candles import Candle
from ..core.execution import Fill, Mode, Order, OrderRole, OrderState, OrderType, Side
from ..core.exit_plan import ExitPlan
from ..core.paper import PaperOrder, PaperState, SignalIn, TradeRec
from ..core.regime import Regime
from ..core.risk import RiskPolicy
from ..core.sizing import InstrumentSpec

Conn = psycopg.Connection[dict[str, Any]]


def plan_to_json(plan: ExitPlan) -> dict[str, Any]:
    return {
        "stop": str(plan.stop),
        "max_hold_bars": plan.max_hold_bars,
        "trail_atr": plan.trail_atr,
        "target": None if plan.target is None else str(plan.target),
        "exit_unless_regime": None if plan.exit_unless_regime is None else sorted(r.value for r in plan.exit_unless_regime),
        "breakout_level": None if plan.breakout_level is None else str(plan.breakout_level),
        "failed_breakout_bars": plan.failed_breakout_bars,
    }


def plan_from_json(d: dict[str, Any]) -> ExitPlan:
    return ExitPlan(
        stop=Decimal(d["stop"]),
        max_hold_bars=int(d["max_hold_bars"]),
        trail_atr=d.get("trail_atr"),
        target=None if d.get("target") is None else Decimal(d["target"]),
        exit_unless_regime=None if d.get("exit_unless_regime") is None else frozenset(Regime(r) for r in d["exit_unless_regime"]),
        breakout_level=None if d.get("breakout_level") is None else Decimal(d["breakout_level"]),
        failed_breakout_bars=int(d.get("failed_breakout_bars", 0)),
    )


def policy_to_json(policy: RiskPolicy) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(policy).items()}


def policy_from_json(d: dict[str, Any]) -> RiskPolicy:
    defaults = asdict(RiskPolicy())
    return RiskPolicy(**{k: (Decimal(str(d[k])) if isinstance(defaults[k], Decimal) else d[k]) for k in defaults if k in d})  # type: ignore[arg-type]


class PaperRepo:
    def __init__(self, conn: Conn) -> None:
        self.c = conn

    # --- Konten ------------------------------------------------------------------------------------
    def accounts(self) -> list[dict[str, Any]]:
        """Paper-Konten mit Autopilot-Zustand und laufender Episode."""
        return self.c.execute(
            """
            select a.id, a.name, a.currency, ap.state, e.id as episode_id, e.number, e.start_cash, e.policy,
                   e.strategy_version_ids, e.cost_model, e.sim_through, e.signals_through
            from account a
            join autopilot ap on ap.account_id = a.id
            join episode e on e.account_id = a.id and e.ended_at is null
            where a.mode = 'PAPER' order by a.id
            """
        ).fetchall()

    def create_account(self, account_id: str, name: str, currency: str, start_cash: Decimal, policy: RiskPolicy,
                       strategy_ids: list[str], cost_model: str, sim_version: str, now: datetime, sim_through: datetime) -> None:
        with self.c.transaction():
            self.c.execute("insert into account (id, mode, name, currency, created_at) values (%s, 'PAPER', %s, %s, %s)", (account_id, name, currency, now))
            self._new_episode(account_id, 1, "NEW", start_cash, policy, strategy_ids, cost_model, sim_version, now, sim_through)
            self.c.execute("insert into autopilot (account_id, state, reason, updated_at) values (%s, 'READY', 'Konto angelegt', %s)", (account_id, now))

    def _new_episode(self, account_id: str, number: int, reason: str, start_cash: Decimal, policy: RiskPolicy, strategy_ids: list[str],
                     cost_model: str, sim_version: str, now: datetime, sim_through: datetime) -> None:
        self.c.execute(
            """
            insert into episode (account_id, number, reason, start_cash, cash, policy, strategy_version_ids, cost_model, sim_version,
                                 sim_through, signals_through, started_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (account_id, number, reason, start_cash, start_cash, Jsonb(policy_to_json(policy)), Jsonb(strategy_ids), cost_model, sim_version,
             sim_through, now, now),
        )

    def reset_account(self, account_id: str, start_cash: Decimal, policy: RiskPolicy, strategy_ids: list[str], cost_model: str,
                      sim_version: str, now: datetime, sim_through: datetime) -> int:
        """Beendet die laufende Episode und legt eine neue an. Alte Episoden bleiben unverändert."""
        with self.c.transaction():
            row = self.c.execute("select id, number from episode where account_id = %s and ended_at is null for update", (account_id,)).fetchone()
            assert row is not None
            self.c.execute("update episode set ended_at = %s where id = %s", (now, row["id"]))
            self._new_episode(account_id, row["number"] + 1, "RESET", start_cash, policy, strategy_ids, cost_model, sim_version, now, sim_through)
            return int(row["number"]) + 1

    def set_autopilot(self, account_id: str, state: str, reason: str, now: datetime) -> None:
        self.c.execute("update autopilot set state = %s, reason = %s, updated_at = %s where account_id = %s", (state, reason, now, account_id))

    def mark_signals_seen(self, episode_id: int, through: datetime) -> None:
        self.c.execute("update episode set signals_through = %s where id = %s and signals_through < %s", (through, episode_id, through))

    def set_sim_through(self, episode_id: int, through: datetime) -> None:
        self.c.execute("update episode set sim_through = %s where id = %s", (through, episode_id))

    # --- Zustand laden/speichern ---------------------------------------------------------------------
    def load_state(self, account_id: str) -> PaperState:
        ep = self.c.execute("select * from episode where account_id = %s and ended_at is null", (account_id,)).fetchone()
        acc = self.c.execute("select * from account where id = %s", (account_id,)).fetchone()
        assert ep is not None and acc is not None
        account = Account(account_id, acc["currency"], ep["cash"], fees_paid=ep["fees_paid"], realized=ep["realized"])
        for r in self.c.execute("select * from reservation where episode_id = %s", (ep["id"],)).fetchall():
            account.reservations[r["intent_key"]] = Reservation(r["intent_key"], r["instrument_id"], r["qty"], r["cash"], r["risk"])
        state = PaperState(account_id, int(ep["id"]), account, ep["sim_through"])

        for t in self.c.execute("select * from trade where episode_id = %s and status = 'OPEN'", (ep["id"],)).fetchall():
            state.trades[t["instrument_id"]] = TradeRec(
                id=t["id"], instrument_id=t["instrument_id"], strategy_version_id=t["strategy_version_id"],
                signal_id=None if t["signal_id"] is None else str(t["signal_id"]), timeframe=t["timeframe"], opened_at=t["opened_at"],
                qty=t["qty"], entry_value=t["entry_value"], entry_fees=t["entry_fees"], planned_stop=t["planned_stop"],
                planned_risk=t["planned_risk"], plan=plan_from_json(t["exit_plan"]), stop=t["current_stop"],
                highest_close=t["highest_close"], bars_held=t["bars_held"], managed_through=t["managed_through"],
            )
        working = [s.value for s in OrderState if s not in (OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED)]
        for o in self.c.execute("select * from trade_order where episode_id = %s and state = any(%s)", (ep["id"], working)).fetchall():
            order = Order(
                id=o["id"], intent_key=o["intent_key"], mode=Mode(o["mode"]), account_id=o["account_id"], instrument_id=o["instrument_id"],
                side=Side(o["side"]), type=OrderType(o["type"]), role=OrderRole(o["role"]), qty=o["qty"], created_at=o["created_at"],
                limit_price=o["limit_price"], stop_price=o["stop_price"], valid_until=o["valid_until"], state=OrderState(o["state"]),
            )
            for f in self.c.execute("select * from fill where order_id = %s order by time, id", (o["id"],)).fetchall():
                order.fills.append(Fill(f["id"], f["order_id"], f["qty"], f["price"], f["fee"], f["fee_currency"], f["time"], f["simulated"]))
            state.orders[order.id] = PaperOrder(
                order, o["strategy_version_id"], None if o["signal_id"] is None else str(o["signal_id"]), o["timeframe"], o["trade_id"],
                plan=None if o["exit_plan"] is None else plan_from_json(o["exit_plan"]), reason=o["reason"],
            )
        state.restore_positions()
        return state

    def save_state(self, state: PaperState, now: datetime) -> None:
        with self.c.transaction():
            for po in state.dirty_orders.values():
                o = po.order
                self.c.execute(
                    """
                    insert into trade_order (id, intent_key, mode, account_id, episode_id, instrument_id, strategy_version_id, signal_id, trade_id,
                                             timeframe, side, type, role, qty, limit_price, stop_price, state, exit_plan, reason, created_at,
                                             valid_until, updated_at)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (id) do update set qty = excluded.qty, stop_price = excluded.stop_price, state = excluded.state,
                        reason = excluded.reason, updated_at = excluded.updated_at
                    """,
                    (o.id, o.intent_key, o.mode.value, o.account_id, state.episode_id, o.instrument_id, po.strategy_version_id, po.signal_id,
                     po.trade_id, po.timeframe, o.side.value, o.type.value, o.role.value, o.qty, o.limit_price, o.stop_price, o.state.value,
                     None if po.plan is None else Jsonb(plan_to_json(po.plan)), po.reason, o.created_at, o.valid_until, now),
                )
            for f in state.new_fills:
                self.c.execute(
                    "insert into fill (id, order_id, qty, price, fee, fee_currency, time, simulated) values (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "on conflict (id) do nothing",
                    (f.id, f.order_id, f.qty, f.price, f.fee, f.fee_currency, f.time, f.simulated),
                )
            for t in state.dirty_trades.values():
                self.c.execute(
                    """
                    insert into trade (id, account_id, episode_id, instrument_id, strategy_version_id, signal_id, timeframe, status, opened_at,
                                       closed_at, qty, entry_value, entry_fees, exit_value, exit_fees, net, planned_stop, planned_risk,
                                       current_stop, highest_close, bars_held, exit_plan, exit_reason, managed_through)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (id) do update set status = excluded.status, closed_at = excluded.closed_at, qty = excluded.qty,
                        entry_value = excluded.entry_value, entry_fees = excluded.entry_fees, exit_value = excluded.exit_value,
                        exit_fees = excluded.exit_fees, net = excluded.net, planned_risk = excluded.planned_risk,
                        current_stop = excluded.current_stop, highest_close = excluded.highest_close, bars_held = excluded.bars_held,
                        exit_reason = excluded.exit_reason, managed_through = excluded.managed_through
                    """,
                    (t.id, state.account_id, state.episode_id, t.instrument_id, t.strategy_version_id, t.signal_id, t.timeframe, t.status,
                     t.opened_at, t.closed_at, t.qty, t.entry_value, t.entry_fees, t.exit_value, t.exit_fees, t.net, t.planned_stop,
                     t.planned_risk, t.stop, t.highest_close, t.bars_held, Jsonb(plan_to_json(t.plan)), t.exit_reason, t.managed_through),
                )
            self.c.execute("delete from reservation where episode_id = %s", (state.episode_id,))
            for r in state.account.reservations.values():
                self.c.execute(
                    "insert into reservation (intent_key, account_id, episode_id, instrument_id, qty, cash, risk) values (%s, %s, %s, %s, %s, %s, %s)",
                    (r.intent_key, state.account_id, state.episode_id, r.instrument_id, r.qty, r.cash, r.risk),
                )
            for s in state.snapshots:
                self.c.execute(
                    "insert into equity_snapshot (account_id, episode_id, ts, equity, cash, invested) values (%s, %s, %s, %s, %s, %s) "
                    "on conflict do nothing",
                    (state.account_id, state.episode_id, s.ts, s.equity, s.cash, s.invested),
                )
            for out in state.outcomes:
                self.c.execute(
                    "insert into signal_outcome (signal_id, account_id, episode_id, status, reasons, values, created_at) "
                    "values (%s, %s, %s, %s, %s, %s, %s) on conflict do nothing",
                    (out.signal_id, state.account_id, state.episode_id, out.status, Jsonb(out.reasons), Jsonb(out.values), now),
                )
            self.c.execute(
                "update episode set cash = %s, fees_paid = %s, realized = %s, sim_through = %s where id = %s",
                (state.account.cash, state.account.fees_paid, state.account.realized, state.sim_through, state.episode_id),
            )

    # --- Marktdaten für die Simulation ---------------------------------------------------------------
    def new_closes(self, timeframe: str, after: datetime, until: datetime, limit: int = 60) -> list[datetime]:
        rows = self.c.execute(
            "select distinct close_time from candle where timeframe = %s and close_time > %s and close_time <= %s order by 1 limit %s",
            (timeframe, after, until, limit),
        ).fetchall()
        return [r["close_time"] for r in rows]

    def candles_at(self, timeframe: str, close_time: datetime, instrument_ids: list[str]) -> dict[str, Candle]:
        if not instrument_ids:
            return {}
        rows = self.c.execute(
            "select * from candle where timeframe = %s and close_time = %s and instrument_id = any(%s)", (timeframe, close_time, instrument_ids)
        ).fetchall()
        return {r["instrument_id"]: _candle(r) for r in rows}

    def candles_until(self, instrument_id: str, timeframe: str, until: datetime, limit: int) -> list[Candle]:
        rows = self.c.execute(
            "select * from (select * from candle where instrument_id = %s and timeframe = %s and close_time <= %s "
            "order by open_time desc limit %s) t order by open_time",
            (instrument_id, timeframe, until, limit),
        ).fetchall()
        return [_candle(r) for r in rows]

    def last_prices(self, timeframe: str, until: datetime) -> dict[str, Decimal]:
        rows = self.c.execute(
            "select distinct on (instrument_id) instrument_id, close from candle where timeframe = %s and close_time <= %s "
            "order by instrument_id, close_time desc",
            (timeframe, until),
        ).fetchall()
        return {r["instrument_id"]: r["close"] for r in rows}

    def specs(self) -> tuple[dict[str, InstrumentSpec], dict[str, Decimal | None], dict[str, str | None]]:
        """Je Instrument: Spezifikation (nur wenn vollständig bekannt), Tick-Grösse, Leitinstrument."""
        specs: dict[str, InstrumentSpec] = {}
        ticks: dict[str, Decimal | None] = {}
        leaders: dict[str, str | None] = {}
        for r in self.c.execute("select id, tick_size, min_qty, min_notional, leader_id from instrument where in_universe").fetchall():
            ticks[r["id"]] = r["tick_size"]
            leaders[r["id"]] = r["leader_id"]
            if r["tick_size"] is not None and r["min_qty"] is not None and r["min_notional"] is not None:
                specs[r["id"]] = InstrumentSpec(r["tick_size"], Decimal("0.00000001"), r["min_qty"], r["min_notional"])
        return specs, ticks, leaders

    # --- Signale und Risikoeingaben ------------------------------------------------------------------
    def new_buy_signals(self, after: datetime, strategy_ids: list[str]) -> list[tuple[SignalIn, datetime]]:
        rows = self.c.execute(
            """
            select * from signal where action = 'BUY' and created_at > %s and strategy_version_id = any(%s)
              and entry is not null and stop is not null and valid_until is not null order by created_at
            """,
            (after, strategy_ids),
        ).fetchall()
        return [
            (
                SignalIn(
                    id=str(r["id"]), instrument_id=r["instrument_id"], timeframe=r["timeframe"], strategy_version_id=r["strategy_version_id"],
                    candle_close=r["candle_close"], regime=Regime(r["regime"]), score=r["score"], entry=r["entry"], stop=r["stop"],
                    target=r["target"], max_hold_bars=r["max_hold_bars"], ref_level=r["ref_level"], valid_until=r["valid_until"],
                ),
                r["created_at"],
            )
            for r in rows
        ]

    def equity_at_or_before(self, episode_id: int, ts: datetime) -> Decimal | None:
        row = self.c.execute(
            "select equity from equity_snapshot where episode_id = %s and ts <= %s order by ts desc limit 1", (episode_id, ts)
        ).fetchone()
        return None if row is None else row["equity"]

    def peak_equity(self, episode_id: int) -> Decimal | None:
        row = self.c.execute("select max(equity) as m from equity_snapshot where episode_id = %s", (episode_id,)).fetchone()
        return None if row is None else row["m"]

    def entries_since(self, episode_id: int, since: datetime) -> int:
        row = self.c.execute(
            "select count(*) as n from trade_order where episode_id = %s and role = 'ENTRY' and created_at >= %s", (episode_id, since)
        ).fetchone()
        return 0 if row is None else int(row["n"])

    def recent_closed(self, episode_id: int, strategy_id: str, limit: int) -> list[tuple[Decimal, datetime]]:
        rows = self.c.execute(
            "select net, closed_at from trade where episode_id = %s and strategy_version_id = %s and status = 'CLOSED' "
            "order by closed_at desc limit %s",
            (episode_id, strategy_id, limit),
        ).fetchall()
        return [(r["net"], r["closed_at"]) for r in rows]

    def open_counts(self, episode_id: int) -> tuple[int, int]:
        """Offene Trades und arbeitende Orders der Episode."""
        t = self.c.execute("select count(*) as n from trade where episode_id = %s and status = 'OPEN'", (episode_id,)).fetchone()
        o = self.c.execute(
            "select count(*) as n from trade_order where episode_id = %s and state not in ('FILLED','CANCELED','REJECTED','EXPIRED')", (episode_id,)
        ).fetchone()
        assert t is not None and o is not None
        return int(t["n"]), int(o["n"])


def _candle(r: dict[str, Any]) -> Candle:
    return Candle(
        instrument_id=r["instrument_id"], timeframe=r["timeframe"], open_time=r["open_time"], close_time=r["close_time"], open=r["open"],
        high=r["high"], low=r["low"], close=r["close"], volume=r["volume"], trades=r["trades"], source=r["source"],
    )
