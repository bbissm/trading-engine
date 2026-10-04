"""Exit-Plan eines Trades (Datenobjekt)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .regime import Regime


@dataclass(frozen=True, slots=True)
class ExitPlan:
    stop: Decimal
    max_hold_bars: int
    trail_atr: float | None = None
    target: Decimal | None = None
    exit_unless_regime: frozenset[Regime] | None = None  # Ausstieg, sobald das Regime nicht mehr dazugehört
    breakout_level: Decimal | None = None
    failed_breakout_bars: int = 0
