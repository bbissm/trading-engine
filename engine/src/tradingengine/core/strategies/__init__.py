"""Startstrategien (Forschungskandidaten, kein Qualitätsnachweis)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from ..candles import Candle
from ..exit_plan import ExitPlan
from ..regime import RegimePoint
from ..signals import Decision, StrategyVersion
from . import s1_trend_pullback, s2_volume_breakout, s3_mean_reversion


class DecideFn(Protocol):
    def __call__(
        self, candles: list[Candle], own_regimes: list[RegimePoint], leader_regimes: list[RegimePoint], *, tick: Decimal | None = None
    ) -> list[Decision]: ...


class ExitPlanFn(Protocol):
    def __call__(self, decision: Decision) -> ExitPlan: ...


@dataclass(frozen=True, slots=True)
class StrategyDef:
    version: StrategyVersion
    decide: DecideFn
    exit_plan: ExitPlanFn
    risk_factor: float = 1.0


# Reihenfolge = dokumentierte Strategiepriorität (docs/01, 3.8)
ACTIVE: list[StrategyDef] = [
    StrategyDef(s1_trend_pullback.VERSION, s1_trend_pullback.decide, s1_trend_pullback.exit_plan),
    StrategyDef(s2_volume_breakout.VERSION, s2_volume_breakout.decide, s2_volume_breakout.exit_plan),
    StrategyDef(s3_mean_reversion.VERSION, s3_mean_reversion.decide, s3_mean_reversion.exit_plan, s3_mean_reversion.PARAMS.risk_factor),
]
