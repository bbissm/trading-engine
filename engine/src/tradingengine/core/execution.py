"""Order, Fill und Order-Zustandsautomat (docs/01, 3.4) – modusneutral für Backtest, Paper und Live.

Grundsätze:
- Die gefüllte Menge ist immer die Summe der Fill-Datensätze, nie ein überschriebenes Feld.
- Eine Stornierungsanfrage ist keine Stornierung: bis zur Bestätigung werden Fills verarbeitet.
- Doppelt gelieferte Fills (gleiche Fill-ID) sind wirkungslos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class Mode(StrEnum):
    PAPER = "PAPER"
    LIVE = "LIVE"
    RESEARCH = "RESEARCH"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP = "STOP"


class OrderRole(StrEnum):
    ENTRY = "ENTRY"
    PROTECT = "PROTECT"
    EXIT = "EXIT"


class OrderState(StrEnum):
    PREPARED = "PREPARED"
    CHECKED = "CHECKED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


S = OrderState
TERMINAL = {S.FILLED, S.CANCELED, S.REJECTED, S.EXPIRED}
# Zustände, in denen die Order beim Anbieter liegen und noch gefüllt werden kann
WORKING = {S.SUBMITTED, S.ACCEPTED, S.PARTIALLY_FILLED, S.CANCEL_REQUESTED, S.UNKNOWN}

_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    S.PREPARED: {S.CHECKED, S.REJECTED},
    S.CHECKED: {S.SUBMITTED, S.REJECTED, S.EXPIRED},
    S.SUBMITTED: {S.ACCEPTED, S.PARTIALLY_FILLED, S.FILLED, S.REJECTED, S.UNKNOWN, S.CANCEL_REQUESTED, S.EXPIRED},
    S.ACCEPTED: {S.PARTIALLY_FILLED, S.FILLED, S.CANCEL_REQUESTED, S.CANCELED, S.EXPIRED, S.UNKNOWN},
    S.PARTIALLY_FILLED: {S.PARTIALLY_FILLED, S.FILLED, S.CANCEL_REQUESTED, S.CANCELED, S.EXPIRED, S.UNKNOWN},
    S.CANCEL_REQUESTED: {S.CANCELED, S.FILLED, S.CANCEL_REQUESTED, S.UNKNOWN},
    # UNKNOWN wird nur durch Statusklärung beim Anbieter aufgelöst, nie durch erneutes Senden
    S.UNKNOWN: {S.ACCEPTED, S.PARTIALLY_FILLED, S.FILLED, S.CANCELED, S.REJECTED, S.EXPIRED},
}


class OrderError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Fill:
    id: str
    order_id: str
    qty: Decimal
    price: Decimal
    fee: Decimal
    fee_currency: str
    time: datetime
    simulated: bool = True


@dataclass(slots=True)
class Order:
    id: str
    intent_key: str
    mode: Mode
    account_id: str
    instrument_id: str
    side: Side
    type: OrderType
    role: OrderRole
    qty: Decimal
    created_at: datetime
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    valid_until: datetime | None = None
    state: OrderState = OrderState.PREPARED
    fills: list[Fill] = field(default_factory=list)
    history: list[tuple[datetime, OrderState]] = field(default_factory=list)

    @property
    def filled_qty(self) -> Decimal:
        return sum((f.qty for f in self.fills), Decimal(0))

    @property
    def remaining_qty(self) -> Decimal:
        return self.qty - self.filled_qty

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def is_working(self) -> bool:
        return self.state in WORKING


def transition(order: Order, new_state: OrderState, at: datetime) -> None:
    if new_state not in _TRANSITIONS.get(order.state, set()):
        raise OrderError(f"Übergang {order.state.value} → {new_state.value} ist nicht erlaubt")
    order.state = new_state
    order.history.append((at, new_state))


def apply_fill(order: Order, fill: Fill) -> bool:
    """Verbucht einen Fill. Rückgabe False, wenn die Fill-ID schon verarbeitet wurde (Duplikat)."""
    if any(f.id == fill.id for f in order.fills):
        return False
    if fill.order_id != order.id:
        raise OrderError("Fill gehört zu einer anderen Order")
    if order.state not in WORKING:
        raise OrderError(f"Fill für Order im Zustand {order.state.value}")
    if fill.qty <= 0 or fill.qty > order.remaining_qty:
        raise OrderError(f"Fill-Menge {fill.qty} passt nicht zur Restmenge {order.remaining_qty}")
    order.fills.append(fill)
    if order.remaining_qty == 0:
        transition(order, S.FILLED, fill.time)
    elif order.state is not S.CANCEL_REQUESTED:  # Storno bleibt angefragt, bis der Anbieter bestätigt
        transition(order, S.PARTIALLY_FILLED, fill.time)
    return True


def request_cancel(order: Order, at: datetime) -> None:
    if order.is_terminal:
        return
    transition(order, S.CANCEL_REQUESTED, at)


def confirm_cancel(order: Order, at: datetime) -> Decimal:
    """Bestätigte Stornierung. Rückgabe: stornierte Restmenge (erst jetzt wird Reserviertes frei)."""
    remaining = order.remaining_qty
    transition(order, S.CANCELED, at)
    return remaining
