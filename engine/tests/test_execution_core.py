"""Abnahmetests aus docs/07 auf dem fachlichen Kern: T6, T7, T12, T18, T19, T21."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from tradingengine.core.account import Account, AccountError
from tradingengine.core.candles import Candle
from tradingengine.core.costs import KRAKEN_SPOT_TIER1, CostModel, plan_costs
from tradingengine.core.execution import (
    Fill,
    Mode,
    Order,
    OrderError,
    OrderRole,
    OrderState,
    OrderType,
    Side,
    apply_fill,
    confirm_cancel,
    request_cancel,
    transition,
)
from tradingengine.core.exit_plan import ExitPlan
from tradingengine.core.exits import on_close, start
from tradingengine.core.pnl import spread_slippage_info, stop_deviation, to_reporting, trade_result
from tradingengine.core.regime import Regime
from tradingengine.core.risk import EntryRequest, RiskContext, RiskPolicy, check_entry, check_exit
from tradingengine.core.simulator import SimConfig, simulate
from tradingengine.core.sizing import InstrumentSpec, size_position

T0 = datetime(2026, 1, 1, tzinfo=UTC)
ZERO_COST = CostModel("zero", D(0), D(0), D(0))
SPEC = InstrumentSpec(tick=D("0.01"), qty_step=D("0.0001"), min_qty=D("0.001"), min_notional=D("5"))


def fill(fid: str, qty: str, price: str, fee: str = "0", order_id: str = "o1", minutes: int = 0) -> Fill:
    return Fill(fid, order_id, D(qty), D(price), D(fee), "USD", T0 + timedelta(minutes=minutes))


def order(qty: str, side: Side = Side.BUY, type_: OrderType = OrderType.LIMIT, role: OrderRole = OrderRole.ENTRY, order_id: str = "o1", **kw: D) -> Order:
    o = Order(order_id, f"k-{order_id}", Mode.PAPER, "acc", "TEST:AAA/USD", side, type_, role, D(qty), T0, **kw)
    for state in (OrderState.CHECKED, OrderState.SUBMITTED, OrderState.ACCEPTED):
        transition(o, state, T0)
    return o


def candle(open_: str, high: str, low: str, close: str, volume: str = "1000", start: datetime = T0) -> Candle:
    return Candle("TEST:AAA/USD", "4h", start, start + timedelta(hours=4), D(open_), D(high), D(low), D(close), D(volume), 10, "test")


# --- T12: Gewinn-/Verlustrechnung ------------------------------------------------------------------


def test_t12_1_example_profit() -> None:
    """10 × 100 gekauft, 10 × 104 verkauft, Gesamtkosten 2 → brutto +40, netto +38."""
    r = trade_result([fill("b", "10", "100", "1")], [fill("s", "10", "104", "1")])
    assert (r.gross, r.fees, r.net) == (D(40), D(2), D(38))


def test_t12_2_gap_through_stop_is_not_corrected() -> None:
    """Exit-Fill 97 bei geplantem Stop 98: netto −32; der Stop korrigiert die Ausführung nicht."""
    exits = [fill("s", "10", "97", "1")]
    r = trade_result([fill("b", "10", "100", "1")], exits)
    assert (r.gross, r.net) == (D(-30), D(-32))
    assert r.avg_exit == D(97)
    dev = stop_deviation(D(98), exits)
    assert (dev.executed, dev.per_unit, dev.total) == (D(97), D(-1), D(-10))


def test_t12_3_spread_and_slippage_are_not_deducted_twice() -> None:
    """Spread/Slippage stecken im Fill-Preis: informativ ausweisbar, aber nicht zusätzlich abgezogen."""
    buys, sells = [fill("b", "10", "100.05", "1")], [fill("s", "10", "103.95", "1")]
    r = trade_result(buys, sells)
    info = spread_slippage_info(buys, D("100"), True) + spread_slippage_info(sells, D("104"), False)
    assert info == D("1.00")
    assert r.net == D("103.95") * 10 - D("100.05") * 10 - 2  # nur Fills und Gebühren


def test_t12_4_partial_fills() -> None:
    r = trade_result([fill("b1", "4", "100", "0.8"), fill("b2", "6", "101", "1.2")], [fill("s", "10", "104", "0")])
    assert (r.gross, r.net) == (D(34), D(32))
    assert r.avg_entry == D("100.6")


def test_t12_partial_exit_allocates_entry_cost_pro_rata() -> None:
    r = trade_result([fill("b", "10", "100", "2")], [fill("s", "4", "105", "1")])
    assert (r.qty, r.open_qty) == (D(4), D(6))
    assert r.entry_value == D(400) and r.fees == D("1.8") and r.net == D(20) - D("1.8")


def test_t12_5_currency_split_sums_exactly() -> None:
    """USD-Trade in CHF: Handelsergebnis + Währungseffekt = Gesamtergebnis; Kurse bleiben am Ergebnis."""
    r = trade_result([fill("b", "10", "100", "1")], [fill("s", "10", "104", "1")])
    rep = to_reporting(r, entry_fees=D(1), exit_fees=D(1), fx_entry=D("0.90"), fx_exit=D("0.88"))
    assert rep.total == (D(1040) - 1) * D("0.88") - (D(1000) + 1) * D("0.90")
    assert rep.fx_effect == D(1000) * (D("0.88") - D("0.90"))
    assert rep.trading + rep.fx_effect == rep.total
    assert (rep.total, rep.trading, rep.fx_effect) == (D("13.42"), D("33.42"), D("-20.00"))  # der Währungseffekt ist sichtbar
    assert (rep.fx_entry, rep.fx_exit) == (D("0.90"), D("0.88"))


def test_t18_no_short_positions() -> None:
    with pytest.raises(ValueError, match="Short"):
        trade_result([fill("b", "5", "100")], [fill("s", "6", "101")])
    acc = Account("a", "USD", D(1000))
    assert not check_exit("TEST:AAA/USD", D(1), acc).allowed  # SELL ohne Bestand erzeugt keine Order
    acc.reserve("k", "TEST:AAA/USD", D(5), D(500), D(10))
    acc.buy_fill("k", D(5), D(100), D(0), "s1")
    assert check_exit("TEST:AAA/USD", D(5), acc).allowed
    assert not check_exit("TEST:AAA/USD", D(6), acc).allowed
    assert not check_exit("TEST:AAA/USD", D(3), acc, working_sell_qty=D(3)).allowed  # Summe offener Verkäufe ≤ Bestand
    with pytest.raises(AccountError, match="Short"):
        acc.sell_fill("TEST:AAA/USD", D(6), D(100), D(0))


# --- T7: Teilfüllungen und Fill-/Storno-Rennen -----------------------------------------------------


def test_t7_1_partial_fills_then_cancel() -> None:
    acc = Account("a", "USD", D(2000))
    o = order("10", limit_price=D(100))
    acc.reserve(o.intent_key, o.instrument_id, D(10), D(1004), D(20))
    for f in (fill("f1", "3", "100", "1.2"), fill("f2", "4", "100", "1.6", minutes=1)):
        assert apply_fill(o, f)
        acc.buy_fill(o.intent_key, f.qty, f.price, f.fee, "s1")
    assert o.state is OrderState.PARTIALLY_FILLED and o.filled_qty == 7

    request_cancel(o, T0 + timedelta(minutes=2))
    assert acc.reservations[o.intent_key].qty == 3  # erst die Bestätigung gibt die Reservierung frei
    assert confirm_cancel(o, T0 + timedelta(minutes=3)) == 3
    acc.release(o.intent_key)

    pos = acc.positions[o.instrument_id]
    assert pos.qty == 7 and not acc.reservations
    assert acc.cash == D(2000) - 700 - D("2.8")
    assert pos.planned_risk == D(14)  # 7/10 des geplanten Risikos


def test_t7_2_fill_arrives_after_cancel_request() -> None:
    o = order("10", limit_price=D(100))
    apply_fill(o, fill("f1", "3", "100"))
    apply_fill(o, fill("f2", "4", "100"))
    request_cancel(o, T0)
    assert apply_fill(o, fill("f3", "2", "100"))  # Fill während der Stornierung wird verarbeitet
    assert o.state is OrderState.CANCEL_REQUESTED and o.filled_qty == 9
    assert confirm_cancel(o, T0) == 1
    assert o.state is OrderState.CANCELED


def test_t7_3_duplicate_fill_is_ignored_and_overfill_rejected() -> None:
    o = order("10", limit_price=D(100))
    assert apply_fill(o, fill("f1", "6", "100"))
    assert not apply_fill(o, fill("f1", "6", "100"))
    assert o.filled_qty == 6
    with pytest.raises(OrderError, match="Restmenge"):
        apply_fill(o, fill("f2", "5", "100"))
    apply_fill(o, fill("f3", "4", "100"))
    assert o.state is OrderState.FILLED
    with pytest.raises(OrderError):
        apply_fill(o, fill("f4", "1", "100"))


def test_order_state_machine_rejects_illegal_transitions() -> None:
    o = Order("o", "k", Mode.PAPER, "acc", "X", Side.BUY, OrderType.LIMIT, OrderRole.ENTRY, D(1), T0, limit_price=D(1))
    with pytest.raises(OrderError):
        transition(o, OrderState.FILLED, T0)  # nie ohne Prüfung und Übermittlung
    o2 = order("1", limit_price=D(1))
    transition(o2, OrderState.UNKNOWN, T0)  # Timeout nach Übermittlung
    with pytest.raises(OrderError):
        transition(o2, OrderState.SUBMITTED, T0)  # kein blindes erneutes Senden
    transition(o2, OrderState.ACCEPTED, T0)  # Statusklärung löst UNKNOWN auf


# --- T6: Reservierung und gemeinsames Budget -------------------------------------------------------


def _ctx(**kw: object) -> RiskContext:
    start = D(10000)
    base: dict[str, object] = dict(
        equity=start, day_start_equity=start, week_start_equity=start, peak_equity=start,
        entries_today=0, consecutive_losses=0, entries_allowed=True, data_fresh=True,
    )
    base.update(kw)
    return RiskContext(**base)  # type: ignore[arg-type]


def _req(key: str, instrument: str, qty: str = "10", entry: str = "100", risk: str = "50") -> EntryRequest:
    return EntryRequest(key, instrument, "s1", D(qty), D(entry), D(risk), D(qty) * D(entry) * D("1.004"))


def test_t6_simultaneous_entries_share_one_budget() -> None:
    """Fünf gleichzeitige Signale: Reservierungen zählen sofort, Budget/Risiko/Positionszahl halten."""
    policy = RiskPolicy(max_positions=2)
    acc = Account("a", "USD", D(10000))
    allowed = []
    for n in range(5):
        req = _req(f"k{n}", f"TEST:I{n}/USD", qty="19", entry="100", risk="50")
        verdict = check_entry(req, acc, _ctx(), policy)
        if verdict.allowed:
            acc.reserve(req.intent_key, req.instrument_id, req.qty, req.cash_needed, req.planned_risk)
            allowed.append(n)
        # Invarianten nach jedem Schritt
        assert acc.free_cash >= D(10000) * policy.cash_reserve
        assert acc.open_risk + acc.reserved_risk <= D(10000) * policy.max_open_risk
        assert acc.open_position_count() <= policy.max_positions
    assert allowed == [0, 1]
    third = check_entry(_req("k9", "TEST:I9/USD", qty="19"), acc, _ctx(), policy)
    assert not third.allowed and any("Positionen" in r for r in third.reasons)


def test_t6_duplicate_intent_and_same_instrument_are_blocked() -> None:
    acc = Account("a", "USD", D(10000))
    req = _req("k1", "TEST:AAA/USD")
    acc.reserve(req.intent_key, req.instrument_id, req.qty, req.cash_needed, req.planned_risk)
    with pytest.raises(AccountError, match="bereits reserviert"):
        acc.reserve(req.intent_key, req.instrument_id, req.qty, req.cash_needed, req.planned_risk)
    again = check_entry(_req("k2", "TEST:AAA/USD"), acc, _ctx(), RiskPolicy())
    assert not again.allowed and any("bereits ein Einstieg reserviert" in r for r in again.reasons)


def test_t8_unknown_values_block_and_are_never_treated_as_zero() -> None:
    acc, policy, req = Account("a", "USD", D(10000)), RiskPolicy(), _req("k", "TEST:AAA/USD")
    assert check_entry(req, acc, _ctx(), policy).allowed
    for kw, text in (
        ({"equity": None}, "Eigenkapital unbekannt"),
        ({"peak_equity": None}, "Höchststand unbekannt"),
        ({"entries_today": None}, "unbekannt"),
        ({"data_fresh": False}, "nicht frisch"),
        ({"unknown_orders": 1}, "unbekanntem Status"),
        ({"reconciliation_ok": False}, "Abgleich"),
        ({"entries_allowed": False}, "erlaubt keine Einstiege"),
        ({"require_spread": True}, "Spread unbekannt"),
        ({"spread_bps": D(40)}, "Spread 40"),
    ):
        verdict = check_entry(req, acc, _ctx(**kw), policy)
        assert not verdict.allowed and any(text in r for r in verdict.reasons), kw


def test_t9_loss_limits_block_entries_but_not_exits() -> None:
    acc, policy = Account("a", "USD", D(10000)), RiskPolicy()
    acc.reserve("held", "TEST:BBB/USD", D(5), D(500), D(10))
    acc.buy_fill("held", D(5), D(100), D(0), "s1")
    for kw, text in (
        ({"equity": D(9840)}, "Tagesverlust"),  # −1.6 % ≥ 1.5 %
        ({"equity": D(9990), "week_start_equity": D(10400)}, "Wochenverlust"),
        ({"equity": D(9990), "peak_equity": D(11000)}, "Drawdown"),
        ({"consecutive_losses": 3}, "Wartezeit"),
        ({"entries_today": 4}, "Einstiege pro Tag"),
    ):
        verdict = check_entry(_req("k", "TEST:AAA/USD"), acc, _ctx(**kw), policy)
        assert not verdict.allowed and any(text in r for r in verdict.reasons), kw
    assert check_exit("TEST:BBB/USD", D(5), acc).allowed  # Ausstiege bleiben immer zulässig


# --- Kosten und Positionsgrösse ----------------------------------------------------------------------


def test_plan_costs_and_sizing_include_fees_of_both_sides() -> None:
    costs = plan_costs(KRAKEN_SPOT_TIER1, D(100), D(96), D(112))
    assert costs.entry_fee == D("0.40")
    assert costs.risk_per_unit > D(4) + D("0.40") + D("0.76")  # Stopabstand + Maker-Einstieg + Taker-Ausstieg + Slippage
    assert costs.net_reward_risk is not None and costs.net_reward_risk < D(3)  # brutto wäre 3.0
    sizing = size_position(D(50), D(2000), D(100), D(96), KRAKEN_SPOT_TIER1, SPEC)
    assert sizing.ok and sizing.planned_risk <= D(50) and sizing.qty * D(100) <= D(2000)
    assert sizing.qty == (D(50) / costs.risk_per_unit).quantize(D("0.0001"), rounding="ROUND_DOWN")
    capped = size_position(D(500), D(300), D(100), D(96), KRAKEN_SPOT_TIER1, SPEC)
    assert capped.qty == D(3)  # Kapitalgrenze bindet vor dem Risikobudget
    assert not size_position(D(50), D(2000), D(100), D(100), KRAKEN_SPOT_TIER1, SPEC).ok
    tiny = size_position(D("0.01"), D(2000), D(100), D(96), KRAKEN_SPOT_TIER1, SPEC)
    assert not tiny.ok and "Mindestgrösse" in (tiny.reason or "")


# --- T21: Simulator -----------------------------------------------------------------------------------


def test_t21_limit_touch_is_not_a_fill() -> None:
    o = order("1", limit_price=D(100))
    nxt = T0 + timedelta(hours=4)
    assert simulate(o, candle("101", "102", "100", "101.5", start=nxt), ZERO_COST, SimConfig(), "USD") is None  # nur berührt
    f = simulate(o, candle("101", "102", "99.9", "101.5", start=nxt), ZERO_COST, SimConfig(), "USD")
    assert f is not None and f.price == D(100)
    gap = simulate(o, candle("98", "99", "97", "98.5", start=nxt), ZERO_COST, SimConfig(), "USD")
    assert gap is not None and gap.price == D(98)  # Eröffnung unter dem Limit: zum Eröffnungskurs


def test_t21_order_never_trades_before_its_creation() -> None:
    o = order("1", limit_price=D(100))
    earlier = candle("90", "91", "89", "90", start=T0 - timedelta(hours=4))
    assert simulate(o, earlier, ZERO_COST, SimConfig(), "USD") is None


def test_t21_partial_fill_by_volume_participation() -> None:
    o = order("50", limit_price=D(100))
    f = simulate(o, candle("99", "100", "98", "99", volume="1000", start=T0 + timedelta(hours=4)), ZERO_COST, SimConfig(participation=D("0.01")), "USD")
    assert f is not None and f.qty == D(10)


def test_t21_stop_fills_at_worse_of_stop_and_open_with_slippage_and_taker_fee() -> None:
    model = CostModel("m", D(40), D(80), D(10))
    nxt = T0 + timedelta(hours=4)
    stop = order("10", Side.SELL, OrderType.STOP, OrderRole.PROTECT, order_id="s1", stop_price=D(98))
    assert simulate(stop, candle("100", "101", "98.5", "100", start=nxt), model, SimConfig(), "USD") is None
    normal = simulate(stop, candle("100", "101", "97.5", "99", start=nxt), model, SimConfig(), "USD")
    assert normal is not None and normal.price == D(98) * D("0.999")
    assert normal.fee == normal.qty * normal.price * D("0.008")
    gap = simulate(stop, candle("97", "97.5", "96", "97", start=nxt), model, SimConfig(), "USD")
    assert gap is not None and gap.price == D(97) * D("0.999")  # Kurslücke: Eröffnung statt Stop


def test_sell_limit_target_needs_trade_through() -> None:
    nxt = T0 + timedelta(hours=4)
    target = order("10", Side.SELL, OrderType.LIMIT, OrderRole.EXIT, order_id="t1", limit_price=D(110))
    assert simulate(target, candle("105", "110", "104", "108", start=nxt), ZERO_COST, SimConfig(), "USD") is None
    hit = simulate(target, candle("105", "110.5", "104", "108", start=nxt), ZERO_COST, SimConfig(), "USD")
    assert hit is not None and hit.price == D(110) and hit.qty == D(10)


# --- T19: Stop wird nie weiter entfernt ----------------------------------------------------------------


def test_t19_trailing_stop_only_tightens() -> None:
    plan = ExitPlan(stop=D(96), max_hold_bars=40, trail_atr=3.0, exit_unless_regime=frozenset({Regime.UP}))
    trade = start(plan, D(100))
    stops = []
    closes = ["101", "104", "108", "103", "99", "107"]
    for n, close in enumerate(closes):
        c = candle(close, str(D(close) + 1), str(D(close) - 1), close, start=T0 + timedelta(hours=4 * n))
        assert on_close(trade, c, atr=2.0, regime=Regime.UP, tick=D("0.01")) is None
        stops.append(trade.stop)
    assert stops == sorted(stops)  # monoton nicht fallend
    assert stops[2] == D(102) and stops[-1] == D(102)  # höchster Schluss 108 − 3 × 2; Rückgang lockert nichts
    assert stops[0] == D(96)  # 101 − 6 = 95 läge unter dem Anfangsstop: bleibt 96


def test_exit_reasons_regime_time_and_failed_breakout() -> None:
    c = candle("100", "101", "99", "100")
    regime_trade = start(ExitPlan(stop=D(96), max_hold_bars=40, exit_unless_regime=frozenset({Regime.UP})), D(100))
    assert "Regimewechsel" in (on_close(regime_trade, c, 2.0, Regime.STRESS, None) or "")
    time_trade = start(ExitPlan(stop=D(96), max_hold_bars=2), D(100))
    assert on_close(time_trade, c, 2.0, Regime.SIDEWAYS, None) is None
    assert "Haltedauer" in (on_close(time_trade, c, 2.0, Regime.SIDEWAYS, None) or "")
    breakout = start(ExitPlan(stop=D(96), max_hold_bars=30, breakout_level=D(101), failed_breakout_bars=3), D(102))
    assert "Gescheiterter Ausbruch" in (on_close(breakout, c, 2.0, Regime.UP, None) or "")
