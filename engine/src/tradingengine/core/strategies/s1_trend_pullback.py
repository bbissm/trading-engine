"""S1 Trendfolge-Pullback (docs/04, Abschnitt 2). Long-only, Entscheidung nur auf abgeschlossenen Kerzen.

Forschungskandidat: Die Regeln sind festgelegt, ein Vorteil ist nicht belegt. Kosten, Spread und
Netto-Chance-Risiko werden erst mit dem Kostenmodell (Etappe 2) geprüft – das steht als Gegenfaktor
an jedem BUY-Signal.
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
class S1Params:
    ema_len: int = 20
    atr_len: int = 14
    rsi_len: int = 14
    stop_atr: float = 2.0
    trail_atr: float = 3.0
    pullback_lookback: int = 5
    max_hold_bars: int = 40
    volume_median_len: int = 20
    rsi_overbought: float = 70.0


PARAMS = S1Params()
VERSION = StrategyVersion(
    id="s1-trend-pullback@1",
    strategy="s1-trend-pullback",
    version=1,
    params=asdict(PARAMS),
    regime_rule_version=REGIME_RULE_VERSION,
)


def decide(
    candles: list[Candle],
    own_regimes: list[RegimePoint],
    leader_regimes: list[RegimePoint],
    params: S1Params = PARAMS,
    tick: Decimal | None = None,
) -> list[Decision]:
    """Eine Entscheidung je Kerze. Entscheidung i verwendet nur Kerzen ≤ i und Regime-Punkte,
    die bis zum Schluss von Kerze i abgeschlossen waren."""
    close = [float(c.close) for c in candles]
    high = [float(c.high) for c in candles]
    low = [float(c.low) for c in candles]
    volume = [float(c.volume) for c in candles]

    ema = ind.ema(close, params.ema_len)
    atr = ind.atr(high, low, close, params.atr_len)
    rsi = ind.rsi(close, params.rsi_len)
    vol_median = ind.rolling_median(volume, params.volume_median_len)

    # ADX der Tagesebene als Trendstärke für den Score
    daily_adx = {p.close_time: p.features.get("adx14") for p in own_regimes}
    daily_times = sorted(daily_adx)

    out: list[Decision] = []
    for i, candle in enumerate(candles):
        t = candle.close_time
        regime = regime_at(own_regimes, t)
        leader = regime_at(leader_regimes, t)
        ema_i, atr_i = ema[i], atr[i]

        if i < params.pullback_lookback or ema_i is None or atr_i is None:
            out.append(no_trade(t, regime, "Zu wenig Historie für EMA/ATR"))
            continue
        if regime is Regime.UNKNOWN:
            out.append(no_trade(t, regime, "Regime unbekannt (weniger als 200 Tageskerzen)"))
            continue
        if regime is not Regime.UP:
            out.append(no_trade(t, regime, f"Regime {regime.value}: S1 handelt nur im Aufwärtstrend"))
            continue
        if leader in (Regime.STRESS, Regime.UNKNOWN):
            out.append(no_trade(t, regime, f"Leitmarkt-Regime {leader.value}: keine Einstiege"))
            continue

        touched = False
        for j in range(i - params.pullback_lookback, i):
            ema_j = ema[j]
            if ema_j is not None and low[j] <= ema_j:
                touched = True
                break
        if not touched:
            out.append(no_trade(t, regime, f"Kein Rücksetzer an EMA{params.ema_len} in den letzten {params.pullback_lookback} Kerzen"))
            continue
        if not (close[i] > high[i - 1] and close[i] > ema_i):
            out.append(no_trade(t, regime, "Rücksetzer vorhanden, aber kein Schluss über Vorkerzenhoch und EMA"))
            continue

        triggers = [
            "Regime Aufwärtstrend (Schluss > SMA200, SMA50 steigend, ADX ≥ 20)",
            f"Rücksetzer an EMA{params.ema_len} innerhalb der letzten {params.pullback_lookback} Kerzen",
            "Schluss über Vorkerzenhoch und über EMA",
        ]
        counter = [COST_NOTE]
        score = 40.0
        adx_day = None
        past = [d for d in daily_times if d <= t]
        if past:
            adx_day = daily_adx[past[-1]]
        if adx_day is not None:
            score += min(adx_day, 40.0)
        rsi_i = rsi[i]
        if rsi_i is not None and rsi_i > params.rsi_overbought:
            counter.append(f"RSI({params.rsi_len}) {rsi_i:.0f} über {params.rsi_overbought:.0f}")
            score -= 15
        med = vol_median[i]
        if med is not None and volume[i] >= med:
            triggers.append("Volumen ≥ 20er-Median")
            score += 10
        else:
            counter.append("Volumen unter 20er-Median")
        if leader is not Regime.UP:
            counter.append(f"Leitmarkt-Regime {leader.value}")

        out.append(
            Decision(
                candle_close=t,
                action=Action.BUY,
                regime=regime,
                triggers=triggers,
                counter=counter,
                score=clamp_score(score),
                entry=candle.close,
                stop=to_tick(close[i] - params.stop_atr * atr_i, tick),
                target=None,  # Ausstieg über Trailing-Stop (trail_atr), kein festes Ziel
                max_hold_bars=params.max_hold_bars,
            )
        )
    return out


def exit_plan(decision: Decision, params: S1Params = PARAMS) -> ExitPlan:
    """Trailing-Stop vom höchsten Schluss; Ausstieg, sobald das Regime nicht mehr Aufwärtstrend ist."""
    assert decision.stop is not None
    return ExitPlan(
        stop=decision.stop,
        max_hold_bars=params.max_hold_bars,
        trail_atr=params.trail_atr,
        exit_unless_regime=frozenset({Regime.UP}),
    )
