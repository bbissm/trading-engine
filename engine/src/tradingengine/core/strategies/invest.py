"""Invest-Topf (docs/11, E-12): längere Haltedauer auf Tageskerzen, Gewichtung statt Risikobudget.

- i1 Kern: BTC und ETH, solange der Kurs über dem 200-Tage-Durchschnitt liegt; Ausstieg erst im Abwärtstrend
  oder über einen weiten, nachgezogenen Stopp.
- i2 Satellit (nur Kursregeln): einmal pro Woche (Wochenschluss Sonntag → Montag 00:00 UTC) Coins ausser BTC/ETH im
  Aufwärtstrend, die über 4 und 12 Wochen stärker als BTC sind und deren Handelsvolumen steigt.
- i3 Satellit (Kurs + Nachrichten): wie i2 die relative Stärke über 4 Wochen, statt Volumen aber positive,
  glaubwürdige Coin-Nachrichten der letzten 14 Tage (Claude, core/news.py) und kein starker Gegenwind.
- h1 Halten: BTC und ETH kaufen und halten – Vergleichsmassstab, nie ein Ausstieg ausser bei Totalverlust.

Forschungskandidaten ohne Qualitätsnachweis. Alle Entscheidungen nur auf abgeschlossenen Tageskerzen; Nachrichten
zählen nur, wenn ihre Einordnung vor dem Kerzenschluss gespeichert war.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal

from .. import indicators as ind
from ..candles import Candle
from ..exit_plan import ExitPlan
from ..news import NewsPoint, news_score
from ..regime import REGIME_RULE_VERSION, Regime, RegimePoint, regime_at
from ..signals import Action, Decision, StrategyVersion
from .common import clamp_score, no_trade, to_tick

CORE = frozenset({"KRAKEN:BTC/USD", "KRAKEN:ETH/USD"})
ALL_REGIMES_BUT_DOWN = frozenset({Regime.UP, Regime.SIDEWAYS, Regime.STRESS, Regime.UNKNOWN})
INVEST_NOTE = "Investieren über Wochen bis Monate: grosse Schwankungen bis zum weiten Stopp sind eingeplant"


def _is_crypto(c: Candle) -> bool:
    return c.instrument_id.startswith("KRAKEN:")


def _buy(t, regime, triggers, counter, score, entry: Decimal, stop: float, max_hold: int, tick) -> Decision:  # type: ignore[no-untyped-def]
    return Decision(
        candle_close=t, action=Action.BUY, regime=regime, triggers=triggers, counter=counter, score=clamp_score(score),
        entry=entry, stop=to_tick(stop, tick), target=None, max_hold_bars=max_hold,
    )


def _leader_closes(leader: list[RegimePoint]) -> list[tuple[object, float]]:
    return [(p.close_time, float(p.features["close"])) for p in leader if p.features.get("close") is not None]  # type: ignore[arg-type]


def _ret(closes: list[float], i: int, days: int) -> float | None:
    return closes[i] / closes[i - days] - 1 if i >= days and closes[i - days] > 0 else None


def _leader_ret(leader: list[tuple[object, float]], t: object, days: int) -> float | None:
    past = [c for at, c in leader if at <= t]  # type: ignore[operator]
    return past[-1] / past[-1 - days] - 1 if len(past) > days and past[-1 - days] > 0 else None


# ───────── i1 Kern ─────────


@dataclass(frozen=True, slots=True)
class CoreParams:
    sma_len: int = 200
    atr_len: int = 14
    stop_atr: float = 8.0
    trail_atr: float = 10.0
    max_hold_bars: int = 3650
    weight: float = 0.25


CORE_PARAMS = CoreParams()
CORE_VERSION = StrategyVersion("i1-core-trend@1", "i1-core-trend", 1, asdict(CORE_PARAMS), REGIME_RULE_VERSION)


def core_decide(candles: list[Candle], own_regimes: list[RegimePoint], leader_regimes: list[RegimePoint], params: CoreParams = CORE_PARAMS,
                tick: Decimal | None = None) -> list[Decision]:
    close = [float(c.close) for c in candles]
    sma = ind.sma(close, params.sma_len)
    atr = ind.atr([float(c.high) for c in candles], [float(c.low) for c in candles], close, params.atr_len)
    out: list[Decision] = []
    for i, c in enumerate(candles):
        t, regime = c.close_time, regime_at(own_regimes, c.close_time)
        if c.instrument_id not in CORE:
            out.append(no_trade(t, regime, "Kern: nur BTC und ETH"))
        elif c.timeframe != "1d":
            out.append(no_trade(t, regime, "Invest: nur Tageskerzen"))
        elif sma[i] is None or atr[i] is None:
            out.append(no_trade(t, regime, f"Zu wenig Historie für SMA{params.sma_len}"))
        elif close[i] <= sma[i]:  # type: ignore[operator]
            out.append(no_trade(t, regime, f"Schluss unter SMA{params.sma_len}: kein langfristiger Aufwärtstrend"))
        elif regime is Regime.DOWN:
            out.append(no_trade(t, regime, "Regime Abwärtstrend"))
        else:
            dist = close[i] / sma[i] - 1  # type: ignore[operator]
            out.append(_buy(t, regime, [f"Schluss {dist:.0%} über SMA{params.sma_len}", f"Regime {regime.value}"], [INVEST_NOTE],
                            60 + min(dist * 100, 30), c.close, close[i] - params.stop_atr * atr[i], params.max_hold_bars, tick))  # type: ignore[operator]
    return out


def core_exit_plan(decision: Decision, params: CoreParams = CORE_PARAMS) -> ExitPlan:
    assert decision.stop is not None
    return ExitPlan(stop=decision.stop, max_hold_bars=params.max_hold_bars, trail_atr=params.trail_atr, exit_unless_regime=ALL_REGIMES_BUT_DOWN)


# ───────── i2 / i3 Satelliten ─────────


@dataclass(frozen=True, slots=True)
class SatelliteParams:
    rs_short_days: int = 28
    rs_long_days: int = 84
    volume_recent: int = 20
    volume_base: int = 60
    volume_factor: float = 1.2
    atr_len: int = 14
    stop_atr: float = 6.0
    trail_atr: float = 8.0
    max_hold_bars: int = 84
    weight: float = 0.10
    news_days: int = 14
    min_news_score: float = 1.0
    max_negative_news: float = -0.5


SAT_PARAMS = SatelliteParams()
SAT_VERSION = StrategyVersion("i2-satellite-strength@1", "i2-satellite-strength", 1, asdict(SAT_PARAMS), REGIME_RULE_VERSION)
NEWS_VERSION = StrategyVersion("i3-satellite-news@1", "i3-satellite-news", 1, asdict(SAT_PARAMS), REGIME_RULE_VERSION)


def _satellite(candles: list[Candle], own_regimes: list[RegimePoint], leader_regimes: list[RegimePoint], params: SatelliteParams,
               tick: Decimal | None, news: Sequence[NewsPoint] | None) -> list[Decision]:
    close = [float(c.close) for c in candles]
    volume = [float(c.volume) for c in candles]
    atr = ind.atr([float(c.high) for c in candles], [float(c.low) for c in candles], close, params.atr_len)
    leader = _leader_closes(leader_regimes)
    out: list[Decision] = []
    for i, c in enumerate(candles):
        t, regime = c.close_time, regime_at(own_regimes, c.close_time)
        if not _is_crypto(c) or c.instrument_id in CORE:
            out.append(no_trade(t, regime, "Satellit: Crypto ausser BTC/ETH"))
            continue
        if c.timeframe != "1d" or t.weekday() != 0 or t.hour != 0:
            out.append(no_trade(t, regime, "Satellit: Entscheid nur zum Wochenschluss"))
            continue
        lead_regime = regime_at(leader_regimes, t)
        if regime is not Regime.UP and not (news is not None and regime is Regime.SIDEWAYS):
            out.append(no_trade(t, regime, f"Regime {regime.value}: Satellit braucht Aufwärtstrend"))
            continue
        if lead_regime in (Regime.STRESS, Regime.DOWN, Regime.UNKNOWN):
            out.append(no_trade(t, regime, f"Leitmarkt BTC im Regime {lead_regime.value}"))
            continue
        own_s, btc_s = _ret(close, i, params.rs_short_days), _leader_ret(leader, t, params.rs_short_days)
        if own_s is None or btc_s is None or atr[i] is None:
            out.append(no_trade(t, regime, "Zu wenig Historie für die relative Stärke"))
            continue
        if own_s <= btc_s:
            out.append(no_trade(t, regime, f"4 Wochen nicht stärker als BTC ({own_s:+.0%} gegenüber {btc_s:+.0%})"))
            continue
        triggers = [f"4 Wochen {own_s:+.0%} gegenüber BTC {btc_s:+.0%}"]
        counter = [INVEST_NOTE]
        score = 50 + min((own_s - btc_s) * 100, 25)
        if news is None:
            own_l, btc_l = _ret(close, i, params.rs_long_days), _leader_ret(leader, t, params.rs_long_days)
            if own_l is None or btc_l is None or own_l <= btc_l:
                out.append(no_trade(t, regime, "12 Wochen nicht stärker als BTC"))
                continue
            if i < params.volume_base + params.volume_recent:
                out.append(no_trade(t, regime, "Zu wenig Historie für den Volumenvergleich"))
                continue
            recent = sum(volume[i - params.volume_recent + 1 : i + 1]) / params.volume_recent
            base = sum(volume[i - params.volume_recent - params.volume_base + 1 : i - params.volume_recent + 1]) / params.volume_base
            if base <= 0 or recent < params.volume_factor * base:
                out.append(no_trade(t, regime, f"Volumen steigt nicht genug ({recent / base if base else 0:.1f}× statt {params.volume_factor:.1f}×)"))
                continue
            triggers += [f"12 Wochen {own_l:+.0%} gegenüber BTC {btc_l:+.0%}", f"Volumen {recent / base:.1f}× des Vorquartals"]
            score += min((recent / base - 1) * 20, 15)
        else:
            s = news_score(news, t, params.news_days)
            neg = news_score([p for p in news if p.direction < 0], t, params.news_days)
            if s < params.min_news_score:
                out.append(no_trade(t, regime, f"Nachrichten-Rückenwind {s:.1f} unter {params.min_news_score:.1f}"))
                continue
            if neg <= params.max_negative_news:
                out.append(no_trade(t, regime, f"Negative Nachrichten {neg:.1f}"))
                continue
            triggers.append(f"Nachrichten-Rückenwind {s:.1f} (14 Tage, eingeordnet von Claude)")
            score += min(s * 5, 20)
        out.append(_buy(t, regime, triggers, counter, score, c.close, close[i] - params.stop_atr * atr[i], params.max_hold_bars, tick))  # type: ignore[operator]
    return out


def satellite_decide(candles: list[Candle], own_regimes: list[RegimePoint], leader_regimes: list[RegimePoint], params: SatelliteParams = SAT_PARAMS,
                     tick: Decimal | None = None) -> list[Decision]:
    return _satellite(candles, own_regimes, leader_regimes, params, tick, None)


def news_satellite_decide(candles: list[Candle], own_regimes: list[RegimePoint], leader_regimes: list[RegimePoint], news: Sequence[NewsPoint],
                          params: SatelliteParams = SAT_PARAMS, tick: Decimal | None = None) -> list[Decision]:
    return _satellite(candles, own_regimes, leader_regimes, params, tick, news)


def satellite_exit_plan(decision: Decision, params: SatelliteParams = SAT_PARAMS) -> ExitPlan:
    assert decision.stop is not None
    return ExitPlan(stop=decision.stop, max_hold_bars=params.max_hold_bars, trail_atr=params.trail_atr,
                    exit_unless_regime=frozenset({Regime.UP, Regime.SIDEWAYS}))


# ───────── h1 Halten ─────────


@dataclass(frozen=True, slots=True)
class HoldParams:
    weight: float = 0.45
    stop_fraction: float = 0.05  # Stopp bei 5 % des Kaufkurses: praktisch nie, nur gegen Totalverlust
    max_hold_bars: int = 36500


HOLD_PARAMS = HoldParams()
HOLD_VERSION = StrategyVersion("h1-hold@1", "h1-hold", 1, asdict(HOLD_PARAMS), REGIME_RULE_VERSION)


def hold_decide(candles: list[Candle], own_regimes: list[RegimePoint], leader_regimes: list[RegimePoint], params: HoldParams = HOLD_PARAMS,
                tick: Decimal | None = None) -> list[Decision]:
    out: list[Decision] = []
    for c in candles:
        t, regime = c.close_time, regime_at(own_regimes, c.close_time)
        if c.instrument_id not in CORE or c.timeframe != "1d":
            out.append(no_trade(t, regime, "Halten: nur BTC und ETH, Tageskerzen"))
        else:
            out.append(_buy(t, regime, ["Kaufen und halten (Vergleichsmassstab)"], [], 50, c.close, float(c.close) * params.stop_fraction,
                            params.max_hold_bars, tick))
    return out


def hold_exit_plan(decision: Decision, params: HoldParams = HOLD_PARAMS) -> ExitPlan:
    assert decision.stop is not None
    return ExitPlan(stop=decision.stop, max_hold_bars=params.max_hold_bars)
