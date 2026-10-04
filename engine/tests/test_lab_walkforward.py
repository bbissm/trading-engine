"""Walk-forward, Purging/Embargo, Datensatz-Hash, Varianten, Baselines, Meta-Labeling (ohne Datenbank)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from lab_synth import dataset, market

from tradingengine.core.costs import KRAKEN_SPOT_TIER1
from tradingengine.core.strategies import s1_trend_pullback
from tradingengine.research import baselines, metalabel, variants
from tradingengine.research import walkforward as wf
from tradingengine.research.data import dataset_hash

INSTS = ["T:A/USD", "T:B/USD"]


@pytest.fixture(scope="module")
def trend() -> wf.Prepared:
    return wf.prepare(dataset(market("trend", INSTS, start=datetime(2017, 1, 1, tzinfo=UTC)), INSTS[0]))


def test_folds_have_sperrzone_and_contiguous_tests() -> None:
    start, end = datetime(2015, 1, 1, tzinfo=UTC), datetime(2025, 10, 1, tzinfo=UTC)
    cfg = wf.WFConfig()
    folds = wf.make_folds(start, end, "1d", 40, cfg)
    gap = timedelta(days=40) + timedelta(days=5)
    assert len(folds) > 10
    for a, b in zip(folds, folds[1:], strict=False):
        assert b.test_start == a.test_end  # lückenlose Testfenster
    for f in folds:
        assert f.test_start - f.train_end == gap
        assert wf.add_months(f.train_start, 24) == f.train_end or f.train_start == start
        assert f.test_end <= end and f.run_end <= end
    four_h = wf.make_folds(start, end, "4h", 40, cfg)
    assert four_h[0].test_start - four_h[0].train_end == timedelta(hours=4 * 40) + timedelta(days=5)


def test_purge_no_train_label_overlaps_test(trend: wf.Prepared) -> None:
    fam = variants.family("s1-trend-pullback")
    params = variants.full_params(fam, {})
    cfg = wf.WFConfig()
    folds = wf.make_folds(trend.ds.start, trend.ds.end, "1d", variants.max_hold(params), cfg)
    embargo = timedelta(days=cfg.embargo_days)
    seen_train = seen_test = 0
    for f in folds[:6]:
        out = wf.evaluate_fold(trend, fam, params, f, KRAKEN_SPOT_TIER1, cfg, with_train=True)
        assert out.train_trades is not None
        for t in out.train_trades:
            seen_train += 1
            assert f.train_start <= t.entry and t.exit <= f.train_end  # Label vor Trainingsende bekannt
            assert t.exit + embargo < f.test_start  # Sperrzone: kein Trainings-Label reicht ins Testfenster
        for t in out.trades:
            seen_test += 1
            # Entscheidung im Testfenster, Füllung frühestens eine Kerze danach, Exit spätestens am Laufende
            assert f.test_start < t.entry <= f.test_end + timedelta(days=1)
            assert t.exit <= f.run_end
    assert seen_train > 0 and seen_test > 0


def test_dataset_hash_is_canonical_and_sensitive() -> None:
    series = market("noise", INSTS, start=datetime(2023, 1, 1, tzinfo=UTC))
    ds1, ds2 = dataset(series, INSTS[0]), dataset({k: list(v) for k, v in series.items()}, INSTS[0])
    assert ds1.hash == ds2.hash and len(ds1.hash) == 64
    # Gleicher Wert in anderer Decimal-Schreibweise (numeric(28,10) aus der DB) ändert den Hash nicht
    c = series[INSTS[0]][10]
    padded = {**series, INSTS[0]: [*series[INSTS[0]][:10], replace(c, close=Decimal(f"{c.close:.10f}")), *series[INSTS[0]][11:]]}
    assert dataset_hash({f"{k}|1d": v for k, v in padded.items()}) == dataset_hash({f"{k}|1d": v for k, v in series.items()})
    changed = {**series, INSTS[0]: [*series[INSTS[0]][:10], replace(c, close=c.close + Decimal("0.01")), *series[INSTS[0]][11:]]}
    assert dataset(changed, INSTS[0]).hash != ds1.hash


def test_variants_never_touch_strategy_defaults() -> None:
    fam = variants.family("s1-trend-pullback")
    before = s1_trend_pullback.PARAMS
    g = variants.grid(fam)
    assert 1 < len(g) <= 50 and g[0] == variants.full_params(fam, {})
    for p in g:
        for name, (lo, hi) in fam.limits.items():
            assert lo <= p[name] <= hi
    vid = variants.version_id(fam, g[1])
    assert vid.startswith("s1-trend-pullback@1+opt-") and len(vid.split("+opt-")[1]) == 8
    assert variants.version_id(fam, g[0]) == "s1-trend-pullback@1"
    assert s1_trend_pullback.PARAMS == before and s1_trend_pullback.VERSION.params == variants.full_params(fam, {})
    nb = variants.neighbours(fam, g[0], fam.free)
    assert set(nb) == {"stop_atr−20 %", "stop_atr+20 %", "ema_len−20 %", "ema_len+20 %", "trail_atr−20 %"}  # trail 3.6 > Grenze 3
    with pytest.raises(ValueError):
        variants.grid(fam, ("stop_atr", "ema_len", "trail_atr", "rsi_len"))


def test_entry_window_and_delay_wrappers(trend: wf.Prepared) -> None:
    fam = variants.family("s1-trend-pullback")
    params = variants.full_params(fam, {})
    candles = trend.ds.candles[INSTS[0]]
    own, lead = trend.own[INSTS[0]], trend.leader[INSTS[0]]
    base = variants.make_strategy(fam, params).decide(candles, own, lead, tick=None)
    lo, hi = candles[800].close_time, candles[1200].close_time
    windowed = variants.make_strategy(fam, params, (lo, hi)).decide(candles, own, lead, tick=None)
    buys = [d for d in windowed if d.action.value == "BUY"]
    assert buys and all(lo <= d.candle_close < hi for d in buys)
    delayed = variants.make_strategy(fam, params, delay_bars=1).decide(candles, own, lead, tick=None)
    for i in range(1, len(base)):
        if base[i - 1].action.value == "BUY":
            assert delayed[i].action.value == "BUY" and delayed[i].entry == base[i - 1].entry and delayed[i].candle_close == base[i].candle_close


def test_baselines_and_metalabel_on_little_data(trend: wf.Prepared) -> None:
    fam = variants.family("s1-trend-pullback")
    params = variants.full_params(fam, {})
    cfg = wf.WFConfig()
    folds = wf.make_folds(trend.ds.start, trend.ds.end, "1d", 40, cfg)
    outs = [wf.evaluate_fold(trend, fam, params, f, KRAKEN_SPOT_TIER1, cfg, with_train=True) for f in folds]
    index = wf.day_index(folds)
    a = baselines.compute(trend, folds, outs, index, KRAKEN_SPOT_TIER1, 200, 3)
    b = baselines.compute(trend, folds, outs, index, KRAKEN_SPOT_TIER1, 200, 3)
    assert a == b  # fester Seed
    assert a.cash.total == 0 and 0 < a.exposure < 1 and a.random_p95 >= a.random_median
    res = metalabel.run(trend, folds, outs, seed=1)
    assert res.status == "ZU_WENIG_DATEN" and res.n_oof < metalabel.MIN_OOF and not res.calibrated
    for step in res.pipeline:  # T13.3: kein Trainingslabel nach Trainingsende, Skalierer nur auf Trainingsdaten
        if step["max_label_at"] is not None:
            assert step["max_label_at"] <= step["train_end"]
        assert step["scaler_fitted_on"] == "train"


def test_logistic_regression_learns_and_calibration_metrics() -> None:
    import random

    rng = random.Random(2)
    xs = [[rng.gauss(0, 1), rng.gauss(0, 1)] for _ in range(600)]
    ys = [int(x[0] + 0.3 * rng.gauss(0, 1) > 0) for x in xs]
    model = metalabel.fit(xs[:400], ys[:400])
    ps = [model.predict(x) for x in xs[400:]]
    acc = sum(int((p > 0.5) == bool(y)) for p, y in zip(ps, ys[400:], strict=True)) / 200
    assert acc > 0.85
    assert metalabel.ece([0.9] * 10, [1] * 9 + [0]) == pytest.approx(0.0)
    assert metalabel.ece([0.9] * 10, [0] * 10) == pytest.approx(0.9)
