from __future__ import annotations

from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from ..regime import Regime
from ..signals import Action, Decision

COST_NOTE = "Kosten, Spread und Netto-Chance-Risiko noch nicht geprüft (Kostenmodell folgt in Etappe 2)"


def to_tick(x: float, tick: Decimal | None) -> Decimal:
    """Preis auf ein Vielfaches der Tick-Grösse des Instruments abgerundet (ohne Tick: 8 Nachkommastellen)."""
    value = Decimal(str(x))
    if tick is None or tick <= 0:
        return value.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    return (value / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def no_trade(t: datetime, regime: Regime, reason: str) -> Decision:
    return Decision(candle_close=t, action=Action.NO_TRADE, regime=regime, triggers=[reason])


def clamp_score(score: float) -> int:
    return max(0, min(100, round(score)))
