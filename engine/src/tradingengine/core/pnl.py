"""Gewinn-/Verlustrechnung eines Trades aus tatsächlichen Fills (docs/07, T12).

- Massgeblich sind Fill-Preise. Ein geplanter Stop korrigiert eine schlechtere Ausführung nie.
- Spread und Slippage stecken bereits im Fill-Preis und werden nicht zusätzlich abgezogen;
  sie werden nur informativ gegenüber einem Referenzpreis ausgewiesen.
- Kosten = Gebühren beider Seiten.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .execution import Fill


@dataclass(frozen=True, slots=True)
class TradeResult:
    qty: Decimal  # geschlossene Menge
    entry_value: Decimal
    exit_value: Decimal
    gross: Decimal
    fees: Decimal
    net: Decimal
    avg_entry: Decimal
    avg_exit: Decimal
    open_qty: Decimal  # noch nicht geschlossene Restmenge


def _value(fills: list[Fill]) -> Decimal:
    return sum((f.qty * f.price for f in fills), Decimal(0))


def _qty(fills: list[Fill]) -> Decimal:
    return sum((f.qty for f in fills), Decimal(0))


def _fees(fills: list[Fill]) -> Decimal:
    return sum((f.fee for f in fills), Decimal(0))


def trade_result(entry_fills: list[Fill], exit_fills: list[Fill]) -> TradeResult:
    """Ergebnis in Handelswährung. Gebühren müssen in Handelswährung vorliegen.

    Bei teilweise geschlossener Position werden Einstandswert und Einstiegsgebühren anteilig
    (Durchschnittspreis) der geschlossenen Menge zugerechnet."""
    bought, sold = _qty(entry_fills), _qty(exit_fills)
    if sold > bought:
        raise ValueError("Verkaufte Menge übersteigt die gekaufte Menge (keine Short-Positionen)")
    if bought == 0:
        raise ValueError("Trade ohne Einstiegs-Fill")
    share = sold / bought
    entry_value = _value(entry_fills) * share
    exit_value = _value(exit_fills)
    fees = _fees(entry_fills) * share + _fees(exit_fills)
    gross = exit_value - entry_value
    return TradeResult(
        qty=sold,
        entry_value=entry_value,
        exit_value=exit_value,
        gross=gross,
        fees=fees,
        net=gross - fees,
        avg_entry=_value(entry_fills) / bought,
        avg_exit=exit_value / sold if sold else Decimal(0),
        open_qty=bought - sold,
    )


@dataclass(frozen=True, slots=True)
class StopDeviation:
    planned_stop: Decimal
    executed: Decimal
    per_unit: Decimal
    total: Decimal  # negativ = schlechter als geplant


def stop_deviation(planned_stop: Decimal, exit_fills: list[Fill]) -> StopDeviation:
    """Abweichung der tatsächlichen Stop-Ausführung vom geplanten Stop (z. B. bei Kurslücken)."""
    qty = _qty(exit_fills)
    executed = _value(exit_fills) / qty
    return StopDeviation(planned_stop, executed, executed - planned_stop, (executed - planned_stop) * qty)


@dataclass(frozen=True, slots=True)
class ReportingResult:
    """Ergebnis in Berichtswährung, zerlegt in Handelsergebnis und Währungseffekt.
    Es gilt exakt: total == trading + fx_effect."""

    total: Decimal
    trading: Decimal
    fx_effect: Decimal
    fx_entry: Decimal
    fx_exit: Decimal


def to_reporting(result: TradeResult, entry_fees: Decimal, exit_fees: Decimal, fx_entry: Decimal, fx_exit: Decimal) -> ReportingResult:
    """Umrechnung mit dem Kurs zum Einstieg bzw. Ausstieg (Berichtswährung je Einheit Handelswährung).

    total = Ausstiegserlös und -gebühr zum Ausstiegskurs − Einstandswert und -gebühr zum Einstiegskurs.
    Der Währungseffekt ist die Kursänderung auf dem gebundenen Einstandswert."""
    total = (result.exit_value - exit_fees) * fx_exit - (result.entry_value + entry_fees) * fx_entry
    fx_effect = result.entry_value * (fx_exit - fx_entry)
    return ReportingResult(total=total, trading=total - fx_effect, fx_effect=fx_effect, fx_entry=fx_entry, fx_exit=fx_exit)


def spread_slippage_info(fills: list[Fill], reference_price: Decimal, is_buy: bool) -> Decimal:
    """Informativ: Kosten gegenüber einem Referenzpreis (z. B. Mid), bereits im Fill enthalten."""
    sign = Decimal(1) if is_buy else Decimal(-1)
    return sum(((f.price - reference_price) * f.qty * sign for f in fills), Decimal(0))
