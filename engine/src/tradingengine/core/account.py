"""Kontobuch für ein (virtuelles oder echtes) Konto: Cash, Reservierungen, Positionen (docs/06, 2.1).

Reservierungen verhindern, dass mehrere gleichzeitige Entscheidungen dasselbe Geld oder dasselbe
Risikobudget verplanen. Frei wird Reserviertes erst durch einen Fill (Umbuchung in Bestand) oder eine
*bestätigte* Stornierung. Long-only: Verkäufe über den Bestand hinaus sind unmöglich.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


class AccountError(ValueError):
    pass


@dataclass(slots=True)
class Reservation:
    intent_key: str
    instrument_id: str
    qty: Decimal
    cash: Decimal  # reservierter Betrag inkl. geschätzter Einstiegsgebühr
    risk: Decimal  # reserviertes geplantes Verlustrisiko


@dataclass(slots=True)
class Position:
    instrument_id: str
    qty: Decimal = Decimal(0)
    cost: Decimal = Decimal(0)  # Einstandswert der gehaltenen Menge (ohne Gebühren)
    planned_risk: Decimal = Decimal(0)  # geplantes Stop-Risiko der gehaltenen Menge
    owner: str | None = None  # Strategieversion, deren Exit-Plan gilt

    @property
    def avg_price(self) -> Decimal:
        return self.cost / self.qty if self.qty else Decimal(0)


@dataclass(slots=True)
class Account:
    id: str
    currency: str
    cash: Decimal
    positions: dict[str, Position] = field(default_factory=dict)
    reservations: dict[str, Reservation] = field(default_factory=dict)
    fees_paid: Decimal = Decimal(0)
    realized: Decimal = Decimal(0)

    # --- Kennzahlen ---------------------------------------------------------------------------
    @property
    def reserved_cash(self) -> Decimal:
        return sum((r.cash for r in self.reservations.values()), Decimal(0))

    @property
    def reserved_risk(self) -> Decimal:
        return sum((r.risk for r in self.reservations.values()), Decimal(0))

    @property
    def free_cash(self) -> Decimal:
        return self.cash - self.reserved_cash

    @property
    def open_risk(self) -> Decimal:
        return sum((p.planned_risk for p in self.positions.values()), Decimal(0))

    @property
    def invested(self) -> Decimal:
        return sum((p.cost for p in self.positions.values()), Decimal(0))

    def open_position_count(self) -> int:
        """Gehaltene Positionen plus reservierte Einstiege in noch nicht gehaltene Instrumente."""
        held = {i for i, p in self.positions.items() if p.qty > 0}
        return len(held | {r.instrument_id for r in self.reservations.values()})

    def equity(self, prices: dict[str, Decimal]) -> Decimal:
        """Cash + Marktwert. Fehlt ein Preis für eine gehaltene Position, ist das Eigenkapital unbekannt."""
        total = self.cash
        for instrument_id, pos in self.positions.items():
            if pos.qty == 0:
                continue
            if instrument_id not in prices:
                raise AccountError(f"Kein Preis für {instrument_id}: Eigenkapital unbekannt")
            total += pos.qty * prices[instrument_id]
        return total

    # --- Buchungen ----------------------------------------------------------------------------
    def reserve(self, intent_key: str, instrument_id: str, qty: Decimal, cash: Decimal, risk: Decimal) -> Reservation:
        if intent_key in self.reservations:
            raise AccountError(f"Absicht {intent_key} ist bereits reserviert")
        if cash > self.free_cash:
            raise AccountError("Reservierung übersteigt das freie Cash")
        res = Reservation(intent_key, instrument_id, qty, cash, risk)
        self.reservations[intent_key] = res
        return res

    def buy_fill(self, intent_key: str, qty: Decimal, price: Decimal, fee: Decimal, owner: str) -> None:
        """Einstiegs-Fill: gefüllter Anteil der Reservierung wird Bestand; Rest bleibt reserviert."""
        res = self.reservations.get(intent_key)
        if res is None or qty > res.qty:
            raise AccountError("Fill ohne passende Reservierung")
        share = qty / res.qty
        risk_part, cash_part = res.risk * share, res.cash * share
        res.qty -= qty
        res.cash -= cash_part
        res.risk -= risk_part
        if res.qty == 0:
            del self.reservations[intent_key]

        pos = self.positions.setdefault(res.instrument_id, Position(res.instrument_id))
        if pos.qty > 0 and pos.owner != owner:
            raise AccountError(f"{res.instrument_id} wird bereits von {pos.owner} gehalten")
        pos.owner = owner
        pos.qty += qty
        pos.cost += qty * price
        pos.planned_risk += risk_part
        self.cash -= qty * price + fee
        self.fees_paid += fee

    def release(self, intent_key: str) -> None:
        """Nur nach bestätigter Stornierung, Ablehnung oder Ablauf der Einstiegsorder."""
        self.reservations.pop(intent_key, None)

    def sell_fill(self, instrument_id: str, qty: Decimal, price: Decimal, fee: Decimal) -> Decimal:
        """Ausstiegs-Fill. Rückgabe: realisiertes Nettoergebnis dieses Fills (inkl. anteiliger Kosten nicht – nur Exit-Gebühr)."""
        pos = self.positions.get(instrument_id)
        if pos is None or qty > pos.qty:
            raise AccountError("Verkauf übersteigt den Bestand (keine Short-Positionen)")
        share = qty / pos.qty
        cost_part = pos.cost * share
        pos.planned_risk -= pos.planned_risk * share
        pos.cost -= cost_part
        pos.qty -= qty
        if pos.qty == 0:
            pos.owner = None
        self.cash += qty * price - fee
        self.fees_paid += fee
        result = qty * price - cost_part - fee
        self.realized += result
        return result
