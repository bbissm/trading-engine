import random
from datetime import timedelta
from decimal import Decimal

import pytest
from helpers import make_candles, random_walk

from tradingengine.core import indicators as ind
from tradingengine.core.candles import Candle
from tradingengine.core.regime import Regime, RegimePoint, daily_regimes, regime_at
from tradingengine.core.signals import Action
from tradingengine.core.strategies import s1_trend_pullback as s1


def test_regime_unknown_without_200_days() -> None:
    points = daily_regimes(make_candles([100.0 + i for i in range(150)]))
    assert {p.regime for p in points} == {Regime.UNKNOWN}


def _share(points: list[RegimePoint], regime: Regime, last: int = 100) -> float:
    tail = points[-last:]
    return sum(1 for p in tail if p.regime is regime) / len(tail)


def test_regime_up_down_sideways() -> None:
    """Verrauschte Reihen: das erwartete Regime dominiert; Stress bleibt die Ausnahme."""
    up = daily_regimes(make_candles(random_walk(500, 3, drift=0.004, vol=0.01), spread=0.004))
    assert _share(up, Regime.UP) > 0.6
    down = daily_regimes(make_candles(random_walk(500, 3, drift=-0.004, vol=0.01, start=1000.0), spread=0.004))
    assert _share(down, Regime.DOWN) > 0.6
    assert _share(down, Regime.UP) == 0
    rng = random.Random(7)
    flat = daily_regimes(make_candles([100.0 + rng.uniform(-0.5, 0.5) for _ in range(500)], spread=0.004))
    assert _share(flat, Regime.SIDEWAYS) > 0.5
    for points in (up, down, flat):
        assert _share(points, Regime.STRESS) < 0.3


def test_regime_stress_on_sharp_drop() -> None:
    closes = random_walk(400, 3, drift=0.004, vol=0.01)
    closes.append(closes[-1] * 0.80)  # Einbruch um 20 % an einem Tag
    points = daily_regimes(make_candles(closes, spread=0.004))
    assert points[-1].regime is Regime.STRESS
    assert points[-1].features["drop5"] is not None


def test_regime_at_uses_only_closed_days() -> None:
    candles = make_candles(random_walk(320, 3, drift=0.004, vol=0.01), spread=0.004)
    points = daily_regimes(candles)
    assert regime_at(points, candles[0].open_time) is Regime.UNKNOWN
    just_before_last_close = candles[-1].close_time - timedelta(hours=1)
    assert regime_at(points, just_before_last_close) is points[-2].regime
    assert regime_at(points, candles[-1].close_time) is points[-1].regime


def _fixed_regimes(candles: list[Candle], regime: Regime) -> list[RegimePoint]:
    """S1 getrennt von der Regime-Regel prüfen: Regime wird vorgegeben."""
    return [RegimePoint(c.close_time, regime, {"adx14": 30.0}) for c in candles]


def _uptrend_with_pullback() -> list[Candle]:
    closes = [100.0 * 1.004**i for i in range(300)]
    last = closes[-1]
    closes += [last * 0.985, last * 0.975, last * 0.97, last * 1.02]  # Rücksetzer, dann Schluss über Vorkerzenhoch
    return make_candles(closes, spread=0.004)


def test_s1_buy_after_pullback_in_uptrend() -> None:
    candles = _uptrend_with_pullback()
    regimes = _fixed_regimes(candles, Regime.UP)
    decisions = s1.decide(candles, regimes, regimes)
    last = decisions[-1]
    assert last.action is Action.BUY
    assert last.entry == candles[-1].close
    assert last.stop is not None and last.stop < last.entry
    assert last.target is None
    assert last.score == 40 + 30 + 10 - 15  # Basis + Tages-ADX + Volumen ≥ Median − RSI überkauft
    assert any("RSI" in c for c in last.counter)
    assert any("Kosten" in c for c in last.counter)  # fehlende Kostenprüfung steht offen am Signal
    # während des Rücksetzers selbst kein Kauf, und im glatten Trend davor kein Rücksetzer
    assert decisions[-2].action is Action.NO_TRADE
    assert "Kein Rücksetzer" in decisions[250].triggers[0]


def test_s1_no_trade_outside_uptrend_and_on_leader_stress() -> None:
    candles = _uptrend_with_pullback()
    up = _fixed_regimes(candles, Regime.UP)
    for regime in (Regime.DOWN, Regime.SIDEWAYS, Regime.STRESS, Regime.UNKNOWN):
        own = _fixed_regimes(candles, regime)
        assert all(d.action is Action.NO_TRADE for d in s1.decide(candles, own, up))
    last = s1.decide(candles, up, _fixed_regimes(candles, Regime.STRESS))[-1]
    assert last.action is Action.NO_TRADE
    assert "Leitmarkt" in last.triggers[0]
    sideways_leader = s1.decide(candles, up, _fixed_regimes(candles, Regime.SIDEWAYS))[-1]
    assert sideways_leader.action is Action.BUY
    assert any("Leitmarkt" in c for c in sideways_leader.counter)


def test_s1_stop_distance_is_two_atr() -> None:
    candles = _uptrend_with_pullback()
    regimes = _fixed_regimes(candles, Regime.UP)
    last = s1.decide(candles, regimes, regimes)[-1]
    atr = ind.atr([float(c.high) for c in candles], [float(c.low) for c in candles], [float(c.close) for c in candles], 14)[-1]
    assert atr is not None
    assert last.entry is not None and last.stop is not None
    assert last.entry - last.stop == pytest.approx(Decimal(str(2 * atr)), abs=Decimal("0.0001"))


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_t13_1_truncation_no_lookahead(seed: int) -> None:
    """T13.1: Entscheidungen auf abgeschnittener Historie sind identisch zu denen auf voller Historie."""
    candles = make_candles(random_walk(520, seed, drift=0.002), spread=0.006)
    leader = make_candles(random_walk(520, seed + 100, drift=0.002), spread=0.006, instrument_id="TEST:LEAD/USD")
    full_regimes = daily_regimes(candles)
    full_leader = daily_regimes(leader)
    full = s1.decide(candles, full_regimes, full_leader)

    rng = random.Random(seed)
    for cut in sorted(rng.sample(range(260, 520), 12)):
        part = s1.decide(candles[:cut], daily_regimes(candles[:cut]), daily_regimes(leader[:cut]))
        assert part == full[:cut], f"Abweichung bei Schnitt {cut}"
    assert full_regimes[:300] == daily_regimes(candles[:300])
