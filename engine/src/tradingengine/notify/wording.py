"""Wortlaut-Vorlagen (docs/06, 4.2): «vorbereitet», «gesendet» und «ausgeführt» werden strikt unterschieden.

Paper-Ausführungen tragen immer den Zusatz «(simuliert)». Keine Erfolgs- oder Gewinnbehauptungen.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

ZURICH = ZoneInfo("Europe/Zurich")


def _num(q: Decimal) -> str:
    return format(q.normalize(), "f")


def order_prepared(qty: Decimal, instrument: str, until: datetime) -> str:
    """Stufe 2: Order wartet auf Freigabe; ohne Antwort verfällt sie (Schweigen ist keine Erlaubnis)."""
    return f"Order vorbereitet – {_num(qty)} {instrument}, wartet auf deine Freigabe bis {until.astimezone(ZURICH):%H:%M}"


def order_sent(qty: Decimal, instrument: str, simulated: bool) -> str:
    return f"Order gesendet: {_num(qty)} {instrument}" + (" (simuliert)" if simulated else "")


def order_filled(qty: Decimal, instrument: str, price: Decimal, simulated: bool) -> str:
    return f"Ausgeführt: {_num(qty)} {instrument} zu {_num(price)}" + (" (simuliert)" if simulated else "")
