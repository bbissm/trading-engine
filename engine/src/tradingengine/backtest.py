"""Ereignisgetriebener Backtest für eine Strategie auf einem Instrument (docs/08, E2-4).

Verwendet dieselben Bausteine wie später Paper und Live: Strategieentscheidung, Positionsgrösse,
Risikoprüfung, Kontobuch mit Reservierung, Order-Zustandsautomat, Simulator, Exit-Plan.

Ablauf je Kerze i:
  A) Ausführung innerhalb der Kerze: Orders, die *vor* Kerzenbeginn erteilt wurden.
     Reihenfolge: Market-Exit zur Eröffnung → Stop → Ziel → Einstieg (danach Stop derselben Kerze, konservativ).
  B) Am Kerzenschluss: unausgeführte Einstiegsorder verfällt, Position wird betreut (Trailing, Zeitlimit,
     Regime), oder eine neue Entscheidung der Strategie wird geprüft und ggf. als Order erteilt.

Ein Backtest-Ergebnis ist kein Qualitätsnachweis (docs/04): dafür braucht es Walk-forward, Sensitivitäten
und die Gates. Tageswechsel wird hier in UTC gerechnet (Vereinfachung gegenüber Europe/Zurich).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from .core import indicators as ind
from .core.account import Account
from .core.candles import Candle
from .core.costs import BPS, KRAKEN_SPOT_TIER1, CostModel, plan_costs
from .core.execution import Fill, Mode, Order, OrderRole, OrderState, OrderType, Side, apply_fill, confirm_cancel, request_cancel, transition
from .core.exit_plan import ExitPlan
from .core.exits import ManagedTrade, on_close, start
from .core.pnl import TradeResult, trade_result
from .core.regime import RegimePoint, regime_at
from .core.risk import EntryRequest, RiskContext, RiskPolicy, check_entry
from .core.signals import Action, Decision
from .core.simulator import SimConfig, simulate
from .core.sizing import InstrumentSpec, size_position
from .core.strategies import StrategyDef

DEFAULT_SPEC = InstrumentSpec(tick=Decimal("0.01"), qty_step=Decimal("0.00000001"), min_qty=Decimal("0.00001"), min_notional=Decimal("5"))


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    start_cash: Decimal = Decimal(10000)
    currency: str = "USD"
    policy: RiskPolicy = field(default_factory=RiskPolicy)
    cost: CostModel = KRAKEN_SPOT_TIER1
    sim: SimConfig = field(default_factory=SimConfig)
    spec: InstrumentSpec = DEFAULT_SPEC
    min_net_reward_risk: Decimal = Decimal("1.0")  # nur für Strategien mit festem Ziel
    warmup_bars: int = 0  # vor diesem Index keine Einstiege


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    entry_time: datetime
    exit_time: datetime
    result: TradeResult
    planned_stop: Decimal
    planned_risk: Decimal
    exit_reason: str
    bars_held: int

    @property
    def r_multiple(self) -> Decimal:
        return self.result.net / self.planned_risk if self.planned_risk else Decimal(0)


@dataclass(frozen=True, slots=True)
class BacktestStats:
    trades: int
    wins: int
    net: Decimal
    gross: Decimal
    fees: Decimal
    profit_factor: Decimal | None
    expectancy_r: Decimal | None
    max_drawdown: Decimal  # als Bruch des Höchststands
    exposure: Decimal  # Anteil der Kerzen mit Position
    final_equity: Decimal
    return_pct: Decimal
    buy_hold_return_pct: Decimal  # gleiche Periode, Taker-Gebühr beim Kauf und Verkauf


@dataclass(slots=True)
class BacktestResult:
    strategy_version_id: str
    cost_model: str
    sim_version: str
    trades: list[ClosedTrade]
    equity_curve: list[tuple[datetime, Decimal]]
    blocked: Counter[str]
    entry_orders: int
    unfilled_entries: int
    stats: BacktestStats


def _order(order_id: str, key: str, instrument_id: str, side: Side, type_: OrderType, role: OrderRole, qty: Decimal, at: datetime, **prices: Decimal | None) -> Order:
    order = Order(
        id=order_id, intent_key=key, mode=Mode.RESEARCH, account_id="backtest", instrument_id=instrument_id,
        side=side, type=type_, role=role, qty=qty, created_at=at,
        limit_price=prices.get("limit_price"), stop_price=prices.get("stop_price"),
    )
    for state in (OrderState.CHECKED, OrderState.SUBMITTED, OrderState.ACCEPTED):
        transition(order, state, at)
    return order


def run_backtest(
    candles: list[Candle],
    own_regimes: list[RegimePoint],
    leader_regimes: list[RegimePoint],
    strategy: StrategyDef,
    cfg: BacktestConfig | None = None,
) -> BacktestResult:
    cfg = cfg or BacktestConfig()
    if not candles:
        raise ValueError("Keine Kerzen")
    instrument_id = candles[0].instrument_id
    policy, model, spec = cfg.policy, cfg.cost, cfg.spec
    decisions: list[Decision] = strategy.decide(candles, own_regimes, leader_regimes, tick=spec.tick)
    atr = ind.atr([float(c.high) for c in candles], [float(c.low) for c in candles], [float(c.close) for c in candles], 14)

    account = Account("backtest", cfg.currency, cfg.start_cash)
    entry_order: Order | None = None
    stop_order: Order | None = None
    target_order: Order | None = None
    exit_order: Order | None = None
    managed: ManagedTrade | None = None
    pending_plan: ExitPlan | None = None
    entry_fills: list[Fill] = []
    planned_risk = Decimal(0)
    exit_reason = ""

    trades: list[ClosedTrade] = []
    curve: list[tuple[datetime, Decimal]] = []
    blocked: Counter[str] = Counter()
    entry_orders = unfilled = bars_in_position = 0
    peak = day_start = week_start = cfg.start_cash
    current_day: date | None = None
    current_week: tuple[int, int] | None = None
    entries_today = losses_in_row = 0
    last_loss_at: datetime | None = None
    cooldown = timedelta(hours=policy.loss_streak_cooldown_hours)

    def held() -> Decimal:
        pos = account.positions.get(instrument_id)
        return pos.qty if pos else Decimal(0)

    def close_trade(exit_fills: list[Fill], reason: str, at: datetime) -> None:
        nonlocal managed, stop_order, target_order, exit_order, entry_fills, planned_risk, losses_in_row, last_loss_at
        assert managed is not None
        result = trade_result(entry_fills, exit_fills)
        trades.append(ClosedTrade(entry_fills[0].time, at, result, managed.plan.stop, planned_risk, reason, managed.bars_held))
        losses_in_row = losses_in_row + 1 if result.net < 0 else 0
        if result.net < 0:
            last_loss_at = at
        managed = stop_order = target_order = exit_order = None
        entry_fills, planned_risk = [], Decimal(0)

    def sell(order: Order, fill: Fill, reason: str) -> None:
        apply_fill(order, fill)
        account.sell_fill(instrument_id, fill.qty, fill.price, fill.fee)
        close_trade([fill], reason, fill.time)

    for i, candle in enumerate(candles):
        # --- A) Ausführung innerhalb der Kerze ---------------------------------------------------
        if exit_order is not None and (f := simulate(exit_order, candle, model, cfg.sim, cfg.currency)):
            sell(exit_order, f, exit_reason)
        if stop_order is not None and managed is not None and (f := simulate(stop_order, candle, model, cfg.sim, cfg.currency)):
            sell(stop_order, f, "Stop")
        if target_order is not None and managed is not None and (f := simulate(target_order, candle, model, cfg.sim, cfg.currency)):
            sell(target_order, f, "Ziel")

        if entry_order is not None and entry_order.is_working and (f := simulate(entry_order, candle, model, cfg.sim, cfg.currency)):
            assert pending_plan is not None
            res = account.reservations[entry_order.intent_key]
            planned_risk += res.risk * (f.qty / res.qty)
            apply_fill(entry_order, f)
            account.buy_fill(entry_order.intent_key, f.qty, f.price, f.fee, strategy.version.id)
            entry_fills.append(f)
            if managed is None:
                managed = start(pending_plan, f.price)
            # Schutzorder passt immer zur tatsächlich gefüllten Menge
            stop_order = _order(f"bt-{i}-stop", f"{entry_order.intent_key}:protect", instrument_id, Side.SELL, OrderType.STOP,
                                OrderRole.PROTECT, held(), candle.close_time, stop_price=managed.stop)
            if pending_plan.target is not None:
                target_order = _order(f"bt-{i}-target", f"{entry_order.intent_key}:target", instrument_id, Side.SELL, OrderType.LIMIT,
                                      OrderRole.EXIT, held(), candle.close_time, limit_price=pending_plan.target)
            # Reihenfolge innerhalb der Einstiegskerze ist unbekannt: fällt sie bis zum Stop, gilt er als ausgelöst.
            if candle.low <= managed.stop:
                price = managed.stop * (1 - model.slippage_bps / BPS)
                qty = held()
                stop_fill = Fill(f"{stop_order.id}:1", stop_order.id, qty, price, model.order_fee(qty, price, True), cfg.currency, candle.close_time)
                if entry_order.is_working:  # Rest des Einstiegs nicht weiter verfolgen
                    request_cancel(entry_order, candle.close_time)
                    confirm_cancel(entry_order, candle.close_time)
                    account.release(entry_order.intent_key)
                sell(stop_order, stop_fill, "Stop (in der Einstiegskerze)")

        # --- B) Am Kerzenschluss ------------------------------------------------------------------
        day, week = candle.close_time.date(), candle.close_time.isocalendar()[:2]
        equity = account.equity({instrument_id: candle.close})
        if day != current_day:
            current_day, day_start, entries_today = day, equity, 0
        if week != current_week:
            current_week, week_start = week, equity
        peak = max(peak, equity)

        if entry_order is not None:
            if entry_order.is_working:  # Einstiegsorder gilt eine Kerze; Rest verfällt
                if not entry_order.fills:
                    unfilled += 1
                request_cancel(entry_order, candle.close_time)
                confirm_cancel(entry_order, candle.close_time)
                account.release(entry_order.intent_key)
            entry_order = None

        if managed is not None:
            bars_in_position += 1
            reason = on_close(managed, candle, atr[i], regime_at(own_regimes, candle.close_time), spec.tick)
            if stop_order is not None:
                stop_order.stop_price = managed.stop
            if reason is not None and exit_order is None:
                exit_reason = reason
                exit_order = _order(f"bt-{i}-exit", f"exit:{i}", instrument_id, Side.SELL, OrderType.MARKET, OrderRole.EXIT, held(), candle.close_time)
                stop_order = target_order = None  # durch den Exit ersetzt; Summe offener Verkäufe ≤ Bestand
        elif i >= cfg.warmup_bars and i < len(candles) - 1:
            # Wartezeit nach einer Verlustserie ist abgelaufen: die Serie beginnt neu
            if losses_in_row >= policy.loss_streak_cooldown and last_loss_at is not None and candle.close_time >= last_loss_at + cooldown:
                losses_in_row = 0
            decision = decisions[i]
            if decision.action is Action.BUY and decision.entry is not None and decision.stop is not None:
                plan = strategy.exit_plan(decision)
                costs = plan_costs(model, decision.entry, decision.stop, decision.target)
                risk_budget = equity * policy.risk_per_trade * Decimal(str(strategy.risk_factor))
                fee_rate = model.maker_bps / BPS
                free = account.free_cash - equity * policy.cash_reserve
                max_notional = min(equity * policy.max_instrument_share, free / (1 + fee_rate))
                sizing = size_position(risk_budget, max_notional, decision.entry, decision.stop, model, spec)
                if decision.target is not None and (costs.net_reward_risk is None or costs.net_reward_risk < cfg.min_net_reward_risk):
                    blocked["Netto-Chance-Risiko nach Kosten zu klein"] += 1
                elif not sizing.ok:
                    blocked[sizing.reason or "Grösse"] += 1
                else:
                    key = f"{strategy.version.id}:{instrument_id}:{candle.close_time.isoformat()}:entry"
                    request = EntryRequest(key, instrument_id, strategy.version.id, sizing.qty, decision.entry, sizing.planned_risk,
                                           sizing.notional * (1 + fee_rate))
                    ctx = RiskContext(equity=equity, day_start_equity=day_start, week_start_equity=week_start, peak_equity=peak,
                                      entries_today=entries_today, consecutive_losses=losses_in_row, entries_allowed=True, data_fresh=True)
                    verdict = check_entry(request, account, ctx, policy)
                    if not verdict.allowed:
                        blocked[verdict.reasons[0].split()[0].rstrip(":")] += 1
                    else:
                        account.reserve(key, instrument_id, sizing.qty, request.cash_needed, sizing.planned_risk)
                        entry_order = _order(f"bt-{i}-entry", key, instrument_id, Side.BUY, OrderType.LIMIT, OrderRole.ENTRY,
                                             sizing.qty, candle.close_time, limit_price=decision.entry)
                        pending_plan = plan
                        entry_orders += 1
                        entries_today += 1
        curve.append((candle.close_time, equity))

    # Offene Position am Ende zum letzten Schluss bewerten (kein Trade, bleibt im Eigenkapital)
    return BacktestResult(
        strategy_version_id=strategy.version.id,
        cost_model=model.version,
        sim_version=cfg.sim.version,
        trades=trades,
        equity_curve=curve,
        blocked=blocked,
        entry_orders=entry_orders,
        unfilled_entries=unfilled,
        stats=_stats(trades, curve, cfg, candles, bars_in_position),
    )


def _stats(trades: list[ClosedTrade], curve: list[tuple[datetime, Decimal]], cfg: BacktestConfig, candles: list[Candle], bars_in_position: int) -> BacktestStats:
    zero = Decimal(0)
    wins = [t.result.net for t in trades if t.result.net > 0]
    losses = [t.result.net for t in trades if t.result.net < 0]
    peak, max_dd = cfg.start_cash, zero
    for _, eq in curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    final = curve[-1][1]
    first = candles[min(cfg.warmup_bars, len(candles) - 1)].close
    fee = cfg.cost.taker_bps / BPS
    buy_hold = (candles[-1].close * (1 - fee)) / (first * (1 + fee)) - 1
    return BacktestStats(
        trades=len(trades),
        wins=len(wins),
        net=sum((t.result.net for t in trades), zero),
        gross=sum((t.result.gross for t in trades), zero),
        fees=sum((t.result.fees for t in trades), zero),
        profit_factor=(sum(wins, zero) / -sum(losses, zero)) if losses else None,
        expectancy_r=(sum((t.r_multiple for t in trades), zero) / len(trades)) if trades else None,
        max_drawdown=max_dd,
        exposure=Decimal(bars_in_position) / Decimal(len(candles)),
        final_equity=final,
        return_pct=(final / cfg.start_cash - 1) * 100,
        buy_hold_return_pct=buy_hold * 100,
    )
