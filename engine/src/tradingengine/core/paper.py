"""Paper-Handel: Fortschreibung eines virtuellen Kontos über abgeschlossene Kerzen (docs/08, E2).

Reine Logik über einem `PaperState`, der aus der Datenbank geladen und danach gespeichert wird.
Jeder Schritt ist deterministisch und genau einmal anwendbar (Zeiger `sim_through`):

- `step_candle`: Ausführung der arbeitenden Orders innerhalb einer 4h-Kerze, danach Ablauf von
  Einstiegsorders und Betreuung offener Trades (Trailing, Zeitlimit, Regime) am Kerzenschluss.
- `submit_entries`: neue BUY-Signale → Positionsgrösse → Risikoprüfung → Reservierung → Einstiegsorder.
- `pause_entries`, `close_all`: Bedienhandlungen.

Kein Zugriff auf Handelskonten; alle Fills sind simuliert und so gekennzeichnet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .account import Account, AccountError, Position
from .candles import Candle
from .costs import BPS, CostModel, plan_costs
from .execution import Fill, Mode, Order, OrderRole, OrderState, OrderType, Side, apply_fill, confirm_cancel, request_cancel, transition
from .exit_plan import ExitPlan
from .exits import ManagedTrade, on_close
from .regime import Regime
from .risk import EntryRequest, RiskContext, RiskPolicy, check_entry
from .signals import Action, Decision
from .simulator import SimConfig, simulate
from .sizing import InstrumentSpec, size_position
from .strategies import StrategyDef


@dataclass(slots=True)
class TradeRec:
    id: str
    instrument_id: str
    strategy_version_id: str
    signal_id: str | None
    timeframe: str
    opened_at: datetime
    qty: Decimal
    entry_value: Decimal
    entry_fees: Decimal
    planned_stop: Decimal
    planned_risk: Decimal
    plan: ExitPlan
    stop: Decimal
    highest_close: Decimal
    bars_held: int
    managed_through: datetime
    status: str = "OPEN"
    closed_at: datetime | None = None
    exit_value: Decimal | None = None
    exit_fees: Decimal | None = None
    net: Decimal | None = None
    exit_reason: str | None = None


@dataclass(slots=True)
class PaperOrder:
    order: Order
    strategy_version_id: str
    signal_id: str | None
    timeframe: str
    trade_id: str
    plan: ExitPlan | None = None  # nur Einstiegsorders
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SignalIn:
    id: str
    instrument_id: str
    timeframe: str
    strategy_version_id: str
    candle_close: datetime
    regime: Regime
    score: int | None
    entry: Decimal
    stop: Decimal
    target: Decimal | None
    max_hold_bars: int | None
    ref_level: Decimal | None
    valid_until: datetime


@dataclass(frozen=True, slots=True)
class Outcome:
    signal_id: str
    status: str  # ORDERED | BLOCKED
    reasons: list[str]
    values: dict[str, str]


@dataclass(frozen=True, slots=True)
class Snapshot:
    ts: datetime
    equity: Decimal
    cash: Decimal
    invested: Decimal


@dataclass(frozen=True, slots=True)
class Management:
    """Abgeschlossene Kerze der Signal-Zeitebene eines Trades mit ATR und Regime zu diesem Zeitpunkt."""

    candle: Candle
    atr: float | None
    regime: Regime


@dataclass(frozen=True, slots=True)
class RiskInputs:
    day_start_equity: Decimal | None
    week_start_equity: Decimal | None
    peak_equity: Decimal | None
    entries_today: int
    losses_by_strategy: dict[str, int]
    entries_allowed: bool
    fresh: dict[str, bool]  # Datenstatus je Instrument


@dataclass(slots=True)
class PaperState:
    account_id: str
    episode_id: int
    account: Account
    sim_through: datetime
    trades: dict[str, TradeRec] = field(default_factory=dict)  # offene Trades je Instrument
    orders: dict[str, PaperOrder] = field(default_factory=dict)  # arbeitende Orders je Order-ID
    # Änderungen dieses Durchlaufs (werden gespeichert)
    dirty_orders: dict[str, PaperOrder] = field(default_factory=dict)
    dirty_trades: dict[str, TradeRec] = field(default_factory=dict)
    new_fills: list[Fill] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)

    def restore_positions(self) -> None:
        """Positionen des Kontobuchs aus den offenen Trades herstellen (nach dem Laden)."""
        for t in self.trades.values():
            self.account.positions[t.instrument_id] = Position(t.instrument_id, t.qty, t.entry_value, t.planned_risk, t.strategy_version_id)

    def _orders_of(self, instrument_id: str) -> list[PaperOrder]:
        return [o for o in self.orders.values() if o.order.instrument_id == instrument_id]

    def _touch(self, po: PaperOrder) -> None:
        self.dirty_orders[po.order.id] = po
        if not po.order.is_working:
            self.orders.pop(po.order.id, None)


def _new_order(order_id: str, state: PaperState, instrument_id: str, side: Side, type_: OrderType, role: OrderRole, qty: Decimal, at: datetime,
               limit_price: Decimal | None = None, stop_price: Decimal | None = None, valid_until: datetime | None = None) -> Order:
    order = Order(
        id=order_id, intent_key=order_id, mode=Mode.PAPER, account_id=state.account_id, instrument_id=instrument_id,
        side=side, type=type_, role=role, qty=qty, created_at=at, limit_price=limit_price, stop_price=stop_price, valid_until=valid_until,
    )
    for s in (OrderState.CHECKED, OrderState.SUBMITTED, OrderState.ACCEPTED):
        transition(order, s, at)
    return order


def _cancel(state: PaperState, po: PaperOrder, at: datetime, reason: str) -> None:
    if po.order.is_terminal:
        return
    request_cancel(po.order, at)
    confirm_cancel(po.order, at)  # der Simulator bestätigt sofort; beim echten Anbieter sind das zwei Schritte
    po.reason = reason
    if po.order.role is OrderRole.ENTRY:
        state.account.release(po.order.intent_key)
    state._touch(po)


def _close_trade(state: PaperState, trade: TradeRec, fill: Fill, reason: str) -> None:
    state.account.sell_fill(trade.instrument_id, fill.qty, fill.price, fill.fee)
    trade.exit_value = fill.qty * fill.price
    trade.exit_fees = fill.fee
    trade.net = trade.exit_value - trade.entry_value - trade.entry_fees - fill.fee
    trade.status, trade.closed_at, trade.exit_reason = "CLOSED", fill.time, reason
    state.dirty_trades[trade.id] = trade
    del state.trades[trade.instrument_id]
    for other in state._orders_of(trade.instrument_id):
        if other.trade_id == trade.id and other.order.is_working:
            _cancel(state, other, fill.time, "Trade geschlossen")


def _sell(state: PaperState, po: PaperOrder, trade: TradeRec, fill: Fill, reason: str) -> None:
    apply_fill(po.order, fill)
    state.new_fills.append(fill)
    state._touch(po)
    _close_trade(state, trade, fill, reason)


def _protect(state: PaperState, trade: TradeRec, at: datetime) -> None:
    """Schutz- und Zielorder passen immer zur tatsächlich gehaltenen Menge."""
    stop_id, target_id = f"{trade.id}-stop", f"{trade.id}-target"
    if stop_id in state.orders:
        state.orders[stop_id].order.qty = trade.qty
        state.orders[stop_id].order.stop_price = trade.stop
    else:
        order = _new_order(stop_id, state, trade.instrument_id, Side.SELL, OrderType.STOP, OrderRole.PROTECT, trade.qty, at, stop_price=trade.stop)
        state.orders[stop_id] = PaperOrder(order, trade.strategy_version_id, trade.signal_id, trade.timeframe, trade.id)
    state.dirty_orders[stop_id] = state.orders[stop_id]
    if trade.plan.target is not None:
        if target_id in state.orders:
            state.orders[target_id].order.qty = trade.qty
        else:
            order = _new_order(target_id, state, trade.instrument_id, Side.SELL, OrderType.LIMIT, OrderRole.EXIT, trade.qty, at, limit_price=trade.plan.target)
            state.orders[target_id] = PaperOrder(order, trade.strategy_version_id, trade.signal_id, trade.timeframe, trade.id)
        state.dirty_orders[target_id] = state.orders[target_id]


def step_candle(
    state: PaperState,
    close_time: datetime,
    candles: dict[str, Candle],
    management: dict[str, Management],
    last_prices: dict[str, Decimal],
    model: CostModel,
    sim: SimConfig,
    ticks: dict[str, Decimal | None],
    currency: str,
) -> None:
    """Schreibt das Konto um eine 4h-Kerze fort. `candles`: Kerze je Instrument, die bei `close_time` schloss."""
    if close_time <= state.sim_through:
        return  # bereits verarbeitet (Wiederholung, Neustart)

    for instrument_id, candle in candles.items():
        by_role = {(o.order.role, o.order.type): o for o in state._orders_of(instrument_id) if o.order.is_working}
        trade = state.trades.get(instrument_id)

        # A) Ausführung innerhalb der Kerze: Market-Exit → Stop → Ziel → Einstieg
        for key, reason in (((OrderRole.EXIT, OrderType.MARKET), None), ((OrderRole.PROTECT, OrderType.STOP), "Stop"), ((OrderRole.EXIT, OrderType.LIMIT), "Ziel")):
            po = by_role.get(key)
            trade = state.trades.get(instrument_id)
            if po is None or trade is None or not po.order.is_working:
                continue
            fill = simulate(po.order, candle, model, sim, currency)
            if fill is not None:
                _sell(state, po, trade, fill, reason or po.reason or "Ausstieg")

        entry = by_role.get((OrderRole.ENTRY, OrderType.LIMIT))
        if entry is not None and entry.order.is_working:
            fill = simulate(entry.order, candle, model, sim, currency)
            if fill is not None:
                assert entry.plan is not None
                res = state.account.reservations[entry.order.intent_key]
                risk_part = res.risk * (fill.qty / res.qty)
                apply_fill(entry.order, fill)
                state.new_fills.append(fill)
                state.account.buy_fill(entry.order.intent_key, fill.qty, fill.price, fill.fee, entry.strategy_version_id)
                state._touch(entry)
                trade = state.trades.get(instrument_id)
                if trade is None:
                    trade = TradeRec(
                        id=entry.trade_id, instrument_id=instrument_id, strategy_version_id=entry.strategy_version_id, signal_id=entry.signal_id,
                        timeframe=entry.timeframe, opened_at=fill.time, qty=Decimal(0), entry_value=Decimal(0), entry_fees=Decimal(0),
                        planned_stop=entry.plan.stop, planned_risk=Decimal(0), plan=entry.plan, stop=entry.plan.stop,
                        highest_close=fill.price, bars_held=0, managed_through=close_time,
                    )
                    state.trades[instrument_id] = trade
                trade.qty += fill.qty
                trade.entry_value += fill.qty * fill.price
                trade.entry_fees += fill.fee
                trade.planned_risk += risk_part
                state.dirty_trades[trade.id] = trade
                _protect(state, trade, close_time)
                # Reihenfolge innerhalb der Einstiegskerze ist unbekannt: fällt sie bis zum Stop, gilt er als ausgelöst.
                if candle.low <= trade.stop:
                    stop_po = state.orders[f"{trade.id}-stop"]
                    price = trade.stop * (1 - model.slippage_bps / BPS)
                    stop_fill = Fill(f"{stop_po.order.id}:1", stop_po.order.id, trade.qty, price, model.fee(trade.qty * price, True), currency, close_time)
                    if entry.order.is_working:
                        _cancel(state, entry, close_time, "Stop in der Einstiegskerze")
                    _sell(state, stop_po, trade, stop_fill, "Stop (in der Einstiegskerze)")

    # B) Am Kerzenschluss: abgelaufene Einstiegsorders
    for po in list(state.orders.values()):
        if po.order.role is OrderRole.ENTRY and po.order.is_working and po.order.valid_until is not None and po.order.valid_until <= close_time:
            _cancel(state, po, close_time, "Einstiegsorder abgelaufen")

    # Betreuung offener Trades auf ihrer Signal-Zeitebene
    for instrument_id, trade in list(state.trades.items()):
        m = management.get(instrument_id)
        if m is None or m.candle.close_time <= trade.managed_through or m.candle.timeframe != trade.timeframe:
            continue
        managed = ManagedTrade(trade.plan, trade.stop, trade.highest_close, trade.bars_held)
        reason = on_close(managed, m.candle, m.atr, m.regime, ticks.get(instrument_id))
        trade.stop, trade.highest_close, trade.bars_held = managed.stop, managed.highest_close, managed.bars_held
        trade.managed_through = m.candle.close_time
        state.dirty_trades[trade.id] = trade
        exit_id = f"{trade.id}-exit"
        if reason is not None and exit_id not in state.orders:
            _market_exit(state, trade, close_time, reason)
        elif reason is None:
            _protect(state, trade, close_time)

    prices = dict(last_prices)
    prices.update({i: c.close for i, c in candles.items()})
    state.snapshots.append(Snapshot(close_time, state.account.equity(prices), state.account.cash, state.account.invested))
    state.sim_through = close_time


def _market_exit(state: PaperState, trade: TradeRec, at: datetime, reason: str) -> None:
    """Ersetzt Schutz- und Zielorder durch einen Market-Exit (Summe offener Verkäufe ≤ Bestand)."""
    for po in state._orders_of(trade.instrument_id):
        if po.trade_id == trade.id and po.order.side is Side.SELL and po.order.is_working:
            _cancel(state, po, at, "Durch Market-Exit ersetzt")
    exit_id = f"{trade.id}-exit"
    order = _new_order(exit_id, state, trade.instrument_id, Side.SELL, OrderType.MARKET, OrderRole.EXIT, trade.qty, at)
    po = PaperOrder(order, trade.strategy_version_id, trade.signal_id, trade.timeframe, trade.id, reason=reason)
    state.orders[exit_id] = po
    state.dirty_orders[exit_id] = po


def submit_entries(
    state: PaperState,
    signals: list[SignalIn],
    now: datetime,
    prices: dict[str, Decimal],
    risk: RiskInputs,
    policy: RiskPolicy,
    model: CostModel,
    specs: dict[str, InstrumentSpec],
    strategies: list[StrategyDef],
    min_net_reward_risk: Decimal = Decimal("1.0"),
) -> None:
    """Verarbeitet neue BUY-Signale in dokumentierter Reihenfolge: Strategiepriorität, dann Setup-Score."""
    by_id = {s.version.id: (n, s) for n, s in enumerate(strategies)}
    entries_today = risk.entries_today
    ordered = sorted((s for s in signals if s.strategy_version_id in by_id), key=lambda s: (by_id[s.strategy_version_id][0], -(s.score or 0), s.instrument_id))

    for sig in ordered:
        strategy = by_id[sig.strategy_version_id][1]

        def blocked(reasons: list[str], values: dict[str, str] | None = None, sig: SignalIn = sig) -> None:
            state.outcomes.append(Outcome(sig.id, "BLOCKED", reasons, values or {}))

        if sig.valid_until <= now:
            blocked(["Signal abgelaufen"])
            continue
        spec = specs.get(sig.instrument_id)
        if spec is None:
            blocked(["Instrument-Spezifikation (Tick, Mindestmenge) unbekannt"])
            continue
        try:
            equity: Decimal | None = state.account.equity(prices)
        except AccountError:
            equity = None
        if equity is None:
            blocked(["Eigenkapital unbekannt (Preis fehlt)"])
            continue

        decision = Decision(sig.candle_close, Action.BUY, sig.regime, [], [], sig.score, sig.entry, sig.stop, sig.target, sig.max_hold_bars, sig.ref_level)
        plan = strategy.exit_plan(decision)
        costs = plan_costs(model, sig.entry, sig.stop, sig.target)
        if sig.target is not None and (costs.net_reward_risk is None or costs.net_reward_risk < min_net_reward_risk):
            shown = "–" if costs.net_reward_risk is None else f"{costs.net_reward_risk:.2f}"
            blocked([f"Netto-Chance-Risiko nach Kosten {shown} unter {min_net_reward_risk}"])
            continue

        fee_rate = model.maker_bps / BPS
        free = state.account.free_cash - equity * policy.cash_reserve
        max_notional = min(equity * policy.max_instrument_share, free / (1 + fee_rate))
        sizing = size_position(equity * policy.risk_per_trade * Decimal(str(strategy.risk_factor)), max_notional, sig.entry, sig.stop, model, spec)
        if not sizing.ok:
            blocked([sizing.reason or "Positionsgrösse nicht bestimmbar"])
            continue

        trade_id = f"p{state.episode_id}-{sig.id}"
        order_id = f"{trade_id}-entry"
        request = EntryRequest(order_id, sig.instrument_id, sig.strategy_version_id, sizing.qty, sig.entry, sizing.planned_risk, sizing.notional * (1 + fee_rate))
        ctx = RiskContext(
            equity=equity, day_start_equity=risk.day_start_equity, week_start_equity=risk.week_start_equity, peak_equity=risk.peak_equity,
            entries_today=entries_today, consecutive_losses=risk.losses_by_strategy.get(sig.strategy_version_id, 0),
            entries_allowed=risk.entries_allowed, data_fresh=risk.fresh.get(sig.instrument_id, False),
        )
        verdict = check_entry(request, state.account, ctx, policy)
        values = {**verdict.values, "menge": f"{sizing.qty}", "geplantes_risiko": f"{sizing.planned_risk:.2f}", "kostenmodell": model.version}
        if not verdict.allowed:
            blocked(verdict.reasons, values)
            continue

        state.account.reserve(order_id, sig.instrument_id, sizing.qty, request.cash_needed, sizing.planned_risk)
        order = _new_order(order_id, state, sig.instrument_id, Side.BUY, OrderType.LIMIT, OrderRole.ENTRY, sizing.qty, now,
                           limit_price=sig.entry, valid_until=sig.valid_until)
        po = PaperOrder(order, sig.strategy_version_id, sig.id, sig.timeframe, trade_id, plan=plan)
        state.orders[order_id] = po
        state.dirty_orders[order_id] = po
        state.outcomes.append(Outcome(sig.id, "ORDERED", [], values))
        entries_today += 1


def pause_entries(state: PaperState, at: datetime) -> int:
    """«Einstiege pausieren»: noch offene Einstiegsorders stornieren; Positionen und Schutz bleiben."""
    count = 0
    for po in list(state.orders.values()):
        if po.order.role is OrderRole.ENTRY and po.order.is_working:
            _cancel(state, po, at, "Einstiege pausiert")
            count += 1
    return count


def close_all(state: PaperState, at: datetime, reason: str = "Manuell geschlossen") -> int:
    """«Positionen jetzt schliessen»: Market-Exit für jede Position; ausgeführt wird in der nächsten Kerze."""
    pause_entries(state, at)
    count = 0
    for trade in list(state.trades.values()):
        if f"{trade.id}-exit" not in state.orders:
            _market_exit(state, trade, at, reason)
            count += 1
    return count
