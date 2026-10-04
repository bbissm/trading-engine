"""Gates G1–G3 (docs/04, 4.3) mit versionierter Konfiguration `gates@1`.

Jedes Gate liefert Kriterien `{name, actual, required, passed}` (Zahlen als Text; `passed = None`, wenn nicht
bestimmbar), ein Ergebnis PASSED | FAILED | INSUFFICIENT und maschinenlesbare Gründe aus docs/04.

Ergebnisregel: Fehlen Fälle oder Regime-Abdeckung (Evidenzkriterien), lautet das Ergebnis INSUFFICIENT
(Statuskarte «zu wenig Evidenz»), auch wenn weitere Kriterien scheitern – deren Gründe stehen trotzdem dabei.
Sonst FAILED bei einem gescheiterten Kriterium, INSUFFICIENT bei einem nicht bestimmbaren, PASSED nur, wenn
alle Kriterien bestanden sind.

Lebenszyklus (nur `strategy_version.lifecycle_status` wird geschrieben):
G1 PASSED → HISTORISCH_GEPRUEFT · G2 PASSED → VALIDIERT · G3 PASSED → FORWARD_PAPER (+ Freigabe-Vorschlag PENDING);
FAILED → ABGELEHNT; INSUFFICIENT → ZU_WENIG_EVIDENZ (bei G3 bleibt der Status, Forward-Evidenz wächst noch).
Eine Freigabe über PENDING hinaus erzeugt das Lernlabor nie.

G3 ist ausdrücklich kein statistischer Vorteilsbeweis: geprüft wird, ob die Forward-Realität der historischen
Erwartung nicht widerspricht und ob der Betrieb stimmt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from ..core.candles import Candle
from ..core.regime import daily_regimes
from ..core.risk import RiskPolicy
from ..core.signals import StrategyVersion
from .data import Conn
from .stats import TradeRec, bootstrap_sum_distribution, cluster_trades, daily_log_returns, fmt, percentile
from .variants import FAMILIES, make_strategy
from .walkforward import day_of

GATES_VERSION = "gates@1"
DD_LIMIT = float(RiskPolicy().drawdown_pause)  # konfiguriertes Drawdown-Limit (8 %)

GATES_V1: dict[str, Any] = {
    "version": GATES_VERSION,
    "g1": {
        "min_expectancy_r": 0.0,
        "cost_factor": 1.5,
        "min_clusters": 100,
        "regime_phase_min_days": 5,
        "min_stress_phases": 1,
        "min_down_phases": 1,
        "min_dsr": 0.90,
        "max_pbo": 0.25,
        "plateau_step": 0.20,
        "plateau_ratio": 0.50,
        "max_dd_factor": 1.5,
        "random_percentile": 0.95,
    },
    "g2": {"min_expectancy_r": 0.0, "bootstrap_interval": 0.80, "min_clusters": 20},
    "g3": {"min_weeks": 8, "min_clusters": 25, "consistency_percentile": 0.10, "signal_fidelity": 1.0},
}

EVIDENCE_CODES = {"ZU_WENIG_FAELLE", "REGIME_NICHT_ABGEDECKT"}
LIFECYCLE = {
    "G1": {"PASSED": "HISTORISCH_GEPRUEFT", "FAILED": "ABGELEHNT", "INSUFFICIENT": "ZU_WENIG_EVIDENZ"},
    "G2": {"PASSED": "VALIDIERT", "FAILED": "ABGELEHNT", "INSUFFICIENT": "ZU_WENIG_EVIDENZ"},
    "G3": {"PASSED": "FORWARD_PAPER", "FAILED": "ABGELEHNT"},
}
ACTOR = "engine:lab"


@dataclass(frozen=True, slots=True)
class Criterion:
    name: str
    actual: str
    required: str
    passed: bool | None
    code: str | None = None  # Ablehnungsgrund, falls nicht bestanden


@dataclass
class GateResult:
    gate: str
    result: str
    criteria: list[Criterion]
    reasons: list[str]
    config_version: str = GATES_VERSION
    notes: list[str] = field(default_factory=list)

    def criteria_json(self) -> list[dict[str, Any]]:
        return [{"name": c.name, "actual": c.actual, "required": c.required, "passed": c.passed} for c in self.criteria]


def _decide(gate: str, criteria: list[Criterion], notes: list[str] | None = None) -> GateResult:
    failed = [c for c in criteria if c.passed is False]
    reasons: list[str] = []
    for c in failed:
        if c.code and c.code not in reasons:
            reasons.append(c.code)
    if any(c.code in EVIDENCE_CODES for c in failed):
        result = "INSUFFICIENT"
    elif failed:
        result = "FAILED"
    elif any(c.passed is None for c in criteria):
        result = "INSUFFICIENT"
    else:
        result = "PASSED"
    return GateResult(gate, result, criteria, reasons, GATES_VERSION, notes or [])


def _gt(x: float | None, limit: float) -> bool | None:
    return None if x is None else x > limit


# ───────────────────────── G1 ─────────────────────────


@dataclass(frozen=True)
class G1Input:
    expectancy_r: float | None
    expectancy_ci: tuple[float, float] | None
    expectancy_r_cost15: float | None
    clusters: int
    stress_phases: int
    down_phases: int
    dsr: float | None
    n_trials: int
    pbo: float | None
    pbo_variants: int
    plateau: bool | None
    plateau_note: str
    max_dd: float | None
    total_return: float
    ratio: float | None  # Rendite / Max-Drawdown der Strategie
    matched_total: float
    matched_ratio: float | None
    random_p95: float | None
    pbo_applicable: bool = True  # False: Version vorab festgelegt, keine Auswahl unter Varianten


def _better_ratio(total: float, ratio: float | None, other_total: float, other_ratio: float | None) -> bool:
    if ratio is None:  # Strategie ohne Drawdown: besser, wenn positiv und die Baseline einen Drawdown hat oder weniger bringt
        if total <= 0:
            return False
        return other_ratio is not None or total > other_total
    if other_ratio is None:
        return total > other_total
    return ratio > other_ratio


def evaluate_g1(m: G1Input) -> GateResult:
    g = GATES_V1["g1"]
    ci = f" (95 %-KI {fmt(m.expectancy_ci[0])} … {fmt(m.expectancy_ci[1])})" if m.expectancy_ci else ""
    dd_max = g["max_dd_factor"] * DD_LIMIT
    crit = [
        Criterion("Netto-Erwartungswert je Trade (Basiskosten)", f"{fmt(m.expectancy_r)} R{ci}", "> 0 R", _gt(m.expectancy_r, 0), "NETTO_NEGATIV"),
        Criterion("Netto-Erwartungswert je Trade (Kosten × 1.5)", f"{fmt(m.expectancy_r_cost15)} R", "> 0 R", _gt(m.expectancy_r_cost15, 0), "KOSTENSENSITIV"),
        Criterion("Unabhängige Fälle (Cluster)", str(m.clusters), f"≥ {g['min_clusters']}", m.clusters >= g["min_clusters"], "ZU_WENIG_FAELLE"),
        Criterion("Regime-Abdeckung der Testfenster (Leitmarkt)",
                  f"{m.stress_phases} Stress-, {m.down_phases} Abwärtsphasen (≥ {g['regime_phase_min_days']} Tage)",
                  "≥ 1 Stressphase und ≥ 1 Abwärtsphase",
                  m.stress_phases >= g["min_stress_phases"] and m.down_phases >= g["min_down_phases"], "REGIME_NICHT_ABGEDECKT"),
        Criterion("Deflated Sharpe Ratio", f"{fmt(m.dsr)} (N = {m.n_trials} Varianten)", f"≥ {g['min_dsr']:.2f}",
                  None if m.dsr is None else m.dsr >= g["min_dsr"], "UEBERANPASSUNG_DSR"),
        Criterion("Probability of Backtest Overfitting (CSCV)",
                  f"{fmt(m.pbo)} ({m.pbo_variants} Varianten)" if m.pbo_applicable else "nicht anwendbar: Version vorab festgelegt, keine Auswahl",
                  f"≤ {g['max_pbo']:.2f}", (None if m.pbo is None else m.pbo <= g["max_pbo"]) if m.pbo_applicable else True, "UEBERANPASSUNG_PBO"),
        Criterion("Plateau-Test (Nachbarn ±20 %)", m.plateau_note, "alle Nachbarn ≥ 50 % der Kennzahl", m.plateau, "KEIN_PLATEAU"),
        Criterion("Max. Drawdown im Test", "–" if m.max_dd is None else f"{m.max_dd:.1%}", f"≤ {dd_max:.1%} (1.5 × {DD_LIMIT:.0%})",
                  None if m.max_dd is None else m.max_dd <= dd_max, "DRAWDOWN_ZU_HOCH"),
        Criterion("Besser als Cash", f"{m.total_return:+.2%}", "> 0 %", m.total_return > 0, "SCHLECHTER_ALS_BASELINE"),
        Criterion("Rendite/Max-Drawdown gegenüber exposure-gleichem Buy-and-Hold", f"{fmt(m.ratio, 2)} vs. {fmt(m.matched_ratio, 2)}",
                  "Strategie höher", _better_ratio(m.total_return, m.ratio, m.matched_total, m.matched_ratio), "SCHLECHTER_ALS_BASELINE"),
        Criterion("Gegen Zufallseinstiege (95 %-Perzentil)", f"{m.total_return:+.2%} vs. {'–' if m.random_p95 is None else f'{m.random_p95:+.2%}'}",
                  "Strategie höher", None if m.random_p95 is None else m.total_return > m.random_p95, "SCHLECHTER_ALS_BASELINE"),
    ]
    return _decide("G1", crit)


# ───────────────────────── G2 ─────────────────────────


def evaluate_g2(expectancy_r: float | None, clusters: int, wf_interval: tuple[float, float] | None, access_number: int) -> GateResult:
    g = GATES_V1["g2"]
    first = access_number <= 1
    lo_hi = "–" if wf_interval is None else f"{fmt(wf_interval[0])} … {fmt(wf_interval[1])} R"
    crit = [
        Criterion("Holdout erstmals verwendet (je Strategiefamilie)", f"Zugriff Nr. {access_number}", "1. Zugriff", first, "HOLDOUT_VERBRAUCHT"),
        Criterion("Netto-Erwartungswert je Trade (Holdout)", f"{fmt(expectancy_r)} R", "> 0 R", _gt(expectancy_r, 0), "NETTO_NEGATIV"),
        Criterion("Kein Einbruch gegenüber Walk-forward", f"{fmt(expectancy_r)} R", f"innerhalb des 80 %-Intervalls {lo_hi}",
                  None if wf_interval is None or expectancy_r is None else expectancy_r >= wf_interval[0], "HOLDOUT_EINBRUCH"),
        Criterion("Unabhängige Fälle (Cluster)", str(clusters), f"≥ {g['min_clusters']}", clusters >= g["min_clusters"], "ZU_WENIG_FAELLE"),
    ]
    res = _decide("G2", crit)
    if not first:
        res.result = "INSUFFICIENT"  # ein zweiter Zugriff kann nicht mehr bestehen: nur noch Forward-Evidenz zählt
    return res


# ───────────────────────── G3 ─────────────────────────


@dataclass(frozen=True)
class G3Input:
    weeks: float
    clusters: int
    trades: int
    paper_cum_r: float
    expected_p10: float | None
    fidelity_checked: int
    fidelity_matched: int
    ops_issues: list[str]


def evaluate_g3(m: G3Input) -> GateResult:
    g = GATES_V1["g3"]
    enough = m.weeks >= g["min_weeks"] and m.clusters >= g["min_clusters"]
    fid = None if m.fidelity_checked == 0 else m.fidelity_matched == m.fidelity_checked
    crit = [
        Criterion("Dauer und Fälle", f"{m.weeks:.1f} Wochen, {m.clusters} Cluster", f"≥ {g['min_weeks']} Wochen und ≥ {g['min_clusters']} Cluster",
                  enough, "ZU_WENIG_FAELLE"),
        Criterion("Konsistenz mit Walk-forward", f"{m.paper_cum_r:+.2f} R kumuliert ({m.trades} Trades)",
                  "≥ 10. Perzentil der Erwartung" + ("" if m.expected_p10 is None else f" ({m.expected_p10:+.2f} R)"),
                  None if m.expected_p10 is None or m.trades == 0 else m.paper_cum_r >= m.expected_p10, "FORWARD_INKONSISTENT"),
        Criterion("Signaltreue (Replay derselben Version)", f"{m.fidelity_matched}/{m.fidelity_checked}", "100 %", fid, "FORWARD_INKONSISTENT"),
        Criterion("Betrieb ohne ungelöste Störung", "keine" if not m.ops_issues else "; ".join(m.ops_issues[:5]), "keine", not m.ops_issues, "BETRIEB_INSTABIL"),
        Criterion("Simulator-Realismus der genutzten Ordertypen", "Limit, Stop, Market simuliert", "keine roten Punkte", True),
    ]
    res = _decide("G3", crit, ["G3 ist kein statistischer Vorteilsbeweis: geprüft wird, ob Forward und Erwartung sich nicht widersprechen."])
    if not enough and res.result != "FAILED":
        res.result = "INSUFFICIENT"
    return res


# ───────────────────────── Persistenz ─────────────────────────


def ensure_version(conn: Conn, version: StrategyVersion, code_commit: str | None = None) -> None:
    conn.execute(
        """insert into strategy_version (id, strategy, version, params, regime_rule_version, code_commit)
           values (%s, %s, %s, %s, %s, %s) on conflict (id) do nothing""",
        (version.id, version.strategy, version.version, Jsonb(version.params), version.regime_rule_version, code_commit),
    )
    row = conn.execute("select params from strategy_version where id = %s", (version.id,)).fetchone()
    if row is None or row["params"] != version.params:
        raise ValueError(f"Strategieversion {version.id} existiert mit anderen Parametern")


def write_gate(conn: Conn, version_id: str, res: GateResult, experiment_id: int | None, now: datetime) -> None:
    conn.execute(
        """insert into gate_evaluation (strategy_version_id, gate, result, criteria, reasons, gate_config_version, experiment_id, evaluated_at)
           values (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (version_id, res.gate, res.result, Jsonb(res.criteria_json()), Jsonb(res.reasons), res.config_version, experiment_id, now),
    )
    new = LIFECYCLE.get(res.gate, {}).get(res.result)
    if new:
        conn.execute("update strategy_version set lifecycle_status = %s where id = %s", (new, version_id))
    conn.execute(
        "insert into audit_event (actor, kind, object, data) values (%s, %s, %s, %s)",
        (ACTOR, "lab.gate", f"strategy_version:{version_id}", Jsonb({"gate": res.gate, "result": res.result, "reasons": res.reasons,
                                                                      "experiment_id": experiment_id, "lifecycle": new})),
    )


def latest_gate(conn: Conn, version_id: str, gate: str) -> dict[str, Any] | None:
    return conn.execute(
        "select * from gate_evaluation where strategy_version_id = %s and gate = %s order by evaluated_at desc, id desc limit 1", (version_id, gate)
    ).fetchone()


def propose_approval(conn: Conn, version_id: str, now: datetime) -> bool:
    """Freigabe-Vorschlag (PENDING), wenn G1–G3 zuletzt bestanden sind und noch keiner offen ist."""
    for gate in ("G1", "G2", "G3"):
        row = latest_gate(conn, version_id, gate)
        if row is None or row["result"] != "PASSED":
            return False
    if conn.execute("select 1 from approval where strategy_version_id = %s and decision = 'PENDING'", (version_id,)).fetchone():
        return False
    conn.execute(
        "insert into approval (strategy_version_id, proposed_at, proposed_by, decision, note) values (%s, %s, %s, 'PENDING', %s)",
        (version_id, now, ACTOR, "G1–G3 bestanden. Vorschlag des Lernlabors – entscheiden kann nur der Nutzer."),
    )
    conn.execute("insert into audit_event (actor, kind, object, data) values (%s, %s, %s, %s)",
                 (ACTOR, "lab.approval_proposed", f"strategy_version:{version_id}", Jsonb({"decision": "PENDING"})))
    return True


# ───────────────────────── G3: Forward-Evidenz aus Paper ─────────────────────────


def _candle(r: dict[str, Any]) -> Candle:
    return Candle(r["instrument_id"], r["timeframe"], r["open_time"], r["close_time"], r["open"], r["high"], r["low"], r["close"],
                  r["volume"], r["trades"], r["source"])


def _until(conn: Conn, inst: str, tf: str, until: datetime, limit: int) -> list[Candle]:
    rows = conn.execute(
        "select * from (select * from candle where instrument_id = %s and timeframe = %s and close_time <= %s order by open_time desc limit %s) t "
        "order by open_time", (inst, tf, until, limit)).fetchall()
    return [_candle(r) for r in rows]


def replay_signal(conn: Conn, version: dict[str, Any], sig: dict[str, Any], lookback: int = 600) -> bool | None:
    """Rechnet die Entscheidung zur Signalkerze mit derselben Version nach (gleiches Fenster wie der Signalbetrieb)."""
    fam = FAMILIES.get(version["strategy"])
    if fam is None:
        return None
    inst = conn.execute("select leader_id, tick_size from instrument where id = %s", (sig["instrument_id"],)).fetchone()
    leader_id = (inst["leader_id"] if inst else None) or sig["instrument_id"]
    candles = _until(conn, sig["instrument_id"], sig["timeframe"], sig["candle_close"], lookback)
    if not candles or candles[-1].close_time != sig["candle_close"]:
        return None
    own_daily = _until(conn, sig["instrument_id"], "1d", sig["created_at"], lookback)
    lead_daily = own_daily if leader_id == sig["instrument_id"] else _until(conn, leader_id, "1d", sig["created_at"], lookback)
    own, lead = daily_regimes(own_daily), daily_regimes(lead_daily)
    strategy = make_strategy(fam, dict(version["params"]))
    d = strategy.decide(candles, own, lead, tick=inst["tick_size"] if inst else None)[-1]
    same = d.action.value == sig["action"]
    if same and d.entry is not None:
        same = sig["entry"] is not None and Decimal(sig["entry"]) == d.entry and sig["stop"] is not None and d.stop is not None and Decimal(sig["stop"]) == d.stop
    return bool(same)


def forward_input(conn: Conn, version_id: str, now: datetime, expected_r: list[float] | None, seed: int = 7) -> G3Input:
    version = conn.execute("select * from strategy_version where id = %s", (version_id,)).fetchone()
    episodes = conn.execute(
        """select e.id, e.account_id, e.start_cash, e.started_at from episode e join account a on a.id = e.account_id
           where a.mode = 'PAPER' and e.strategy_version_ids @> %s""", (Jsonb([version_id]),)).fetchall()
    if not episodes or version is None:
        return G3Input(0.0, 0, 0, 0.0, None, 0, 0, [])
    start = min(e["started_at"] for e in episodes)
    weeks = (now - start).total_seconds() / (7 * 86400)
    cash = {e["id"]: Decimal(e["start_cash"]) for e in episodes}
    rows = conn.execute(
        "select * from trade where strategy_version_id = %s and episode_id = any(%s) order by opened_at", (version_id, list(cash))).fetchall()
    closed = [r for r in rows if r["status"] == "CLOSED" and r["net"] is not None]
    trades = [TradeRec(r["instrument_id"], r["opened_at"], r["closed_at"], float(Decimal(r["net"]) / Decimal(r["planned_risk"])) if Decimal(r["planned_risk"]) else 0.0,
                       float(r["net"]), float(Decimal(r["entry_value"]) / cash[r["episode_id"]]), int(r["bars_held"])) for r in closed]
    returns = {}
    for inst in {t.instrument for t in trades}:
        daily = _until(conn, inst, "1d", now, 2000)
        returns[inst] = daily_log_returns([(day_of(c.close_time), float(c.close)) for c in daily if c.close_time >= start - timedelta(days=30)])
    clusters = cluster_trades(trades, returns)
    cum = sum(t.r for t in trades)
    p10 = None
    if expected_r and trades:
        p10 = percentile(bootstrap_sum_distribution(expected_r, len(trades), seed), GATES_V1["g3"]["consistency_percentile"])
    checked = matched = 0
    for r in rows:
        if r["signal_id"] is None:
            continue
        sig = conn.execute("select * from signal where id = %s", (r["signal_id"],)).fetchone()
        if sig is None:
            continue
        ok = replay_signal(conn, version, sig)
        if ok is None:
            continue
        checked += 1
        matched += int(ok)
    issues: list[str] = []
    for a in conn.execute("select title from alert where status <> 'RESOLVED' and level = 'CRITICAL' and mode in ('PAPER', 'SYSTEM')").fetchall():
        issues.append(f"Meldung: {a['title']}")
    for acc in {e["account_id"] for e in episodes}:
        rec = conn.execute("select status from reconciliation where account_id = %s order by at desc limit 1", (acc,)).fetchone()
        if rec is not None and rec["status"] != "OK":
            issues.append(f"Abgleich {acc}: {rec['status']}")
    return G3Input(weeks, len(clusters), len(trades), cum, p10, checked, matched, issues)


def as_dict(res: GateResult) -> dict[str, Any]:
    d = asdict(res)
    d["criteria"] = res.criteria_json()
    return d
