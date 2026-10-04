import math

import pytest

from tradingengine.core import indicators as ind


def test_sma_and_warmup() -> None:
    assert ind.sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_ema_seed_and_step() -> None:
    out = ind.ema([1, 2, 3, 4], 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)  # SMA der ersten drei Werte
    assert out[3] == pytest.approx(4 * 0.5 + 2.0 * 0.5)


def test_true_range_uses_previous_close() -> None:
    tr = ind.true_range([10, 12], [9, 11.5], [9.5, 12])
    assert tr == [1.0, 2.5]  # zweite Kerze: Hoch 12 − Vortagesschluss 9.5


def test_atr_constant_range() -> None:
    n = 30
    high = [11.0] * n
    low = [10.0] * n
    close = [10.5] * n
    out = ind.atr(high, low, close, 14)
    assert out[12] is None
    assert out[13] == pytest.approx(1.0)
    assert out[-1] == pytest.approx(1.0)


def test_rsi_extremes_and_balance() -> None:
    rising = [float(i) for i in range(1, 40)]
    assert ind.rsi(rising, 14)[-1] == pytest.approx(100.0)
    falling = rising[::-1]
    assert ind.rsi(falling, 14)[-1] == pytest.approx(0.0)
    zigzag = [100.0 + (1 if i % 2 else 0) for i in range(60)]
    assert ind.rsi(zigzag, 14)[-1] == pytest.approx(50.0, abs=4.0)


def test_adx_strong_trend_vs_chop() -> None:
    n = 120
    trend = [100.0 + i for i in range(n)]
    adx_trend = ind.adx([c + 0.5 for c in trend], [c - 0.5 for c in trend], trend, 14)[-1]
    chop = [100.0 + math.sin(i) for i in range(n)]
    adx_chop = ind.adx([c + 0.5 for c in chop], [c - 0.5 for c in chop], chop, 14)[-1]
    assert adx_trend is not None and adx_chop is not None
    assert adx_trend > 60
    assert adx_chop < 20


def test_percentile_rank_and_rolling() -> None:
    values: ind.Series = [1.0, 2.0, 3.0, 4.0]
    assert ind.percentile_rank(values, window=4, min_obs=2) == [None, 1.0, 1.0, 1.0]
    assert ind.rolling_max([1, 3, 2, 5], 2) == [None, 3, 3, 5]
    assert ind.rolling_median([1, 3, 2, 5], 3) == [None, None, 2, 3]


@pytest.mark.parametrize("cut", [50, 77, 140])
def test_indicators_are_causal(cut: int) -> None:
    """Werte bis `cut` ändern sich nicht, wenn spätere Daten hinzukommen."""
    close = [100.0 + math.sin(i / 3) * 5 + i * 0.1 for i in range(200)]
    high = [c + 1 for c in close]
    low = [c - 1 for c in close]
    for fn in (
        lambda c, h, lo: ind.ema(c, 20),
        lambda c, h, lo: ind.atr(h, lo, c, 14),
        lambda c, h, lo: ind.rsi(c, 14),
        lambda c, h, lo: ind.adx(h, lo, c, 14),
        lambda c, h, lo: ind.realized_vol(c, 20),
    ):
        full = fn(close, high, low)
        part = fn(close[:cut], high[:cut], low[:cut])
        assert part == full[:cut]
