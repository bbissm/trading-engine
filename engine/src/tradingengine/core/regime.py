"""Marktregime auf Tageskerzen (docs/04, Abschnitt 1.3). Die Regel ist versioniert und wird nicht pro
Strategie optimiert. Stress hat Vorrang."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from . import indicators as ind
from .candles import Candle

REGIME_RULE_VERSION = "regime@1"

SMA_LONG = 200
SMA_MID = 50
SLOPE_LOOKBACK = 5
ADX_TREND = 20.0
VOL_WINDOW = 365
VOL_MIN_OBS = 120
VOL_STRESS_PCTL = 0.90
DROP_LOOKBACK = 5
DROP_ATR_MULT = 4.0


class Regime(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    SIDEWAYS = "SIDEWAYS"
    STRESS = "STRESS"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RegimePoint:
    close_time: datetime
    regime: Regime
    features: dict[str, float | None]


def daily_regimes(daily: list[Candle]) -> list[RegimePoint]:
    """Regime je Tageskerze. Kausal: Punkt i hängt nur von Kerzen ≤ i ab."""
    close = [float(c.close) for c in daily]
    high = [float(c.high) for c in daily]
    low = [float(c.low) for c in daily]

    sma_long = ind.sma(close, SMA_LONG)
    sma_mid = ind.sma(close, SMA_MID)
    adx14 = ind.adx(high, low, close, 14)
    atr14 = ind.atr(high, low, close, 14)
    vol20 = ind.realized_vol(close, 20)
    vol_pctl = ind.percentile_rank(vol20, VOL_WINDOW, VOL_MIN_OBS)

    out: list[RegimePoint] = []
    for i, candle in enumerate(daily):
        s_long, s_mid, adx_v, atr_v = sma_long[i], sma_mid[i], adx14[i], atr14[i]
        s_mid_prev = sma_mid[i - SLOPE_LOOKBACK] if i >= SLOPE_LOOKBACK else None
        pctl = vol_pctl[i]
        # Rückgang über DROP_LOOKBACK Tage, gemessen an der ATR *vor* dem Rückgang (sonst bläht der
        # Einbruch selbst den Massstab auf).
        atr_before = atr14[i - DROP_LOOKBACK] if i >= DROP_LOOKBACK else None
        drop = close[i - DROP_LOOKBACK] - close[i] if i >= DROP_LOOKBACK else None
        features: dict[str, float | None] = {
            "close": close[i],
            "sma200": s_long,
            "sma50": s_mid,
            "adx14": adx_v,
            "atr14": atr_v,
            "vol20": vol20[i],
            "vol_pctl": pctl,
            "drop5": drop,
        }

        if s_long is None or s_mid is None or s_mid_prev is None or adx_v is None or atr_v is None:
            regime = Regime.UNKNOWN
        elif (pctl is not None and pctl > VOL_STRESS_PCTL) or (
            drop is not None and atr_before is not None and drop > DROP_ATR_MULT * atr_before
        ):
            regime = Regime.STRESS
        elif close[i] > s_long and s_mid > s_mid_prev and adx_v >= ADX_TREND:
            regime = Regime.UP
        elif close[i] < s_long and s_mid < s_mid_prev and adx_v >= ADX_TREND:
            regime = Regime.DOWN
        else:
            regime = Regime.SIDEWAYS
        out.append(RegimePoint(candle.close_time, regime, features))
    return out


def regime_at(points: list[RegimePoint], t: datetime) -> Regime:
    """Regime der letzten Tageskerze, die bis `t` abgeschlossen war."""
    idx = bisect_right([p.close_time for p in points], t) - 1
    return points[idx].regime if idx >= 0 else Regime.UNKNOWN
