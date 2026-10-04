"""Unabhängige Risikoprüfung vor jeder risikosteigernden Order (docs/06, 2.1).

Reine Funktion über Kontobuch, Policy und Marktkontext. Jeder nicht bestimmbare Wert zählt als
Verletzung, nicht als null. Die Policy ist für Strategie- und Lerncode nicht veränderbar (frozen);
risikosenkende Orders werden hier nie blockiert (siehe check_exit).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .account import Account


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """Startwerte gemäss docs/06, Abschnitt 2 (Annahme A-RISK). Anteile als Bruch (0.005 = 0.5 %)."""

    version: str = "risk@1"
    risk_per_trade: Decimal = Decimal("0.005")
    max_open_risk: Decimal = Decimal("0.025")
    max_positions: int = 6
    max_instrument_share: Decimal = Decimal("0.20")
    cash_reserve: Decimal = Decimal("0.10")
    daily_loss_limit: Decimal = Decimal("0.015")
    weekly_loss_limit: Decimal = Decimal("0.03")
    drawdown_pause: Decimal = Decimal("0.08")
    drawdown_emergency: Decimal = Decimal("0.12")
    max_entries_per_day: int = 4
    loss_streak_cooldown: int = 3
    loss_streak_cooldown_hours: int = 24
    max_spread_bps: Decimal = Decimal(15)
    max_quote_age_s: int = 10


@dataclass(frozen=True, slots=True)
class EntryRequest:
    intent_key: str
    instrument_id: str
    strategy_version_id: str
    qty: Decimal
    entry: Decimal
    planned_risk: Decimal  # Menge × (Einstieg − Stop + Kosten)
    cash_needed: Decimal  # Nominal + geschätzte Einstiegsgebühr


@dataclass(frozen=True, slots=True)
class RiskContext:
    """Zustand ausserhalb des Kontobuchs. None bedeutet «unbekannt» und blockiert."""

    equity: Decimal | None
    day_start_equity: Decimal | None
    week_start_equity: Decimal | None
    peak_equity: Decimal | None
    entries_today: int | None
    consecutive_losses: int | None  # der Strategie; der Aufrufer setzt die Serie nach Ablauf der Wartezeit zurück
    entries_allowed: bool  # Autopilot-Zustand erlaubt Einstiege
    data_fresh: bool
    reconciliation_ok: bool = True
    unknown_orders: int = 0
    spread_bps: Decimal | None = None  # None = nicht verfügbar (z. B. Backtest auf Kerzen)
    require_spread: bool = False


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    values: dict[str, str] = field(default_factory=dict)


def loss_limits(ctx: RiskContext, policy: RiskPolicy) -> list[str]:
    """Verletzte Verlustgrenzen (auch für die laufende Überwachung durch den Guardian)."""
    reasons: list[str] = []
    eq = ctx.equity
    if eq is None:
        return ["Eigenkapital unbekannt"]
    for label, start, limit in (
        ("Tagesverlust", ctx.day_start_equity, policy.daily_loss_limit),
        ("Wochenverlust", ctx.week_start_equity, policy.weekly_loss_limit),
    ):
        if start is None:
            reasons.append(f"{label}: Ausgangswert unbekannt")
        elif start > 0 and (start - eq) / start >= limit:
            reasons.append(f"{label} {(start - eq) / start:.2%} ≥ Limit {limit:.2%}")
    if ctx.peak_equity is None:
        reasons.append("Drawdown: Höchststand unbekannt")
    elif ctx.peak_equity > 0 and (ctx.peak_equity - eq) / ctx.peak_equity >= policy.drawdown_pause:
        reasons.append(f"Drawdown {(ctx.peak_equity - eq) / ctx.peak_equity:.2%} ≥ Limit {policy.drawdown_pause:.2%}")
    return reasons


def check_entry(req: EntryRequest, account: Account, ctx: RiskContext, policy: RiskPolicy) -> RiskDecision:
    reasons: list[str] = []
    if not ctx.entries_allowed:
        reasons.append("Autopilot-Zustand erlaubt keine Einstiege")
    if not ctx.data_fresh:
        reasons.append("Marktdaten nicht frisch")
    if not ctx.reconciliation_ok:
        reasons.append("Abgleich mit dem Anbieter nicht in Ordnung")
    if ctx.unknown_orders:
        reasons.append(f"{ctx.unknown_orders} Order(s) mit unbekanntem Status")
    if ctx.require_spread and ctx.spread_bps is None:
        reasons.append("Spread unbekannt")
    elif ctx.spread_bps is not None and ctx.spread_bps > policy.max_spread_bps:
        reasons.append(f"Spread {ctx.spread_bps} bp > Limit {policy.max_spread_bps} bp")

    reasons.extend(loss_limits(ctx, policy))
    if ctx.entries_today is None:
        reasons.append("Anzahl heutiger Einstiege unbekannt")
    elif ctx.entries_today >= policy.max_entries_per_day:
        reasons.append(f"Maximal {policy.max_entries_per_day} Einstiege pro Tag erreicht")
    if ctx.consecutive_losses is None:
        reasons.append("Verlustserie unbekannt")
    elif ctx.consecutive_losses >= policy.loss_streak_cooldown:
        reasons.append(f"Wartezeit nach {ctx.consecutive_losses} Verlusttrades in Folge")

    values: dict[str, str] = {}
    eq = ctx.equity
    if eq is not None and eq > 0:
        pos = account.positions.get(req.instrument_id)
        if pos is not None and pos.qty > 0:
            reasons.append(f"Instrument bereits durch {pos.owner} gehalten")
        if any(r.instrument_id == req.instrument_id for r in account.reservations.values()):
            reasons.append("Für dieses Instrument ist bereits ein Einstieg reserviert")
        if account.open_position_count() >= policy.max_positions:
            reasons.append(f"Maximal {policy.max_positions} gleichzeitige Positionen")

        free = account.free_cash - eq * policy.cash_reserve
        values["cash_frei_nach_reserve"] = f"{free:.2f}"
        if req.cash_needed > free:
            reasons.append(f"Budget: benötigt {req.cash_needed:.2f}, frei nach Reserve {free:.2f}")
        notional = req.qty * req.entry
        values["nominal"] = f"{notional:.2f}"
        if notional > eq * policy.max_instrument_share:
            reasons.append(f"Instrumentgrenze: {notional:.2f} > {eq * policy.max_instrument_share:.2f}")
        if req.planned_risk > eq * policy.risk_per_trade * Decimal("1.0001"):
            reasons.append(f"Risiko pro Trade {req.planned_risk:.2f} > {eq * policy.risk_per_trade:.2f}")
        total_risk = account.open_risk + account.reserved_risk + req.planned_risk
        values["offenes_risiko_inkl_neu"] = f"{total_risk:.2f}"
        if total_risk > eq * policy.max_open_risk:
            reasons.append(f"Gesamtrisiko {total_risk:.2f} > {eq * policy.max_open_risk:.2f}")
    return RiskDecision(allowed=not reasons, reasons=reasons, values=values)


def check_exit(instrument_id: str, qty: Decimal, account: Account, working_sell_qty: Decimal = Decimal(0)) -> RiskDecision:
    """Risikosenkende Order: nur Plausibilität. Nie Short, Summe offener Verkäufe ≤ Bestand."""
    pos = account.positions.get(instrument_id)
    held = pos.qty if pos else Decimal(0)
    if qty <= 0 or held == 0:
        return RiskDecision(False, ["Kein Bestand: SELL/EXIT/REDUCE erzeugt keine Order"])
    if qty + working_sell_qty > held:
        return RiskDecision(False, [f"Verkaufsmenge {qty + working_sell_qty} übersteigt den Bestand {held}"])
    return RiskDecision(True)
