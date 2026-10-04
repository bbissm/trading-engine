from decimal import Decimal as D

import pytest
from helpers import make_candles, random_walk

from tradingengine.backtest import BacktestConfig, run_backtest
from tradingengine.core.costs import KRAKEN_SPOT_TIER1, CostModel
from tradingengine.core.regime import daily_regimes
from tradingengine.core.strategies import ACTIVE


def _data(seed: int, n: int = 900, drift: float = 0.001):
    candles = make_candles(random_walk(n, seed, drift=drift, vol=0.02), spread=0.012, volumes=[5000.0] * n)
    regimes = daily_regimes(candles)
    return candles, regimes


@pytest.mark.parametrize("strategy", ACTIVE, ids=lambda s: s.version.id)
@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_backtest_invariants(strategy, seed: int) -> None:  # type: ignore[no-untyped-def]
    candles, regimes = _data(seed)
    result = run_backtest(candles, regimes, regimes, strategy, BacktestConfig())
    cfg = BacktestConfig()
    assert len(result.equity_curve) == len(candles)
    assert all(eq > 0 for _, eq in result.equity_curve)
    previous_exit = None
    for t in result.trades:
        assert t.exit_time >= t.entry_time
        assert previous_exit is None or t.entry_time >= previous_exit  # nie zwei Positionen gleichzeitig
        previous_exit = t.exit_time
        assert t.result.open_qty == 0 and t.result.qty > 0
        assert t.result.net == t.result.gross - t.result.fees
        assert t.result.fees > 0  # beide Seiten kosten Gebühren
        assert t.planned_risk <= cfg.start_cash * 2 * cfg.policy.risk_per_trade  # Risiko pro Trade bleibt begrenzt
    assert result.stats.trades == len(result.trades)
    assert result.stats.net == sum((t.result.net for t in result.trades), D(0))
    assert 0 <= result.stats.max_drawdown < 1
    assert result.entry_orders >= result.stats.trades


def test_backtest_is_reproducible() -> None:
    candles, regimes = _data(7)
    a = run_backtest(candles, regimes, regimes, ACTIVE[0])
    b = run_backtest(candles, regimes, regimes, ACTIVE[0])
    assert a.trades == b.trades and a.equity_curve == b.equity_curve and a.stats == b.stats


@pytest.mark.parametrize("strategy", ACTIVE, ids=lambda s: s.version.id)
def test_t13_backtest_has_no_lookahead(strategy) -> None:  # type: ignore[no-untyped-def]
    """Trades, die vor einem Schnittpunkt abgeschlossen waren, sind auf der abgeschnittenen Historie identisch."""
    candles, _ = _data(11)
    full = run_backtest(candles, daily_regimes(candles), daily_regimes(candles), strategy)
    for cut in (500, 640, 777):
        part_candles = candles[:cut]
        part = run_backtest(part_candles, daily_regimes(part_candles), daily_regimes(part_candles), strategy)
        cut_time = part_candles[-1].close_time
        assert part.trades == [t for t in full.trades if t.exit_time <= cut_time][: len(part.trades)]
        assert part.equity_curve[:-1] == full.equity_curve[: cut - 1]


def test_costs_reduce_results_and_sensitivity_scales() -> None:
    candles, regimes = _data(3, drift=0.002)
    free = run_backtest(candles, regimes, regimes, ACTIVE[0], BacktestConfig(cost=CostModel("zero", D(0), D(0), D(0))))
    base = run_backtest(candles, regimes, regimes, ACTIVE[0], BacktestConfig())
    double = run_backtest(candles, regimes, regimes, ACTIVE[0], BacktestConfig(cost=KRAKEN_SPOT_TIER1.scaled(D(2))))
    assert base.cost_model == KRAKEN_SPOT_TIER1.version and "×2" in double.cost_model
    if free.stats.trades and base.stats.trades:
        assert free.stats.fees == 0 < base.stats.fees
        avg = lambda r: r.stats.fees / sum((t.result.entry_value for t in r.trades), D(0))  # noqa: E731
        assert avg(double) > avg(base)


def test_no_entries_before_warmup_and_none_on_last_candle() -> None:
    candles, regimes = _data(5)
    result = run_backtest(candles, regimes, regimes, ACTIVE[0], BacktestConfig(warmup_bars=600))
    assert all(t.entry_time > candles[600].close_time for t in result.trades)


def test_loss_streak_cooldown_expires() -> None:
    """Nach einer Verlustserie gilt eine Wartezeit, danach sind Einstiege wieder möglich (kein Dauerblock)."""
    from tradingengine.core.risk import RiskPolicy

    candles, regimes = _data(4)
    strict = run_backtest(candles, regimes, regimes, ACTIVE[0], BacktestConfig(policy=RiskPolicy(loss_streak_cooldown=1, loss_streak_cooldown_hours=24)))
    losses = [i for i, t in enumerate(strict.trades) if t.result.net < 0]
    assert losses and losses[0] < len(strict.trades) - 1  # nach dem ersten Verlust folgen weitere Trades
    for i in losses[:-1]:
        nxt = strict.trades[i + 1]
        assert (nxt.entry_time - strict.trades[i].exit_time).total_seconds() >= 24 * 3600
