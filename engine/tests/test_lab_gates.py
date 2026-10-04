"""Gates G1–G3: Ist/Soll-Kriterien, Ergebnisregel, Gründe (ohne Datenbank)."""

from __future__ import annotations

from dataclasses import replace

from tradingengine.research import gates
from tradingengine.research.gates import G1Input, G3Input

GOOD = G1Input(
    expectancy_r=0.4, expectancy_ci=(0.2, 0.6), expectancy_r_cost15=0.25, clusters=140, stress_phases=3, down_phases=2, dsr=0.97,
    n_trials=6, pbo=0.1, pbo_variants=20, plateau=True, plateau_note="ok", max_dd=0.06, total_return=0.3, ratio=5.0,
    matched_total=0.1, matched_ratio=1.0, random_p95=0.1,
)


def test_g1_config_is_versioned_with_doc_thresholds() -> None:
    g = gates.GATES_V1["g1"]
    assert gates.GATES_V1["version"] == "gates@1"
    assert (g["min_clusters"], g["min_dsr"], g["max_pbo"], g["cost_factor"], g["max_dd_factor"]) == (100, 0.90, 0.25, 1.5, 1.5)
    assert gates.GATES_V1["g2"]["min_clusters"] == 20 and gates.GATES_V1["g3"]["min_weeks"] == 8 and gates.GATES_V1["g3"]["min_clusters"] == 25


def test_g1_passes_only_when_every_criterion_passes() -> None:
    res = gates.evaluate_g1(GOOD)
    assert res.result == "PASSED" and res.reasons == []
    assert all(set(c) == {"name", "actual", "required", "passed"} for c in res.criteria_json())
    assert all(isinstance(c["actual"], str) and isinstance(c["required"], str) for c in res.criteria_json())
    cases = {
        "NETTO_NEGATIV": replace(GOOD, expectancy_r=-0.1),
        "KOSTENSENSITIV": replace(GOOD, expectancy_r_cost15=-0.01),
        "UEBERANPASSUNG_DSR": replace(GOOD, dsr=0.5),
        "UEBERANPASSUNG_PBO": replace(GOOD, pbo=0.6),
        "KEIN_PLATEAU": replace(GOOD, plateau=False),
        "DRAWDOWN_ZU_HOCH": replace(GOOD, max_dd=0.2),
        "SCHLECHTER_ALS_BASELINE": replace(GOOD, random_p95=0.5),
    }
    for code, m in cases.items():
        r = gates.evaluate_g1(m)
        assert r.result == "FAILED" and code in r.reasons, code


def test_t17_too_few_cases_is_insufficient_with_actual_and_required() -> None:
    r = gates.evaluate_g1(replace(GOOD, clusters=37))
    assert r.result == "INSUFFICIENT" and r.reasons == ["ZU_WENIG_FAELLE"]
    row = next(c for c in r.criteria_json() if c["name"].startswith("Unabhängige Fälle"))
    assert row == {"name": "Unabhängige Fälle (Cluster)", "actual": "37", "required": "≥ 100", "passed": False}
    # fehlende Regime-Abdeckung ebenso; andere Mängel stehen trotzdem als Grund dabei
    r2 = gates.evaluate_g1(replace(GOOD, down_phases=0, expectancy_r=-0.2))
    assert r2.result == "INSUFFICIENT" and set(r2.reasons) == {"REGIME_NICHT_ABGEDECKT", "NETTO_NEGATIV"}
    # nicht bestimmbare Kennzahl (z. B. keine Trades) ist kein Bestehen
    r3 = gates.evaluate_g1(replace(GOOD, dsr=None))
    assert r3.result == "INSUFFICIENT" and r3.reasons == []
    assert r3.criteria_json()[4]["passed"] is None


def test_pbo_not_applicable_for_fixed_version() -> None:
    r = gates.evaluate_g1(replace(GOOD, pbo=None, pbo_applicable=False))
    row = next(c for c in r.criteria_json() if "Overfitting" in c["name"])
    assert row["passed"] is True and "nicht anwendbar" in row["actual"] and r.result == "PASSED"


def test_g2_once_then_consumed() -> None:
    ok = gates.evaluate_g2(0.3, 30, (0.1, 0.6), access_number=1)
    assert ok.result == "PASSED"
    assert gates.evaluate_g2(-0.05, 30, (0.1, 0.6), 1).reasons == ["NETTO_NEGATIV", "HOLDOUT_EINBRUCH"]
    assert gates.evaluate_g2(0.3, 12, (0.1, 0.6), 1).result == "INSUFFICIENT"
    second = gates.evaluate_g2(0.3, 30, (0.1, 0.6), access_number=2)
    assert second.result == "INSUFFICIENT" and "HOLDOUT_VERBRAUCHT" in second.reasons


def test_g3_insufficient_before_eight_weeks_and_never_a_proof() -> None:
    early = gates.evaluate_g3(G3Input(weeks=5.0, clusters=40, trades=40, paper_cum_r=3.0, expected_p10=-2.0, fidelity_checked=10,
                                      fidelity_matched=10, ops_issues=[]))
    assert early.result == "INSUFFICIENT" and early.reasons == ["ZU_WENIG_FAELLE"]
    assert any("kein statistischer Vorteilsbeweis" in n for n in early.notes)
    ok = gates.evaluate_g3(G3Input(9.0, 26, 30, 1.0, -2.0, 30, 30, []))
    assert ok.result == "PASSED"
    bad = gates.evaluate_g3(G3Input(9.0, 26, 30, -5.0, -2.0, 30, 29, ["Abgleich P1: DIFF"]))
    assert bad.result == "FAILED" and set(bad.reasons) == {"FORWARD_INKONSISTENT", "BETRIEB_INSTABIL"}
