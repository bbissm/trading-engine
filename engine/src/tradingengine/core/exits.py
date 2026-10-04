"""Exit-Plan und Positionsbetreuung je Trade (docs/04, Abschnitt 2).

Der Plan gehört der Strategieversion, die den Trade eröffnet hat, und bleibt bis zum Schluss gültig.
Der Stop wird nach dem Einstieg nie weiter entfernt – nur gleich oder enger.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .candles import Candle
from .exit_plan import ExitPlan
from .regime import Regime
from .strategies.common import to_tick


@dataclass(slots=True)
class ManagedTrade:
    plan: ExitPlan
    stop: Decimal
    highest_close: Decimal
    bars_held: int = 0


def start(plan: ExitPlan, entry_price: Decimal) -> ManagedTrade:
    return ManagedTrade(plan=plan, stop=plan.stop, highest_close=entry_price)


def on_close(trade: ManagedTrade, candle: Candle, atr: float | None, regime: Regime, tick: Decimal | None) -> str | None:
    """Nach jedem Kerzenschluss mit offener Position: Stop nachziehen, strategische Ausstiege prüfen.

    Rückgabe: Ausstiegsgrund (Market-Exit zur nächsten Eröffnung) oder None."""
    plan = trade.plan
    trade.bars_held += 1
    trade.highest_close = max(trade.highest_close, candle.close)

    if plan.trail_atr is not None and atr is not None:
        candidate = to_tick(float(trade.highest_close) - plan.trail_atr * atr, tick)
        if candidate > trade.stop:  # nur enger, nie weiter
            trade.stop = candidate

    if plan.exit_unless_regime is not None and regime not in plan.exit_unless_regime:
        return f"Regimewechsel auf {regime.value}"
    if plan.breakout_level is not None and trade.bars_held <= plan.failed_breakout_bars and candle.close < plan.breakout_level:
        return "Gescheiterter Ausbruch (Schluss zurück unter das Ausbruchsniveau)"
    if trade.bars_held >= plan.max_hold_bars:
        return f"Maximale Haltedauer von {plan.max_hold_bars} Kerzen erreicht"
    return None
