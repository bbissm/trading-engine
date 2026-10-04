import random
from decimal import Decimal

import pytest
from helpers import make_candles, random_walk

from tradingengine.core import indicators as ind
from tradingengine.core.candles import Candle
from tradingengine.core.regime import Regime, RegimePoint, daily_regimes
from tradingengine.core.signals import Action
from tradingengine.core.strategies import ACTIVE
from tradingengine.core.strategies import s2_volume_breakout as s2
from tradingengine.core.strategies import s3_mean_reversion as s3

TICK = Decimal("0.01")


def _fixed(candles: list[Candle], regime: Regime) -> list[RegimePoint]:
    return [RegimePoint(c.close_time, regime, {"adx14": 15.0}) for c in candles]


def _range_then_breakout(volume_on_breakout: float) -> list[Candle]:
    rng = random.Random(5)
    closes = [100.0 + rng.uniform(-1.0, 1.0) for _ in range(80)] + [104.0]
    volumes = [100.0] * 80 + [volume_on_breakout]
    return make_candles(closes, spread=0.002, volumes=volumes)


def test_s2_buy_on_volume_confirmed_breakout() -> None:
    candles = _range_then_breakout(volume_on_breakout=250.0)
    up = _fixed(candles, Regime.UP)
    decisions = s2.decide(candles, up, up, tick=TICK)
    last = decisions[-1]
    assert last.action is Action.BUY
    assert last.stop is not None and last.stop % TICK == 0
    level = max(float(c.high) for c in candles[-21:-1])
    atr = ind.atr([float(c.high) for c in candles], [float(c.low) for c in candles], [float(c.close) for c in candles], 14)[-1]
    assert atr is not None
    assert last.entry == candles[-1].close
    assert last.stop is not None
    assert float(last.stop) == pytest.approx(level - atr, abs=0.01)  # Stop = Ausbruchsniveau − 1 ATR, auf Tick abgerundet
    assert last.target is None and last.max_hold_bars == 30
    assert any("2.5×" in t for t in last.triggers)
    assert all(d.action is Action.NO_TRADE for d in decisions[:-1])


def test_s2_rejects_breakout_without_volume_and_wrong_regime() -> None:
    weak = _range_then_breakout(volume_on_breakout=120.0)
    up = _fixed(weak, Regime.UP)
    last = s2.decide(weak, up, up)[-1]
    assert last.action is Action.NO_TRADE and "Volumenbestätigung" in last.triggers[0]

    strong = _range_then_breakout(volume_on_breakout=250.0)
    for regime in (Regime.DOWN, Regime.STRESS, Regime.UNKNOWN):
        assert s2.decide(strong, _fixed(strong, regime), _fixed(strong, Regime.UP))[-1].action is Action.NO_TRADE
    assert s2.decide(strong, _fixed(strong, Regime.UP), _fixed(strong, Regime.STRESS))[-1].action is Action.NO_TRADE
    sideways = s2.decide(strong, _fixed(strong, Regime.SIDEWAYS), _fixed(strong, Regime.UP))[-1]
    assert sideways.action is Action.BUY
    assert any("Seitwärtsphase" in c for c in sideways.counter)


def test_s2_signals_only_first_breakout_candle() -> None:
    candles = _range_then_breakout(volume_on_breakout=250.0)
    closes = [float(c.close) for c in candles] + [106.0]
    volumes = [float(c.volume) for c in candles] + [400.0]
    extended = make_candles(closes, spread=0.002, volumes=volumes)
    up = _fixed(extended, Regime.UP)
    decisions = s2.decide(extended, up, up)
    assert decisions[-2].action is Action.BUY
    assert decisions[-1].action is Action.NO_TRADE and "Vorkerze" in decisions[-1].triggers[0]


def _range_with_flush() -> list[Candle]:
    rng = random.Random(9)
    closes = [100.0 + rng.uniform(-0.6, 0.6) for _ in range(60)]
    closes += [98.6, 97.4, 96.2, 96.9]  # Abverkauf unter das untere Band, dann erster höherer Schluss
    return make_candles(closes, spread=0.002)


def test_s3_buy_after_oversold_flush_in_range() -> None:
    candles = _range_with_flush()
    side = _fixed(candles, Regime.SIDEWAYS)
    decisions = s3.decide(candles, side, side, tick=TICK)
    last = decisions[-1]
    assert last.action is Action.BUY
    assert last.entry == candles[-1].close
    assert last.stop is not None and last.target is not None
    assert last.stop < last.entry < last.target
    mid = ind.sma([float(c.close) for c in candles], 20)[-1]
    assert mid is not None and float(last.target) == pytest.approx(mid, abs=0.01)  # Ziel = Mittelband, auf Tick abgerundet
    assert (last.target - last.entry) / (last.entry - last.stop) >= Decimal("1.0")
    assert last.max_hold_bars == 10
    assert decisions[-2].action is Action.NO_TRADE  # Abverkauf selbst: noch kein höherer Schluss


def test_s3_only_in_sideways_and_calm_leader() -> None:
    candles = _range_with_flush()
    side = _fixed(candles, Regime.SIDEWAYS)
    for regime in (Regime.UP, Regime.DOWN, Regime.STRESS, Regime.UNKNOWN):
        assert s3.decide(candles, _fixed(candles, regime), side)[-1].action is Action.NO_TRADE
    for leader in (Regime.DOWN, Regime.STRESS, Regime.UNKNOWN):
        last = s3.decide(candles, side, _fixed(candles, leader))[-1]
        assert last.action is Action.NO_TRADE and "Leitmarkt" in last.triggers[0]


@pytest.mark.parametrize("strategy", ACTIVE, ids=lambda s: s.version.id)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_t13_1_truncation_all_strategies(strategy, seed: int) -> None:  # type: ignore[no-untyped-def]
    """T13.1 für jede aktive Strategie: abgeschnittene Historie liefert dieselben Entscheidungen."""
    candles = make_candles(random_walk(520, seed, drift=0.0005, vol=0.02), spread=0.006)
    leader = make_candles(random_walk(520, seed + 100, drift=0.0005, vol=0.02), spread=0.006, instrument_id="TEST:LEAD/USD")
    full = strategy.decide(candles, daily_regimes(candles), daily_regimes(leader))
    rng = random.Random(seed)
    for cut in sorted(rng.sample(range(260, 520), 10)):
        part = strategy.decide(candles[:cut], daily_regimes(candles[:cut]), daily_regimes(leader[:cut]))
        assert part == full[:cut], f"Abweichung bei Schnitt {cut}"


def test_strategy_versions_are_distinct_and_documented() -> None:
    ids = [s.version.id for s in ACTIVE]
    assert ids == ["s1-trend-pullback@1", "s2-volume-breakout@1", "s3-mean-reversion@1"]
    assert all(s.version.params for s in ACTIVE)
