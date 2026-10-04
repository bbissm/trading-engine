"""Signalbetrieb (Autonomiestufe 1): Regime, Features und Strategieentscheidungen auf abgeschlossenen
Kerzen. Es entstehen keine Orders.

Ins Signaljournal kommt nur die Entscheidung zur *zuletzt abgeschlossenen* Kerze, und nur solange sie
noch gültig ist. Ältere Kerzen werden nie nachträglich mit Signalen versehen – das Journal zeigt, was
der Bot damals tatsächlich entschieden hat, nicht was er rückblickend entschieden hätte.

Reproduzierbarkeit: Jede Berechnung nutzt ein festes Fenster der letzten LOOKBACK Kerzen bis zur
Entscheidungskerze. Ein späterer Replay mit derselben Regel liefert dasselbe Ergebnis, unabhängig
davon, wie viel ältere Historie inzwischen importiert wurde.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from ..core import indicators as ind
from ..core.candles import Candle, floor_time, timeframe_delta
from ..core.regime import REGIME_RULE_VERSION, RegimePoint, daily_regimes, regime_at
from ..core.signals import FeatureSnapshot, Signal
from ..core.strategies import ACTIVE, StrategyDef
from ..ports import Instrument, Store
from .marketdata import key_of

log = logging.getLogger(__name__)

# Gültigkeitsende eines Signals je (Instrument, Zeitebene, Kerzenschluss); None = Standard (Schluss + eine Periode).
# Für Aktien/ETFs: Schluss der nächsten Börsensitzung (services/stocks.py).
ValidUntil = Callable[[Instrument, str, datetime], datetime | None]

REGIME_TIMEFRAME = "1d"
LOOKBACK = 600


def _snapshots(instrument_id: str, timeframe: str, candles: list[Candle], regimes: list[RegimePoint]) -> list[FeatureSnapshot]:
    if timeframe == REGIME_TIMEFRAME:
        return [FeatureSnapshot(instrument_id, timeframe, p.close_time, REGIME_RULE_VERSION, p.regime, p.features) for p in regimes]
    close = [float(c.close) for c in candles]
    high = [float(c.high) for c in candles]
    low = [float(c.low) for c in candles]
    ema20 = ind.ema(close, 20)
    atr14 = ind.atr(high, low, close, 14)
    rsi14 = ind.rsi(close, 14)
    return [
        FeatureSnapshot(
            instrument_id, timeframe, c.close_time, REGIME_RULE_VERSION, regime_at(regimes, c.close_time),
            {"close": close[i], "ema20": ema20[i], "atr14": atr14[i], "rsi14": rsi14[i]},
        )
        for i, c in enumerate(candles)
    ]


def run_once(
    store: Store,
    timeframes: list[str],
    feed_status: dict[str, str],
    pending: set[str],
    data_source: str,
    now: datetime,
    valid_until_of: ValidUntil | None = None,
    strategies: list[StrategyDef] | None = None,
) -> tuple[int, set[str]]:
    """Verarbeitet die Schlüssel in `pending` ("instrument|timeframe" mit neuen Kerzen).

    Rückgabe: Anzahl neuer Signale (inkl. NO_TRADE) und die Schlüssel, die offen bleiben, weil ein Feed
    nicht in Ordnung ist oder die zugehörige Tageskerze noch fehlt. Sie werden im nächsten Durchlauf
    erneut versucht, bis die Kerze abgelaufen ist.
    """
    if not pending:
        return 0, set()
    active = ACTIVE if strategies is None else strategies
    for strategy in active:
        store.ensure_strategy_version(strategy.version)
    universe = store.universe()
    daily = {inst.id: store.load_candles(inst.id, REGIME_TIMEFRAME, LOOKBACK) for inst in universe}
    regimes = {inst_id: daily_regimes(candles) for inst_id, candles in daily.items()}

    created = 0
    still_pending: set[str] = set()
    for inst in universe:
        own = regimes[inst.id]
        leader_id = inst.leader_id or inst.id
        leader = regimes.get(leader_id, own)
        for timeframe in timeframes:
            key = key_of(inst.id, timeframe)
            if key not in pending:
                continue
            candles = daily[inst.id] if timeframe == REGIME_TIMEFRAME else store.load_candles(inst.id, timeframe, LOOKBACK)
            if not candles:
                continue
            known = store.latest_snapshot_close(inst.id, timeframe, REGIME_RULE_VERSION)
            snapshots = _snapshots(inst.id, timeframe, candles, own)
            store.insert_feature_snapshots([s for s in snapshots if known is None or s.candle_close > known])

            last = candles[-1]
            custom = valid_until_of(inst, timeframe, last.close_time) if valid_until_of is not None else None
            valid_until = custom if custom is not None else last.close_time + timeframe_delta(timeframe)
            if not (last.close_time <= now < valid_until):
                continue  # abgelaufen: verpasste Signale werden nicht nachgeholt

            # Sperre: Feed dieser Zeitebene und der Regime-Ebene (auch des Leitinstruments) muss in
            # Ordnung sein, und die Tageskerze, die bis zum Kerzenschluss fällig war, muss vorliegen.
            # Sonst hinge die Entscheidung davon ab, wann die Tageskerze eintrifft (nicht reproduzierbar).
            blockers = [key, key_of(inst.id, REGIME_TIMEFRAME), key_of(leader_id, REGIME_TIMEFRAME)]
            due = floor_time(last.close_time, REGIME_TIMEFRAME)
            regime_complete = all(pts and pts[-1].close_time >= due for pts in (own, leader))
            if any(feed_status.get(b) != "OK" for b in blockers) or not regime_complete:
                still_pending.add(key)
                continue

            for strategy in active:
                decision = strategy.decide(candles, own, leader, tick=inst.tick_size)[-1]
                signal = Signal(
                    instrument_id=inst.id,
                    timeframe=timeframe,
                    strategy_version_id=strategy.version.id,
                    decision=decision,
                    valid_until=valid_until,
                    data_source=data_source,
                    data_age_s=int((now - last.close_time).total_seconds()),
                    created_at=now,
                )
                if store.insert_signal(signal):
                    created += 1
                    log.info("Signal %s %s %s: %s", strategy.version.id, inst.id, timeframe, decision.action.value)
    return created, still_pending
