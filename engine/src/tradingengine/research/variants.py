"""Strategiefamilien und Parametervarianten für das Lernlabor.

Varianten entstehen ausschliesslich über das `params`-Argument der bestehenden Entscheidungsfunktionen – die
Strategiedateien werden nie verändert. Eine Variante mit anderen Parametern als die Basisversion bekommt eine
eigene, deterministische Versions-ID `<basis>+opt-<hash8>` (SHA-256 der kanonischen Parameter).

Parametergrenzen und freie Parameter nach docs/04, Abschnitt 2 (höchstens 3 freie Parameter):
S1: ATR-Faktoren (Stop, Trailing) 1.5–3, EMA 15–30 · S2: Donchian 15–40, Volumenfaktor 1.2–2 ·
S3: Bollinger-Breite 1.8–2.5, RSI-Schwelle 20–35.

Raster: geometrisch mit Faktor 1.2 um den Standardwert (ganzzahlige Parameter gerundet), nur innerhalb der
Grenzen. Dadurch sind die Rasternachbarn genau die «±20 %»-Nachbarn des Plateau-Tests.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from decimal import Decimal
from itertools import product
from typing import Any

from ..core.candles import Candle
from ..core.exit_plan import ExitPlan
from ..core.regime import RegimePoint
from ..core.signals import Action, Decision, StrategyVersion
from ..core.strategies import StrategyDef, s1_trend_pullback, s2_volume_breakout, s3_mean_reversion

GRID_RATIO = 1.2


@dataclass(frozen=True)
class Family:
    name: str
    base: StrategyVersion
    default: Any  # Parameter-Dataclass der Strategie
    decide: Callable[..., list[Decision]]
    exit_plan: Callable[..., ExitPlan]
    limits: dict[str, tuple[float, float]]
    free: tuple[str, ...]
    integer: frozenset[str]


FAMILIES: dict[str, Family] = {
    "s1-trend-pullback": Family(
        "s1-trend-pullback", s1_trend_pullback.VERSION, s1_trend_pullback.PARAMS, s1_trend_pullback.decide, s1_trend_pullback.exit_plan,
        {"stop_atr": (1.5, 3.0), "trail_atr": (1.5, 3.0), "ema_len": (15, 30)}, ("stop_atr", "ema_len", "trail_atr"), frozenset({"ema_len"}),
    ),
    "s2-volume-breakout": Family(
        "s2-volume-breakout", s2_volume_breakout.VERSION, s2_volume_breakout.PARAMS, s2_volume_breakout.decide, s2_volume_breakout.exit_plan,
        {"donchian_len": (15, 40), "volume_factor": (1.2, 2.0)}, ("donchian_len", "volume_factor"), frozenset({"donchian_len"}),
    ),
    "s3-mean-reversion": Family(
        "s3-mean-reversion", s3_mean_reversion.VERSION, s3_mean_reversion.PARAMS, s3_mean_reversion.decide, s3_mean_reversion.exit_plan,
        {"bb_width": (1.8, 2.5), "rsi_oversold": (20, 35)}, ("bb_width", "rsi_oversold"), frozenset(),
    ),
}


def family(name: str) -> Family:
    if name not in FAMILIES:
        raise ValueError(f"Unbekannte Strategiefamilie {name}")
    return FAMILIES[name]


def canonical(params: dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def full_params(fam: Family, overrides: dict[str, Any]) -> dict[str, Any]:
    names = {f.name for f in fields(fam.default)}
    unknown = set(overrides) - names
    if unknown:
        raise ValueError(f"Unbekannte Parameter: {', '.join(sorted(unknown))}")
    return asdict(replace(fam.default, **overrides))


def version_id(fam: Family, params: dict[str, Any]) -> str:
    if params == asdict(fam.default):
        return fam.base.id
    return f"{fam.base.id}+opt-{hashlib.sha256(canonical(params).encode()).hexdigest()[:8]}"


def version_of(fam: Family, params: dict[str, Any]) -> StrategyVersion:
    return StrategyVersion(id=version_id(fam, params), strategy=fam.name, version=fam.base.version, params=params,
                           regime_rule_version=fam.base.regime_rule_version)


def max_hold(params: dict[str, Any]) -> int:
    return int(params["max_hold_bars"])


def make_strategy(
    fam: Family,
    params: dict[str, Any],
    entry_window: tuple[datetime, datetime] | None = None,
    delay_bars: int = 0,
) -> StrategyDef:
    """StrategyDef für eine Variante. `entry_window` lässt BUY nur für Entscheidungskerzen mit Schluss im
    Fenster [von, bis) zu (sonst NO_TRADE); `delay_bars` verschiebt jede Entscheidung um n Kerzen nach hinten
    (Sensitivität «Einstieg eine Kerze verzögert»: gleiche Order, eine Kerze später erteilt)."""
    p = replace(fam.default, **params)
    version = version_of(fam, asdict(p))

    def decide(candles: list[Candle], own_regimes: list[RegimePoint], leader_regimes: list[RegimePoint], *, tick: Decimal | None = None) -> list[Decision]:
        out = fam.decide(candles, own_regimes, leader_regimes, p, tick)
        if delay_bars:
            shifted = [Decision(d.candle_close, Action.NO_TRADE, d.regime, ["Verzögerung"]) for d in out[:delay_bars]]
            for i in range(delay_bars, len(out)):
                src = out[i - delay_bars]
                shifted.append(replace(src, candle_close=out[i].candle_close) if src.action is Action.BUY else out[i])
            out = shifted
        if entry_window is not None:
            lo, hi = entry_window
            out = [d if d.action is not Action.BUY or lo <= d.candle_close < hi else
                   Decision(d.candle_close, Action.NO_TRADE, d.regime, ["Ausserhalb des Einstiegsfensters"]) for d in out]
        return out

    def exit_plan(decision: Decision) -> ExitPlan:
        return fam.exit_plan(decision, p)

    risk = float(getattr(p, "risk_factor", 1.0))
    return StrategyDef(version, decide, exit_plan, risk)


def _level(fam: Family, name: str, value: float) -> float | int:
    return int(round(value)) if name in fam.integer else round(value, 2)


def levels(fam: Family, name: str, k_range: range = range(-2, 3)) -> list[float | int]:
    lo, hi = fam.limits[name]
    center = float(getattr(fam.default, name))
    out: list[float | int] = []
    for k in k_range:
        v = _level(fam, name, center * GRID_RATIO**k)
        if lo <= v <= hi and v not in out:
            out.append(v)
    return sorted(out)


def neighbours(fam: Family, params: dict[str, Any], free: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """±20 %-Nachbarn je freiem Parameter (innerhalb der Grenzen), Schlüssel z. B. «stop_atr−20 %»."""
    out: dict[str, dict[str, Any]] = {}
    for name in free:
        lo, hi = fam.limits[name]
        for sign, factor in (("−", 1 / GRID_RATIO), ("+", GRID_RATIO)):
            v = _level(fam, name, float(params[name]) * factor)
            if lo <= v <= hi and v != params[name]:
                out[f"{name}{sign}20 %"] = {**params, name: v}
    return out


def grid(fam: Family, free: tuple[str, ...] | None = None, custom: dict[str, list[float]] | None = None) -> list[dict[str, Any]]:
    """Volles Raster (Standardvariante zuerst). Freie Parameter höchstens 3, Werte innerhalb der Grenzen."""
    free = tuple(free or fam.free)
    if len(free) > 3:
        raise ValueError("Höchstens 3 freie Parameter")
    axes: list[list[float | int]] = []
    for name in free:
        if name not in fam.limits:
            raise ValueError(f"{name} ist kein freier Parameter von {fam.name}")
        vals = custom.get(name) if custom else None
        if vals:
            lo, hi = fam.limits[name]
            if any(not (lo <= float(v) <= hi) for v in vals):
                raise ValueError(f"{name}: Werte ausserhalb der Grenzen {lo}–{hi}")
            axes.append(sorted({_level(fam, name, float(v)) for v in vals}))
        else:
            axes.append(levels(fam, name))
    base = asdict(fam.default)
    combos = [dict(zip(free, combo, strict=True)) for combo in product(*axes)]
    out = [full_params(fam, c) for c in combos]
    out.sort(key=lambda p: (p != base, canonical(p)))
    if base not in out:
        out.insert(0, base)
    return out
