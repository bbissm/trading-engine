"""S3 Mean Reversion für Seitwärtsphasen (docs/04, Abschnitt 2). Long-only, nur abgeschlossene Kerzen.

Forschungskandidat ohne Qualitätsnachweis. Vorgesehen mit halbem Risiko pro Trade (risk_factor);
die Positionsgrösse entsteht erst mit Risikoprüfung und Simulator.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal

from .. import indicators as ind
from ..candles import Candle
from ..regime import REGIME_RULE_VERSION, Regime, RegimePoint, regime_at
from ..signals import Action, Decision, StrategyVersion
from .common import COST_NOTE, clamp_score, no_trade, to_tick


@dataclass(frozen=True, slots=True)
class S3Params:
    bb_len: int = 20
    bb_width: float = 2.0
    rsi_len: int = 14
    rsi_oversold: float = 30.0
    atr_len: int = 14
    stop_atr: float = 1.5
    max_hold_bars: int = 10
    risk_factor: float = 0.5
    min_gross_reward_risk: float = 1.0


PARAMS = S3Params()
VERSION = StrategyVersion(
    id="s3-mean-reversion@1",
    strategy="s3-mean-reversion",
    version=1,
    params=asdict(PARAMS),
    regime_rule_version=REGIME_RULE_VERSION,
)


def decide(
    candles: list[Candle],
    own_regimes: list[RegimePoint],
    leader_regimes: list[RegimePoint],
    params: S3Params = PARAMS,
    tick: Decimal | None = None,
) -> list[Decision]:
    """Eine Entscheidung je Kerze; Entscheidung i verwendet nur Kerzen ≤ i."""
    close = [float(c.close) for c in candles]
    high = [float(c.high) for c in candles]
    low = [float(c.low) for c in candles]
    mid = ind.sma(close, params.bb_len)
    std = ind.rolling_std(close, params.bb_len)
    rsi = ind.rsi(close, params.rsi_len)
    atr = ind.atr(high, low, close, params.atr_len)

    out: list[Decision] = []
    for i, candle in enumerate(candles):
        t = candle.close_time
        regime = regime_at(own_regimes, t)
        leader = regime_at(leader_regimes, t)
        mid_i, atr_i = mid[i], atr[i]
        mid_p, std_p, rsi_p = (mid[i - 1], std[i - 1], rsi[i - 1]) if i >= 1 else (None, None, None)

        if mid_i is None or atr_i is None or mid_p is None or std_p is None or rsi_p is None:
            out.append(no_trade(t, regime, "Zu wenig Historie für Bollinger/RSI/ATR"))
            continue
        if regime is Regime.UNKNOWN:
            out.append(no_trade(t, regime, "Regime unbekannt (weniger als 200 Tageskerzen)"))
            continue
        if regime is not Regime.SIDEWAYS:
            out.append(no_trade(t, regime, f"Regime {regime.value}: S3 handelt nur in Seitwärtsphasen"))
            continue
        if leader in (Regime.DOWN, Regime.STRESS, Regime.UNKNOWN):
            out.append(no_trade(t, regime, f"Leitmarkt-Regime {leader.value}: keine Einstiege"))
            continue
        lower_p = mid_p - params.bb_width * std_p
        if not (close[i - 1] < lower_p and rsi_p < params.rsi_oversold):
            out.append(no_trade(t, regime, "Vorkerze nicht überverkauft (Schluss unter unterem Band und RSI unter Schwelle)"))
            continue
        if close[i] <= close[i - 1]:
            out.append(no_trade(t, regime, "Überverkauft, aber noch kein höherer Schluss"))
            continue

        stop = close[i] - params.stop_atr * atr_i
        reward, risk = mid_i - close[i], close[i] - stop
        if reward <= 0 or risk <= 0 or reward / risk < params.min_gross_reward_risk:
            out.append(no_trade(t, regime, "Abstand zum Mittelband zu klein (Brutto-Chance-Risiko unter 1.0)"))
            continue

        score = 40.0 + min(reward / risk * 10.0, 30.0) + min(params.rsi_oversold - rsi_p, 15.0)
        out.append(
            Decision(
                candle_close=t,
                action=Action.BUY,
                regime=regime,
                triggers=[
                    "Regime Seitwärts (ADX < 20)",
                    f"Vorkerze unter unterem Bollinger-Band ({params.bb_len}, {params.bb_width:g}) mit RSI {rsi_p:.0f}",
                    "Erster höherer Schluss danach",
                ],
                counter=[COST_NOTE, "Halbes Risiko pro Trade vorgesehen"],
                score=clamp_score(score),
                entry=candle.close,
                stop=to_tick(stop, tick),
                target=to_tick(mid_i, tick),
                max_hold_bars=params.max_hold_bars,
            )
        )
    return out
