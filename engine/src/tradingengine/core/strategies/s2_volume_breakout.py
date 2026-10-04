"""S2 Volumenbestätigter Ausbruch (docs/04, Abschnitt 2). Long-only, nur abgeschlossene Kerzen.

Forschungskandidat ohne Qualitätsnachweis. Der Ausstieg bei gescheitertem Ausbruch (Schluss zurück unter
das Ausbruchsniveau innert 3 Kerzen) gehört zur Positionsbetreuung und folgt mit dem Simulator.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal

from .. import indicators as ind
from ..candles import Candle
from ..exit_plan import ExitPlan
from ..regime import REGIME_RULE_VERSION, Regime, RegimePoint, regime_at
from ..signals import Action, Decision, StrategyVersion
from .common import COST_NOTE, clamp_score, no_trade, to_tick


@dataclass(frozen=True, slots=True)
class S2Params:
    donchian_len: int = 20
    volume_median_len: int = 20
    volume_factor: float = 1.5
    atr_len: int = 14
    stop_atr: float = 1.0
    trail_atr: float = 2.5
    failed_breakout_bars: int = 3
    max_hold_bars: int = 30


PARAMS = S2Params()
VERSION = StrategyVersion(
    id="s2-volume-breakout@1",
    strategy="s2-volume-breakout",
    version=1,
    params=asdict(PARAMS),
    regime_rule_version=REGIME_RULE_VERSION,
)


def decide(
    candles: list[Candle],
    own_regimes: list[RegimePoint],
    leader_regimes: list[RegimePoint],
    params: S2Params = PARAMS,
    tick: Decimal | None = None,
) -> list[Decision]:
    """Eine Entscheidung je Kerze; Entscheidung i verwendet nur Kerzen ≤ i."""
    close = [float(c.close) for c in candles]
    high = [float(c.high) for c in candles]
    low = [float(c.low) for c in candles]
    volume = [float(c.volume) for c in candles]
    atr = ind.atr(high, low, close, params.atr_len)
    donchian = ind.rolling_max(high, params.donchian_len)
    vol_median = ind.rolling_median(volume, params.volume_median_len)

    out: list[Decision] = []
    for i, candle in enumerate(candles):
        t = candle.close_time
        regime = regime_at(own_regimes, t)
        leader = regime_at(leader_regimes, t)
        atr_i = atr[i]
        # Ausbruchsniveau und Volumen-Median aus den Kerzen *vor* der aktuellen
        level = donchian[i - 1] if i >= 1 else None
        prev_level = donchian[i - 2] if i >= 2 else None
        median = vol_median[i - 1] if i >= 1 else None

        if atr_i is None or level is None or prev_level is None or median is None:
            out.append(no_trade(t, regime, "Zu wenig Historie für Donchian/ATR"))
            continue
        if regime is Regime.UNKNOWN:
            out.append(no_trade(t, regime, "Regime unbekannt (weniger als 200 Tageskerzen)"))
            continue
        if regime not in (Regime.UP, Regime.SIDEWAYS):
            out.append(no_trade(t, regime, f"Regime {regime.value}: S2 handelt nur im Aufwärtstrend oder aus der Seitwärtsphase"))
            continue
        if leader in (Regime.STRESS, Regime.UNKNOWN):
            out.append(no_trade(t, regime, f"Leitmarkt-Regime {leader.value}: keine Einstiege"))
            continue
        if close[i] <= level:
            out.append(no_trade(t, regime, f"Kein Schluss über dem {params.donchian_len}-Kerzen-Hoch"))
            continue
        if close[i - 1] > prev_level:
            out.append(no_trade(t, regime, "Ausbruch bereits in der Vorkerze erfolgt"))
            continue
        ratio = volume[i] / median if median > 0 else 0.0
        if ratio < params.volume_factor:
            out.append(no_trade(t, regime, f"Ausbruch ohne Volumenbestätigung ({ratio:.1f}× Median, nötig {params.volume_factor:.1f}×)"))
            continue

        counter = [COST_NOTE]
        score = 45.0 + min((ratio - params.volume_factor) * 20.0, 25.0)
        if regime is Regime.UP:
            score += 15
        else:
            counter.append("Ausbruch aus Seitwärtsphase: übergeordneter Trend nicht bestätigt")
        if leader is Regime.UP:
            score += 10
        else:
            counter.append(f"Leitmarkt-Regime {leader.value}")
        extension = (close[i] - level) / atr_i
        if extension > 1.5:
            counter.append(f"Schluss bereits {extension:.1f} ATR über dem Ausbruchsniveau")
            score -= 15

        out.append(
            Decision(
                candle_close=t,
                action=Action.BUY,
                regime=regime,
                triggers=[
                    f"Schluss über dem {params.donchian_len}-Kerzen-Hoch ({to_tick(level, tick)})",
                    f"Volumen {ratio:.1f}× 20er-Median",
                    f"Regime {regime.value}",
                ],
                counter=counter,
                score=clamp_score(score),
                entry=candle.close,
                stop=to_tick(level - params.stop_atr * atr_i, tick),
                target=None,  # Ausstieg über Trailing-Stop (trail_atr)
                max_hold_bars=params.max_hold_bars,
                ref_level=to_tick(level, tick),
            )
        )
    return out


def exit_plan(decision: Decision, params: S2Params = PARAMS) -> ExitPlan:
    """Trailing-Stop; Ausstieg bei Schluss zurück unter das Ausbruchsniveau in den ersten Kerzen."""
    assert decision.stop is not None
    return ExitPlan(
        stop=decision.stop,
        max_hold_bars=params.max_hold_bars,
        trail_atr=params.trail_atr,
        breakout_level=decision.ref_level,
        failed_breakout_bars=params.failed_breakout_bars,
    )
