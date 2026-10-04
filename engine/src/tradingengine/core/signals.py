from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .regime import Regime


class Action(StrEnum):
    BUY = "BUY"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True, slots=True)
class StrategyVersion:
    """Unveränderlich: jede Änderung an Regeln oder Parametern ist eine neue Version."""

    id: str
    strategy: str
    version: int
    params: dict[str, Any]
    regime_rule_version: str


@dataclass(frozen=True, slots=True)
class Decision:
    """Strategieausgabe für genau eine abgeschlossene Kerze (auch NO_TRADE wird festgehalten)."""

    candle_close: datetime
    action: Action
    regime: Regime
    triggers: list[str]
    counter: list[str] = field(default_factory=list)
    score: int | None = None
    entry: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    max_hold_bars: int | None = None
    ref_level: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Signal:
    """Gespeichertes Signal. Wird nie nachträglich verändert."""

    instrument_id: str
    timeframe: str
    strategy_version_id: str
    decision: Decision
    valid_until: datetime
    data_source: str
    data_age_s: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    instrument_id: str
    timeframe: str
    candle_close: datetime
    regime_rule_version: str
    regime: Regime
    features: dict[str, float | None]
