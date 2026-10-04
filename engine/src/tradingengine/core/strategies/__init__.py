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


# Strategiefamilien: Modul mit decide/exit_plan und Parametertyp. Lab-Versionen (z. B. «…@1+opt-…») sind dieselbe
# Regel mit anderen, unveränderlichen Parametern.
_FAMILIES = {
    "s1-trend-pullback": (s1_trend_pullback, s1_trend_pullback.S1Params),
    "s2-volume-breakout": (s2_volume_breakout, s2_volume_breakout.S2Params),
    "s3-mean-reversion": (s3_mean_reversion, s3_mean_reversion.S3Params),
}


def from_version(version: StrategyVersion) -> StrategyDef | None:
    """StrategyDef für eine gespeicherte Version; None, wenn Familie oder Parameter unbekannt sind."""
    family = _FAMILIES.get(version.strategy)
    if family is None:
        return None
    module, params_cls = family
    try:
        params = params_cls(**version.params)
    except TypeError:
        return None

    def decide(candles: list[Candle], own_regimes: list[RegimePoint], leader_regimes: list[RegimePoint], *, tick: Decimal | None = None) -> list[Decision]:
        return module.decide(candles, own_regimes, leader_regimes, params, tick=tick)  # type: ignore[no-any-return]

    def exit_plan(decision: Decision) -> ExitPlan:
        return module.exit_plan(decision, params)  # type: ignore[no-any-return]

    return StrategyDef(version, decide, exit_plan, float(getattr(params, "risk_factor", 1.0)))
