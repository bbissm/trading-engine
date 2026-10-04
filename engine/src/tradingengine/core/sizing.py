"""Positionsgrösse aus fixem Risiko pro Trade (docs/04, Abschnitt 2; docs/06, Abschnitt 1).

Geplantes Verlustrisiko = Menge × (Einstieg − Stop + Kosten beider Seiten). Kapitalbindung (Nominal)
ist eine eigene Kennzahl und wird separat begrenzt."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from .costs import BPS, CostModel, plan_costs


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    tick: Decimal
    qty_step: Decimal
    min_qty: Decimal
    min_notional: Decimal


@dataclass(frozen=True, slots=True)
class Sizing:
    qty: Decimal
    notional: Decimal
    planned_risk: Decimal
    reason: str | None = None  # gesetzt, wenn qty == 0

    @property
    def ok(self) -> bool:
        return self.qty > 0


def _floor(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def size_position(
    risk_budget: Decimal,
    max_notional: Decimal,
    entry: Decimal,
    stop: Decimal,
    model: CostModel,
    spec: InstrumentSpec,
) -> Sizing:
    """Grösste Menge, die sowohl das Risikobudget als auch die Kapitalgrenze einhält."""
    zero = Decimal(0)
    if stop >= entry:
        return Sizing(zero, zero, zero, "Stop liegt nicht unter dem Einstieg")
    risk_per_unit = plan_costs(model, entry, stop, None).risk_per_unit
    if risk_budget <= 0 or max_notional <= 0:
        return Sizing(zero, zero, zero, "Kein Risiko- oder Kapitalbudget frei")
    qty = _floor(min(risk_budget / risk_per_unit, max_notional / entry), spec.qty_step)
    if not model.linear:
        # Gebühr je Order mit Mindestbetrag (z. B. IBKR): Menge verkleinern, bis das Gesamtrisiko passt.
        while qty >= spec.min_qty and qty > 0:
            risk = order_risk(model, qty, entry, stop)
            if risk <= risk_budget:
                break
            qty -= max(spec.qty_step, _floor((risk - risk_budget) / risk_per_unit, spec.qty_step))
    if qty < spec.min_qty or qty <= 0 or qty * entry < spec.min_notional:
        return Sizing(zero, zero, zero, "Menge unter der Mindestgrösse des Handelsplatzes")
    return Sizing(qty, qty * entry, qty * risk_per_unit if model.linear else order_risk(model, qty, entry, stop))


def order_risk(model: CostModel, qty: Decimal, entry: Decimal, stop: Decimal) -> Decimal:
    """Geplantes Verlustrisiko einer ganzen Order: Kursrisiko + Einstiegsgebühr + Slippage + Stop-Gebühr."""
    stop_fill = stop * (1 - model.slippage_bps / BPS)
    return qty * (entry - stop_fill) + model.order_fee(qty, entry, False) + model.order_fee(qty, stop_fill, True)
