"""Statistik des Lernlabors: DSR, PBO, Cluster, Bootstrap, Plateau (ohne Datenbank, ohne Netz)."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta

from tradingengine.research import stats


def _noise(n_variants: int, days: int, seed: int) -> list[list[float]]:
    rng = random.Random(seed)
    return [[rng.gauss(0.0, 0.01) for _ in range(days)] for _ in range(n_variants)]


def test_pure_noise_has_high_pbo_and_low_dsr_for_many_variants() -> None:
    matrix = _noise(40, 1500, 3)
    res = stats.pbo_cscv(matrix, 10)
    assert res is not None
    pbo, logits = res
    assert len(logits) == 252  # C(10, 5)
    assert pbo > 0.3  # Auswahl unter Zufallsvarianten ist überwiegend Zufall
    srs = [stats.sharpe(r) or 0.0 for r in matrix]
    var = sum((s - sum(srs) / len(srs)) ** 2 for s in srs) / len(srs)
    best = max(range(len(matrix)), key=lambda k: srs[k])
    dsr = stats.deflated_sharpe(matrix[best], len(matrix), var)
    assert dsr is not None and dsr < 0.9  # die beste von 40 Zufallsvarianten besteht nicht
    # ohne Korrektur (N = 1) sähe dieselbe Reihe besser aus – genau das verhindert die DSR
    psr = stats.deflated_sharpe(matrix[best], 1, 0.0)
    assert psr is not None and psr > dsr


def test_known_edge_has_low_pbo_and_high_dsr() -> None:
    rng = random.Random(5)
    days = 1500
    edge = [rng.gauss(0.0015, 0.01) for _ in range(days)]
    matrix = [edge] + [[rng.gauss(0.0, 0.01) for _ in range(days)] for _ in range(19)]
    res = stats.pbo_cscv(matrix, 10)
    assert res is not None and res[0] <= 0.25
    srs = [stats.sharpe(r) or 0.0 for r in matrix]
    var = sum((s - sum(srs) / len(srs)) ** 2 for s in srs) / len(srs)
    dsr = stats.deflated_sharpe(edge, 20, var)
    assert dsr is not None and dsr >= 0.9


def test_dsr_uses_number_of_trials() -> None:
    rng = random.Random(9)
    r = [rng.gauss(0.0006, 0.01) for _ in range(1000)]
    assert (stats.deflated_sharpe(r, 1, 0.001) or 0) > (stats.deflated_sharpe(r, 10, 0.001) or 0) > (stats.deflated_sharpe(r, 100, 0.001) or 0)
    assert stats.deflated_sharpe([0.0] * 10, 5, 0.1) is None  # ohne Streuung nicht bestimmbar
    assert stats.pbo_cscv([[0.1] * 100], 10) is None  # eine Variante: nicht bestimmbar


T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _t(inst: str, start: int, end: int, r: float = 1.0) -> stats.TradeRec:
    return stats.TradeRec(inst, T0 + timedelta(days=start), T0 + timedelta(days=end), r, r * 50, 0.05, end - start)


def _returns(seed: int, days: int, common: float) -> tuple[dict[date, float], dict[date, float]]:
    rng = random.Random(seed)
    a, b = {}, {}
    for i in range(days):
        z = rng.gauss(0, 0.02)
        d = (T0 + timedelta(days=i)).date()
        a[d] = z
        b[d] = common * z + (1 - common) * rng.gauss(0, 0.02)
    return a, b


def test_clusters_merge_overlap_on_same_instrument_and_correlated_instruments() -> None:
    a, b = _returns(1, 200, 0.95)
    _, c = _returns(2, 200, 0.0)
    returns = {"A": a, "B": b, "C": c}
    trades = [_t("A", 0, 30), _t("A", 20, 40), _t("A", 50, 60), _t("B", 55, 80), _t("C", 52, 90), _t("C", 120, 130)]
    clusters = stats.cluster_trades(trades, returns)
    as_sets = sorted(sorted(trades[i].instrument + str(trades[i].entry.day) for i in c) for c in clusters)
    assert len(clusters) == 4
    # A(0–30) und A(20–40) überlappen auf demselben Instrument; A(50–60) und B(55–80) sind hoch korreliert
    assert any(len(s) == 2 and all(x.startswith("A") for x in s) for s in as_sets)
    assert any(sorted(x[0] for x in s) == ["A", "B"] for s in as_sets)
    # C ist unkorreliert: überlappt zeitlich, bleibt aber ein eigener Fall
    assert sum(1 for s in as_sets if s and s[0].startswith("C")) == 2


def test_cluster_bootstrap_is_reproducible_and_brackets_mean() -> None:
    rng = random.Random(4)
    clusters = [[rng.gauss(0.2, 1.0)] for _ in range(150)]
    a = stats.cluster_bootstrap_mean(clusters, seed=11)
    b = stats.cluster_bootstrap_mean(clusters, seed=11)
    assert a == b and a is not None
    m = sum(c[0] for c in clusters) / len(clusters)
    assert a[0] < m < a[1]


def test_plateau_and_paired_comparison() -> None:
    assert stats.plateau(0.4, {"x−20 %": 0.3, "x+20 %": 0.25})[0] is True
    assert stats.plateau(0.4, {"x−20 %": 0.3, "x+20 %": 0.1})[0] is False  # Nadelspitze
    assert stats.plateau(-0.1, {"x−20 %": 0.3})[0] is False
    assert stats.plateau(0.4, {})[0] is None
    better = stats.paired_bootstrap([0.5 + 0.1 * (i % 3) for i in range(20)], seed=1)
    assert better is not None and better[1] > 0
    unclear = stats.paired_bootstrap([(-1) ** i * 0.5 for i in range(20)], seed=1)
    assert unclear is not None and unclear[1] < 0 < unclear[2]
