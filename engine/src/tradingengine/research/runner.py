"""Experiment-Warteschlange des Lernlabors (docs/04, 5.1–5.5).

Ein Experiment läuft in kleinen Arbeitseinheiten über mehrere zustandslose Aufrufe (Vercel-Funktion, je Aufruf
höchstens `time_budget_s`): Der Fortschritt steht in `experiment.progress`, jede getestete Variante sofort in
`experiment_trial` (append-only, zählt auch bei Abbruch). Jeder Lauf endet mit genau einem Ergebnis
KANDIDAT | KEIN_BELASTBARER_FORTSCHRITT | ZU_WENIG_DATEN | ABGELEHNT | ABGEBROCHEN.

Arten:
- BACKTEST: eine Version über die ganzen Entwicklungsdaten, Baselines, Sensitivitäten (Kosten × 1.5 / × 2,
  Slippage +50 %, Einstieg eine Kerze später). Ein Backtest ist nie ein Kandidat.
- WALK_FORWARD: G1 für eine Version; zusätzlich laufen die ±20 %-Nachbarn (Plateau, PBO) – sie zählen als Varianten.
- OPTIMIZE: begrenztes Raster (≤ 50 Varianten, ≤ 3 freie Parameter, innerhalb der Grenzen aus docs/04), Auswahl je
  Fold nur auf dem Trainingsfenster; bewertet wird die Out-of-sample-Kette der Auswahl (G1 mit DSR/PBO/Plateau über
  ALLE Varianten) und gepaart gegen den Champion auf denselben Testfenstern. Eine gewählte Variante mit anderen
  Parametern wird als NEUE Strategieversion angelegt; bestehende Versionen bleiben unverändert.
- HOLDOUT: G2, nur nach bestandenem G1; jeder Zugriff wird protokolliert.
- METALABEL: Lernweg 2 (metalabel.py).

Budgets (Start, überschreibbar über `setting` `lab.budget`): 2 Läufe je Woche und Strategiefamilie, 50 Varianten
je Lauf, 2 h Rechenzeit je Lauf (kumuliert in `cpu_seconds`, gemessen als Wanduhrzeit der Arbeitseinheiten),
20 h je Monat als Kostenlimit-Ersatz.
"""

from __future__ import annotations

import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from ..core.costs import KRAKEN_SPOT_TIER1
from ..core.regime import Regime
from . import baselines, metalabel
from .data import (
    HOLDOUT_CONSUMED_TEXT,
    HOLDOUT_VERSION,
    LEADER,
    Conn,
    Dataset,
    data_start,
    development_end,
    holdout_access_count,
    holdout_window,
    load_dataset,
    record_holdout_access,
)
from .gates import (
    ACTOR,
    GATES_V1,
    G1Input,
    GateResult,
    ensure_version,
    evaluate_g1,
    evaluate_g2,
    evaluate_g3,
    forward_input,
    latest_gate,
    propose_approval,
    write_gate,
)
from .stats import (
    bootstrap_sum_distribution,
    cluster_bootstrap_mean,
    cluster_trades,
    cluster_values,
    deflated_sharpe,
    fmt,
    max_drawdown_additive,
    mean,
    paired_bootstrap,
    pbo_cscv,
    percentile,
    plateau,
    sharpe,
)
from .variants import Family, canonical, family, full_params, grid, max_hold, neighbours, version_id, version_of
from .walkforward import (
    COST_SCENARIOS,
    START_CASH,
    Fold,
    FoldOut,
    Prepared,
    WFConfig,
    day_index,
    evaluate_fold,
    make_folds,
    objective,
    prepare,
    regime_phases,
    vector,
)

log = logging.getLogger(__name__)

KINDS = ("BACKTEST", "WALK_FORWARD", "OPTIMIZE", "HOLDOUT", "METALABEL")
OUTCOMES = ("KANDIDAT", "KEIN_BELASTBARER_FORTSCHRITT", "ZU_WENIG_DATEN", "ABGELEHNT", "ABGEBROCHEN")
DEFAULT_BUDGET: dict[str, float] = {"runs_per_week": 2, "max_variants": 50, "max_seconds_per_run": 7200, "max_seconds_per_month": 72000}
DEFAULT_INSTRUMENTS = ["BITSTAMP:BTC/USD", "BITSTAMP:ETH/USD"]
LOCK_KEY = 7_311_204  # pg_advisory_lock: nie zwei Laborschritte gleichzeitig
HOLDOUT_WARMUP_DAYS = 450
SENSITIVITIES = ["Kosten × 1.5", "Kosten × 2", "Slippage +50 %", "Einstieg eine Kerze später"]
WARNINGS = [
    "Forschungsdaten von Bitstamp (Kursverlauf als Stellvertreter); Handel und Kosten gehen von Kraken aus (kraken-spot-tier1).",
    "Universum nach heutiger Auswahl liquider Paare (Survivorship-Bias, für BTC/ETH klein).",
    "Jedes Fenster und Instrument ist ein eigenes Konto mit 10 000 USD; keine Portfolio-Interaktion.",
    "Simulation auf Kerzen: Stop vor Ziel in derselben Kerze (konservativ); die umgekehrte Annahme ist nicht gerechnet.",
]

Clock = Callable[[], float]


class Rejected(ValueError):
    pass


# ───────────────────────── Budget und Anlage ─────────────────────────


def budget(conn: Conn) -> dict[str, float]:
    out = dict(DEFAULT_BUDGET)
    row = conn.execute("select value from setting where key = 'lab.budget'").fetchone()
    if row and isinstance(row["value"], dict):
        for k, v in row["value"].items():
            if k in out and isinstance(v, int | float):
                out[k] = float(v)
    return out


def _version_params(conn: Conn, fam: Family, version: str | None) -> tuple[str, dict[str, Any]]:
    if version is None or version == fam.base.id:
        return fam.base.id, full_params(fam, {})
    row = conn.execute("select strategy, params from strategy_version where id = %s", (version,)).fetchone()
    if row is None or row["strategy"] != fam.name:
        raise Rejected(f"Strategieversion {version} unbekannt oder nicht aus der Familie {fam.name}")
    params = full_params(fam, dict(row["params"]))
    return version_id(fam, params), params


def plan_variants(fam: Family, kind: str, params: dict[str, Any], config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    if kind == "WALK_FORWARD":
        nb = neighbours(fam, params, fam.free)
        return [params, *nb.values()], ["gewählt", *nb.keys()]
    if kind == "OPTIMIZE":
        free = tuple(config.get("free_params") or fam.free)
        variants = grid(fam, free, config.get("grid"))
        return variants, [canonical({k: v[k] for k in free}) for v in variants]
    return [params], ["gewählt"]


def resolve(conn: Conn, kind: str, strategy: str, config: dict[str, Any] | None, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
    if kind not in KINDS:
        raise Rejected(f"Unbekannte Experimentart {kind}")
    try:
        fam = family(strategy)
    except ValueError as exc:
        raise Rejected(str(exc)) from exc
    config = dict(config or {})
    timeframe = str(config.get("timeframe", "1d"))
    if timeframe not in ("1d", "4h"):
        raise Rejected("Zeitebene muss 1d oder 4h sein")
    instruments = [str(i) for i in config.get("instruments") or DEFAULT_INSTRUMENTS]
    known = {r["id"] for r in conn.execute("select id from instrument where id = any(%s)", (instruments,)).fetchall()}
    missing = [i for i in instruments if i not in known]
    if missing:
        raise Rejected(f"Instrument unbekannt: {', '.join(missing)}")
    try:
        vid, params = _version_params(conn, fam, config.get("version_id"))
        variants, labels = plan_variants(fam, kind, params, config)
        wf = WFConfig.from_json(config.get("wf"))
    except (ValueError, TypeError) as exc:
        raise Rejected(str(exc)) from exc
    resolved = {
        "instruments": sorted(instruments),
        "timeframe": timeframe,
        "seed": int(config.get("seed", 42)),
        "wf": wf.to_json(),
        "version_id": vid,
        "cost_model": KRAKEN_SPOT_TIER1.version,
        "random_draws": int(config.get("random_draws", 1000)),
        "bootstrap": int(config.get("bootstrap", 2000)),
        "as_of": str(config.get("as_of") or now.isoformat()),
        "holdout": HOLDOUT_VERSION,
        "gates": GATES_V1["version"],
    }
    if kind == "OPTIMIZE":
        resolved["free_params"] = list(config.get("free_params") or fam.free)
    progress = {"phase": "init", "variants": variants, "labels": labels, "next": 0}
    return resolved, vid, progress


def create_experiment(conn: Conn, kind: str, strategy: str, config: dict[str, Any] | None, issued_by: str,
                      now: datetime | None = None) -> tuple[int | None, str | None]:
    """Legt ein Experiment an (PLANNED). Rückgabe (id, None) oder (None, Ablehnungsgrund)."""
    now = now or datetime.now(UTC)
    try:
        resolved, vid, progress = resolve(conn, kind, strategy, config, now)
    except Rejected as exc:
        return None, str(exc)
    b = budget(conn)
    runs = conn.execute("select count(*) as n from experiment where strategy = %s and created_at >= %s",
                        (strategy, now - timedelta(days=7))).fetchone()
    if runs and runs["n"] >= b["runs_per_week"]:
        return None, f"Budget: höchstens {int(b['runs_per_week'])} Läufe je Woche für {strategy} (bereits {runs['n']})"
    if len(progress["variants"]) > b["max_variants"]:
        return None, f"Budget: {len(progress['variants'])} Varianten > höchstens {int(b['max_variants'])} je Lauf"
    month = conn.execute("select coalesce(sum(cpu_seconds), 0) as s from experiment where created_at >= %s",
                         (now - timedelta(days=30),)).fetchone()
    if month and float(month["s"]) >= b["max_seconds_per_month"]:
        return None, f"Budget: Rechenzeit der letzten 30 Tage ausgeschöpft ({float(month['s']):.0f} s)"
    if kind == "HOLDOUT":
        g1 = latest_gate(conn, vid, "G1")
        if g1 is None or g1["result"] != "PASSED":
            return None, f"Holdout erst nach bestandenem G1 für {vid} (der Holdout ist je Familie nur einmal verwendbar)"
    hypothesis = str((config or {}).get("hypothesis") or
                     f"{vid} hat nach Kraken-Kosten einen positiven Netto-Erwartungswert ausserhalb der Stichprobe ({kind}).")
    row = conn.execute(
        """insert into experiment (created_at, kind, strategy, strategy_version_id, hypothesis, config, status, progress, issued_by)
           values (%s, %s, %s, %s, %s, %s, 'PLANNED', %s, %s) returning id""",
        (now, kind, strategy, vid, hypothesis, Jsonb(resolved), Jsonb(progress), issued_by),
    ).fetchone()
    assert row is not None
    conn.execute("insert into audit_event (actor, kind, object, data) values (%s, %s, %s, %s)",
                 (ACTOR, "lab.experiment_created", f"experiment:{row['id']}", Jsonb({"kind": kind, "strategy": strategy, "issued_by": issued_by,
                                                                                     "variants": len(progress["variants"])})))
    return int(row["id"]), None


# ───────────────────────── Steuerung ─────────────────────────


def pause(conn: Conn, experiment_id: int) -> str | None:
    cur = conn.execute("update experiment set status = 'PAUSED' where id = %s and status in ('PLANNED', 'RUNNING')", (experiment_id,))
    return None if cur.rowcount == 1 else "Experiment nicht geplant oder laufend"


def resume(conn: Conn, experiment_id: int) -> str | None:
    cur = conn.execute(
        "update experiment set status = case when started_at is null then 'PLANNED' else 'RUNNING' end where id = %s and status = 'PAUSED'",
        (experiment_id,))
    return None if cur.rowcount == 1 else "Experiment ist nicht pausiert"


def abort(conn: Conn, experiment_id: int, now: datetime, reason: str = "durch den Nutzer abgebrochen") -> str | None:
    row = conn.execute("select * from experiment where id = %s", (experiment_id,)).fetchone()
    if row is None or row["status"] in ("DONE", "ABORTED"):
        return "Experiment existiert nicht oder ist abgeschlossen"
    _finish_aborted(conn, row, now, reason)
    return None


def _finish_aborted(conn: Conn, exp: dict[str, Any], now: datetime, reason: str) -> None:
    n = exp["variants_tested"]
    summary = {
        "headline": f"Abgebrochen: {reason}. Alle {n} bis dahin getesteten Varianten bleiben im Protokoll gezählt.",
        "metrics": {"Getestete Varianten": {"value": str(n)}},
        "baselines": [], "sensitivity": [], "equity": [], "folds": [], "reasons": [], "warnings": [reason],
    }
    conn.execute(
        """update experiment set status = 'ABORTED', outcome = 'ABGEBROCHEN', summary = %s, finished_at = %s
           where id = %s and status not in ('DONE', 'ABORTED')""", (Jsonb(summary), now, exp["id"]))
    conn.execute("insert into audit_event (actor, kind, object, data) values (%s, %s, %s, %s)",
                 (ACTOR, "lab.experiment_aborted", f"experiment:{exp['id']}", Jsonb({"reason": reason, "variants_tested": n})))


# ───────────────────────── Arbeitsschritt ─────────────────────────


@dataclass
class Ctx:
    exp: dict[str, Any]
    fam: Family
    cfg: dict[str, Any]
    progress: dict[str, Any]
    ds: Dataset | None = None
    prep: Prepared | None = None
    folds: list[Fold] | None = None


def _dataset(conn: Conn, ctx: Ctx) -> tuple[Dataset, Prepared]:
    if ctx.ds is None or ctx.prep is None:
        p = ctx.progress
        start, end = datetime.fromisoformat(p["data_from"]), datetime.fromisoformat(p["data_to"])
        ctx.ds = load_dataset(conn, ctx.cfg["instruments"], ctx.cfg["timeframe"], start, end, datetime.fromisoformat(ctx.cfg["as_of"]))
        if ctx.exp["dataset_hash"] and ctx.ds.hash != ctx.exp["dataset_hash"]:
            raise DatasetChanged(f"Datensatz hat sich verändert (Hash {ctx.ds.hash[:12]} ≠ {ctx.exp['dataset_hash'][:12]})")
        ctx.prep = prepare(ctx.ds)
    return ctx.ds, ctx.prep


class DatasetChanged(RuntimeError):
    pass


def _folds(ctx: Ctx) -> list[Fold]:
    if ctx.folds is None:
        ctx.folds = [Fold.from_json(f) for f in ctx.progress["folds"]]
    return ctx.folds


def _save(conn: Conn, ctx: Ctx, elapsed: float) -> None:
    ctx.exp["cpu_seconds"] = Decimal(str(ctx.exp["cpu_seconds"])) + Decimal(f"{elapsed:.3f}")
    conn.execute("update experiment set progress = %s, cpu_seconds = %s, variants_tested = %s where id = %s",
                 (Jsonb(ctx.progress), ctx.exp["cpu_seconds"], ctx.exp["variants_tested"], ctx.exp["id"]))


def run_step(conn: Conn, now: datetime, time_budget_s: float = 240.0, clock: Clock = time.perf_counter) -> dict[str, Any]:
    """Ein Arbeitsschritt: Forward-Prüfungen (G3), dann laufende/geplante Experimente bis zum Zeitbudget."""
    t0 = clock()
    got = conn.execute("select pg_try_advisory_lock(%s) as ok", (LOCK_KEY,)).fetchone()
    if not got or not got["ok"]:
        return {"busy": True}
    try:
        out: dict[str, Any] = {"forward": forward_checks(conn, now), "experiments": []}
        seen: set[int] = set()
        while clock() - t0 < time_budget_s:
            exp = conn.execute(
                "select * from experiment where status in ('RUNNING', 'PLANNED') and not (id = any(%s)) "
                "order by (status = 'RUNNING') desc, created_at, id limit 1", (list(seen),)).fetchone()
            if exp is None:
                break
            seen.add(int(exp["id"]))
            state = _advance(conn, exp, now, t0 + time_budget_s, clock)
            out["experiments"].append(state)
            if state["status"] == "RUNNING":
                break  # Zeitbudget erschöpft; nächster Aufruf macht weiter
        return out
    finally:
        conn.execute("select pg_advisory_unlock(%s)", (LOCK_KEY,))


def _advance(conn: Conn, exp: dict[str, Any], now: datetime, deadline: float, clock: Clock) -> dict[str, Any]:
    b = budget(conn)
    ctx = Ctx(exp, family(exp["strategy"]), dict(exp["config"]), dict(exp["progress"] or {}))
    if exp["status"] == "PLANNED":
        conn.execute("update experiment set status = 'RUNNING', started_at = %s where id = %s", (now, exp["id"]))
        exp["status"] = "RUNNING"
    units = 0
    while True:
        if units:
            cur = conn.execute("select status from experiment where id = %s", (exp["id"],)).fetchone()
            if cur is None or cur["status"] != "RUNNING":  # pausiert oder abgebrochen (Befehl aus der Web-App)
                return {"id": exp["id"], "status": cur["status"] if cur else "GONE"}
        if float(exp["cpu_seconds"]) >= b["max_seconds_per_run"]:
            _finish_aborted(conn, exp, now, f"Rechenzeit-Budget von {b['max_seconds_per_run']:.0f} s je Lauf erreicht")
            return {"id": exp["id"], "status": "ABORTED", "outcome": "ABGEBROCHEN"}
        if exp["variants_tested"] > b["max_variants"]:
            _finish_aborted(conn, exp, now, f"Varianten-Budget von {b['max_variants']:.0f} je Lauf überschritten")
            return {"id": exp["id"], "status": "ABORTED", "outcome": "ABGEBROCHEN"}
        last_unit = float(ctx.progress.get("last_unit_s", 0.0))
        if clock() > deadline or (units and clock() + last_unit > deadline):
            return {"id": exp["id"], "status": "RUNNING", "phase": ctx.progress.get("phase"), "variants_tested": exp["variants_tested"]}
        started = clock()
        try:
            done = _unit(conn, ctx, now)
        except DatasetChanged as exc:
            _finish_aborted(conn, exp, now, str(exc))
            return {"id": exp["id"], "status": "ABORTED", "outcome": "ABGEBROCHEN"}
        except Exception as exc:  # ein fehlerhaftes Experiment darf die Warteschlange nicht blockieren
            log.exception("Experiment %s fehlgeschlagen", exp["id"])
            _finish_aborted(conn, exp, now, f"interner Fehler ({type(exc).__name__})")
            return {"id": exp["id"], "status": "ABORTED", "outcome": "ABGEBROCHEN"}
        elapsed = clock() - started
        units += 1
        ctx.progress["last_unit_s"] = round(elapsed, 3)
        if done is not None:
            _save(conn, ctx, elapsed)
            _finish(conn, ctx, now, *done)
            return {"id": exp["id"], "status": "DONE", "outcome": done[0]}
        _save(conn, ctx, elapsed)


def _unit(conn: Conn, ctx: Ctx, now: datetime) -> tuple[str, dict[str, Any]] | None:
    """Eine Arbeitseinheit. Rückgabe (Ergebnis, Summary) am Ende, sonst None."""
    phase = ctx.progress["phase"]
    if phase == "init":
        return _init(conn, ctx, now)
    if phase == "variants":
        _variant(conn, ctx)
        return None
    if phase == "select":
        _select(conn, ctx)
        return None
    if phase == "sensitivity":
        _sensitivity(conn, ctx)
        return None
    if phase == "final":
        return _final(conn, ctx, now)
    raise RuntimeError(f"Unbekannte Phase {phase}")


def _init(conn: Conn, ctx: Ctx, now: datetime) -> tuple[str, dict[str, Any]] | None:
    kind, cfg = ctx.exp["kind"], ctx.cfg
    as_of = datetime.fromisoformat(cfg["as_of"])
    if kind == "HOLDOUT":
        h0, h1 = holdout_window()
        start, end = h0 - timedelta(days=HOLDOUT_WARMUP_DAYS), h1
    else:
        first = data_start(conn, cfg["instruments"], cfg["timeframe"])
        if first is None:
            return "ZU_WENIG_DATEN", _short_summary("Keine Forschungsdaten für diese Instrumente vorhanden.")
        start, end = first, development_end()
    ds = load_dataset(conn, cfg["instruments"], cfg["timeframe"], start, end, as_of)
    empty = [i for i in ds.instruments if not ds.candles[i]]
    ctx.progress.update({"data_from": start.isoformat(), "data_to": end.isoformat()})
    conn.execute("update experiment set dataset_hash = %s, data_from = %s, data_to = %s where id = %s", (ds.hash, start, end, ctx.exp["id"]))
    ctx.exp["dataset_hash"] = ds.hash
    if empty:
        return "ZU_WENIG_DATEN", _short_summary(f"Keine Kerzen für {', '.join(empty)} im Zeitraum.")
    wf = WFConfig.from_json(cfg["wf"])
    hold = max(max_hold(v) for v in ctx.progress["variants"])
    if kind in ("WALK_FORWARD", "OPTIMIZE", "METALABEL"):
        folds = make_folds(start, end, cfg["timeframe"], hold, wf)
        if len(folds) < 2:
            return "ZU_WENIG_DATEN", _short_summary(f"Zu kurze Historie für Walk-forward ({len(folds)} Testfenster; nötig ≥ 2).")
    elif kind == "HOLDOUT":
        h0, h1 = holdout_window()
        folds = [Fold(0, start, h0, h0, h1, h1)]
        n = record_holdout_access(conn, ctx.exp["strategy"], int(ctx.exp["id"]), now)
        ctx.progress["holdout_access"] = n
    else:
        folds = [Fold(0, start, start, start, end, end)]
    ctx.progress["folds"] = [f.to_json() for f in folds]
    ctx.progress["phase"] = "variants"
    ctx.ds, ctx.prep = ds, prepare(ds)
    return None


def _variant(conn: Conn, ctx: Ctx) -> None:
    ds, prep = _dataset(conn, ctx)
    i = int(ctx.progress["next"])
    params = ctx.progress["variants"][i]
    kind = ctx.exp["kind"]
    with_train = kind in ("OPTIMIZE", "METALABEL")
    wf = WFConfig.from_json(ctx.cfg["wf"])
    folds = _folds(ctx)
    outs = [evaluate_fold(prep, ctx.fam, params, f, KRAKEN_SPOT_TIER1, wf, with_train) for f in folds]
    metrics = series_metrics(outs, day_index(folds), prep, ctx.cfg["seed"], ctx.cfg["bootstrap"])
    if kind == "OPTIMIZE":
        metrics["train_objective"] = [objective(fo.train_trades or [], prep.returns) for fo in outs]
    conn.execute(
        "insert into experiment_trial (experiment_id, variant, metrics, folds) values (%s, %s, %s, %s)",
        (ctx.exp["id"], Jsonb({"index": i, "label": ctx.progress["labels"][i], "params": params, "version_id": version_id(ctx.fam, params)}),
         Jsonb(metrics), Jsonb([fo.to_json() for fo in outs])),
    )
    ctx.exp["variants_tested"] += 1
    ctx.progress["next"] = i + 1
    if i + 1 >= len(ctx.progress["variants"]):
        ctx.progress["phase"] = "select" if kind == "OPTIMIZE" else ("sensitivity" if kind in ("BACKTEST", "WALK_FORWARD") else "final")


def series_metrics(outs: list[FoldOut], index: list[Any], prep: Prepared, seed: int, n_boot: int) -> dict[str, Any]:
    trades = [t for fo in outs for t in fo.trades]
    deltas: dict[Any, float] = {}
    for fo in outs:
        for d, v in fo.deltas.items():
            deltas[d] = deltas.get(d, 0.0) + v
    vec = vector(deltas, index)
    clusters = cluster_trades(trades, prep.returns)
    ci = cluster_bootstrap_mean(cluster_values(trades, clusters), seed, n_boot)
    sr = sharpe(vec)
    return {
        "trades": len(trades),
        "clusters": len(clusters),
        "expectancy_r": mean([t.r for t in trades]),
        "expectancy_ci": list(ci) if ci else None,
        "net_return": sum(vec),
        "net_usd": f"{sum(Decimal(str(t.net)) for t in trades):.2f}",
        "sharpe_d": sr,
        "sharpe_ann": None if sr is None else sr * math.sqrt(365),
        "max_dd": max_drawdown_additive(vec),
        "days": len(vec),
    }


def _trials(conn: Conn, experiment_id: int) -> list[dict[str, Any]]:
    return conn.execute("select variant, metrics, folds from experiment_trial where experiment_id = %s order by id", (experiment_id,)).fetchall()


def _fold_outs(trial: dict[str, Any]) -> list[FoldOut]:
    return [FoldOut.from_json(f) for f in trial["folds"] or []]


def _select(conn: Conn, ctx: Ctx) -> None:
    """Auswahl je Fold ausschliesslich nach dem Trainingsziel (keine Testdaten)."""
    trials = _trials(conn, ctx.exp["id"])
    folds = _folds(ctx)
    selection: list[int | None] = []
    for k in range(len(folds)):
        best: tuple[float, int] | None = None
        for t in trials:
            obj = (t["metrics"].get("train_objective") or [None] * len(folds))[k]
            if obj is not None and (best is None or obj > best[0]):
                best = (float(obj), int(t["variant"]["index"]))
        selection.append(None if best is None else best[1])
    ctx.progress["selection"] = selection
    final = next((s for s in reversed(selection) if s is not None), 0)
    ctx.progress["chosen"] = final
    ctx.progress["phase"] = "sensitivity"


def _per_fold_params(ctx: Ctx) -> list[dict[str, Any] | None]:
    folds = _folds(ctx)
    variants = ctx.progress["variants"]
    if ctx.exp["kind"] == "OPTIMIZE":
        return [None if s is None else variants[s] for s in ctx.progress["selection"]]
    return [variants[0]] * len(folds)


def _sensitivity(conn: Conn, ctx: Ctx) -> None:
    done = ctx.progress.setdefault("sensitivity", [])
    scenario = SENSITIVITIES[len(done)]
    _, prep = _dataset(conn, ctx)
    cost, delay = COST_SCENARIOS[scenario]
    wf = WFConfig.from_json(ctx.cfg["wf"])
    outs = [evaluate_fold(prep, ctx.fam, p, f, cost, wf, delay_bars=delay) for f, p in zip(_folds(ctx), _per_fold_params(ctx), strict=True)
            if p is not None]
    trades = [t for fo in outs for t in fo.trades]
    done.append({"scenario": scenario, "trades": len(trades), "net": f"{sum(Decimal(str(t.net)) for t in trades):.2f}",
                 "expectancy_r": mean([t.r for t in trades]), "cost_model": cost.version})
    if len(done) >= len(SENSITIVITIES):
        ctx.progress["phase"] = "final"


# ───────────────────────── Abschluss ─────────────────────────


def _short_summary(headline: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return {"headline": headline, "metrics": {}, "baselines": [], "sensitivity": [], "equity": [], "folds": [], "reasons": [],
            "warnings": (warnings or []) + WARNINGS[:2]}


def _finish(conn: Conn, ctx: Ctx, now: datetime, outcome: str, summary: dict[str, Any]) -> None:
    assert outcome in OUTCOMES
    conn.execute(
        """update experiment set status = 'DONE', outcome = %s, summary = %s, progress = %s, finished_at = %s, strategy_version_id = %s
           where id = %s""",
        (outcome, Jsonb(summary), Jsonb(ctx.progress), now, ctx.progress.get("final", {}).get("version_id", ctx.exp["strategy_version_id"]),
         ctx.exp["id"]),
    )
    conn.execute("insert into audit_event (actor, kind, object, data) values (%s, %s, %s, %s)",
                 (ACTOR, "lab.experiment_done", f"experiment:{ctx.exp['id']}", Jsonb({"outcome": outcome, "variants_tested": ctx.exp["variants_tested"]})))


def _combined(trials: list[dict[str, Any]], per_fold: list[int | None]) -> list[FoldOut]:
    """Fold-Ergebnisse der je Fold gewählten Variante (OOS-Kette der Auswahl)."""
    by_index = {int(t["variant"]["index"]): _fold_outs(t) for t in trials}
    out = []
    for k, s in enumerate(per_fold):
        if s is not None:
            out.append(by_index[s][k])
    return out


def _family_trials(conn: Conn, strategy: str) -> tuple[int, float]:
    rows = conn.execute(
        "select t.metrics->>'sharpe_d' as s from experiment_trial t join experiment e on e.id = t.experiment_id where e.strategy = %s",
        (strategy,)).fetchall()
    srs = [float(r["s"]) for r in rows if r["s"] is not None]
    n = len(rows)
    if len(srs) < 2:
        return n, 0.0
    m = sum(srs) / len(srs)
    return n, sum((s - m) ** 2 for s in srs) / len(srs)


def _equity(index: list[Any], vec: list[float]) -> list[list[str]]:
    if not index:
        return []
    step = max(1, math.ceil(len(index) / 500))
    eq = 0.0
    out: list[list[str]] = []
    for i, (d, r) in enumerate(zip(index, vec, strict=True)):
        eq += r
        if i % step == 0 or i == len(index) - 1:
            out.append([datetime(d.year, d.month, d.day, tzinfo=UTC).isoformat(), f"{START_CASH * (1 + Decimal(str(round(eq, 10)))):.2f}"])
    return out[:500]


def _pct(x: float | None, digits: int = 1) -> str:
    return "–" if x is None else f"{x * 100:.{digits}f}"


def _metric(value: str, unit: str | None = None, ci: list[str] | None = None, note: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"value": value}
    if unit:
        out["unit"] = unit
    if ci:
        out["ci"] = ci
    if note:
        out["note"] = note
    return out


def _final(conn: Conn, ctx: Ctx, now: datetime) -> tuple[str, dict[str, Any]]:
    kind = ctx.exp["kind"]
    _, prep = _dataset(conn, ctx)
    folds = _folds(ctx)
    index = day_index(folds)
    trials = _trials(conn, ctx.exp["id"])
    seed, n_boot = int(ctx.cfg["seed"]), int(ctx.cfg["bootstrap"])
    variants = ctx.progress["variants"]

    if kind == "OPTIMIZE":
        selection = ctx.progress["selection"]
        outs = _combined(trials, selection)
        eval_folds = [f for f, s in zip(folds, selection, strict=True) if s is not None]
        chosen = int(ctx.progress["chosen"])
    else:
        outs = _fold_outs(trials[0])
        eval_folds = folds
        chosen = 0
    chosen_params = variants[chosen]
    vid = version_id(ctx.fam, chosen_params)
    m = series_metrics(outs, index, prep, seed, n_boot)
    trades = [t for fo in outs for t in fo.trades]
    deltas: dict[Any, float] = {}
    for fo in outs:
        for d, v in fo.deltas.items():
            deltas[d] = deltas.get(d, 0.0) + v
    vec = vector(deltas, index)
    clusters = cluster_trades(trades, prep.returns)
    cluster_r = [sum(trades[i].r for i in c) for c in clusters]
    ctx.progress["final"] = {"version_id": vid, "oos_trade_r": [round(t.r, 8) for t in trades], "oos_cluster_r": [round(x, 8) for x in cluster_r]}

    sens = [{"scenario": "Basis", "trades": m["trades"], "net": m["net_usd"], "expectancy_r": fmt(m["expectancy_r"])}]
    for s in ctx.progress.get("sensitivity", []):
        sens.append({"scenario": s["scenario"], "trades": s["trades"], "net": s["net"], "expectancy_r": None if s["expectancy_r"] is None else fmt(s["expectancy_r"])})
    cost15 = next((s["expectancy_r"] for s in ctx.progress.get("sensitivity", []) if s["scenario"] == "Kosten × 1.5"), None)

    bl = baselines.compute(prep, eval_folds or folds, outs, index, KRAKEN_SPOT_TIER1, int(ctx.cfg["random_draws"]), seed)
    base_rows = [
        {"name": "Cash", "metric": "Rendite", "value": "0.00 %"},
        {"name": "Buy-and-Hold", "metric": "Rendite / Max-Drawdown", "value": f"{bl.buy_hold.total:+.2%} / {bl.buy_hold.max_dd:.1%}"},
        {"name": f"Buy-and-Hold, Exposure {bl.exposure:.0%}", "metric": "Rendite / Max-Drawdown",
         "value": f"{bl.matched.total:+.2%} / {bl.matched.max_dd:.1%}"},
        {"name": f"Zufallseinstiege ({bl.draws} Ziehungen)", "metric": "Rendite 95 %-Perzentil / Median",
         "value": f"{bl.random_p95:+.2%} / {bl.random_median:+.2%}"},
    ]
    n_trials, var_trials = _family_trials(conn, ctx.exp["strategy"])
    dsr = deflated_sharpe(vec, max(1, n_trials), var_trials) if trades else None

    metrics: dict[str, Any] = {
        "Trades": _metric(str(m["trades"])),
        "Unabhängige Fälle": _metric(str(m["clusters"]), note="Cluster: zeitlich überlappend und gleiches Instrument oder ρ > 0.7"),
        "Netto-Erwartungswert": _metric(fmt(m["expectancy_r"]), "R", [fmt(x) for x in m["expectancy_ci"]] if m["expectancy_ci"] else None,
                                         "95 %-Intervall aus Block-Bootstrap über Cluster"),
        "Netto-Ergebnis": _metric(m["net_usd"], "USD", note="Summe aller Konten (je Fenster und Instrument 10 000 USD Start)"),
        "Rendite": _metric(_pct(m["net_return"], 2), "%", note="bezogen auf das Startkapital, Mittel über die Instrumente"),
        "Sharpe (annualisiert)": _metric(fmt(m["sharpe_ann"], 2), note="Tagesrenditen × √365"),
        "Max. Drawdown": _metric(_pct(m["max_dd"]), "%"),
        "Exposure": _metric(_pct(bl.exposure), "%"),
        "Deflated Sharpe Ratio": _metric(fmt(dsr), note=f"N = {n_trials} getestete Varianten der Familie"),
        "Testfenster": _metric(str(len(eval_folds))),
        "Varianten in diesem Lauf": _metric(str(ctx.exp["variants_tested"])),
    }
    fold_rows = []
    for f in folds:
        f_out = next((o for o in outs if o.fold == f.index), None)
        n = 0 if f_out is None else len(f_out.trades)
        net = Decimal(0) if f_out is None else sum((Decimal(str(t.net)) for t in f_out.trades), Decimal(0))
        fold_rows.append({"train": [f.train_start.isoformat(), f.train_end.isoformat()], "test": [f.test_start.isoformat(), f.test_end.isoformat()],
                          "trades": n, "net": f"{net:.2f}"})
    summary: dict[str, Any] = {"headline": "", "metrics": metrics, "baselines": base_rows, "sensitivity": sens, "equity": _equity(index, vec),
                               "folds": fold_rows, "reasons": [], "warnings": list(WARNINGS)}


    if kind == "BACKTEST":
        summary["warnings"].insert(0, "Ein Backtest ist kein Qualitätsnachweis: keine Out-of-sample-Prüfung, kein Gate.")
        if m["trades"] < 30:
            outcome = "ZU_WENIG_DATEN"
            summary["headline"] = f"Zu wenig Daten: {m['trades']} Trades im ganzen Entwicklungszeitraum – kein Urteil möglich."
        elif (m["expectancy_r"] or 0) <= 0:
            outcome = "ABGELEHNT"
            summary["headline"] = f"Abgelehnt: {vid} verliert im Backtest nach Kosten im Mittel {fmt(m['expectancy_r'])} R je Trade."
            summary["reasons"] = ["NETTO_NEGATIV"]
        else:
            outcome = "KEIN_BELASTBARER_FORTSCHRITT"
            summary["headline"] = (f"Backtest positiv ({fmt(m['expectancy_r'])} R je Trade), aber ohne Walk-forward und Gates kein "
                                   "belastbares Ergebnis.")
        return outcome, summary

    if kind == "METALABEL":
        res = metalabel.run(prep, folds, outs, seed)
        ctx.progress["metalabel"] = {"pipeline": res.pipeline}
        metrics["Out-of-fold-Fälle"] = _metric(str(res.n_oof), note=f"nötig ≥ {metalabel.MIN_OOF}")
        metrics["ECE"] = _metric(fmt(res.ece), note=f"nötig ≤ {metalabel.MAX_ECE}")
        metrics["Brier-Score"] = _metric(fmt(res.brier), note=f"Basisrate {fmt(res.brier_base)}")
        if res.improvement:
            metrics["Verbesserung je Kandidat"] = _metric(fmt(res.improvement[0]), "R", [fmt(res.improvement[1]), fmt(res.improvement[2])],
                                                          "gepaart: Auslassen = 0 statt Ergebnis des Kandidaten")
        summary["warnings"].insert(0, "Das Modell darf nur filtern, nie vergrössern; ohne Kalibrierung bleibt es beim Setup-Score «nicht kalibriert».")
        if res.status == "ZU_WENIG_DATEN":
            summary["headline"] = f"Zu wenig Daten für ein Meta-Labeling-Modell: {res.n_oof} Out-of-fold-Fälle (nötig ≥ {metalabel.MIN_OOF})."
        elif res.status == "KANDIDAT":
            summary["headline"] = "Kalibriertes Filtermodell verbessert die Kandidaten out-of-fold – Kandidat, kein Vorteilsbeweis."
        else:
            summary["headline"] = "Kein belastbarer Fortschritt: Das Filtermodell ist nicht kalibriert oder verbessert die Kandidaten nicht nachweisbar."
        return res.status, summary

    if kind == "HOLDOUT":
        access = int(ctx.progress.get("holdout_access", holdout_access_count(conn, ctx.exp["strategy"])))
        wf = _wf_reference(conn, vid)
        interval = None
        if wf and trades:
            dist = [x / len(trades) for x in bootstrap_sum_distribution(wf, len(trades), seed, n_boot)]
            interval = (percentile(dist, 0.10), percentile(dist, 0.90))
        g2 = evaluate_g2(m["expectancy_r"], m["clusters"], interval, access)
        write_gate(conn, vid, g2, int(ctx.exp["id"]), now)
        summary["reasons"] = g2.reasons
        if access > 1:
            summary["warnings"].insert(0, HOLDOUT_CONSUMED_TEXT)
        outcome = {"PASSED": "KANDIDAT", "FAILED": "ABGELEHNT", "INSUFFICIENT": "ZU_WENIG_DATEN"}[g2.result]
        summary["headline"] = _headline("G2", g2, vid, m, access)
        return outcome, summary

    # WALK_FORWARD / OPTIMIZE: G1 mit DSR, PBO (CSCV über alle Varianten dieses Laufs) und Plateau
    # PBO misst die Auswahl unter Varianten: bei OPTIMIZE über alle Rastervarianten dieses Laufs; bei einer
    # Walk-forward-Prüfung einer optimierten Version gilt der PBO ihres OPTIMIZE-Laufs; eine vorab festgelegte
    # Version wurde nicht ausgewählt (nicht anwendbar). Die ±20 %-Nachbarn dienen dort nur dem Plateau-Test.
    pbo_applicable, pbo_val, pbo_n = True, None, 0
    if kind == "OPTIMIZE":
        matrix = [vector(_sum_deltas(_fold_outs(t)), index) for t in trials]
        pbo_res = pbo_cscv(matrix, 10) if len(matrix) >= 2 else None
        pbo_val, pbo_n = (None if pbo_res is None else pbo_res[0]), len(matrix)
        ctx.progress["pbo"] = {"value": pbo_val, "variants": pbo_n}
    elif "+opt-" in vid:
        origin = _optimize_origin(conn, vid)
        if origin:
            pbo_val, pbo_n = origin.get("value"), int(origin.get("variants") or 0)
    else:
        pbo_applicable = False
    by_params = {canonical(t["variant"]["params"]): t["metrics"].get("expectancy_r") for t in trials}
    nb = neighbours(ctx.fam, chosen_params, tuple(ctx.cfg.get("free_params") or ctx.fam.free))
    nb_metrics = {k: by_params.get(canonical(p)) for k, p in nb.items() if canonical(p) in by_params}
    chosen_metric = by_params.get(canonical(chosen_params))
    plat, plat_note = plateau(chosen_metric, nb_metrics)
    leader = prep.leader.get(ctx.cfg["instruments"][0]) or prep.own.get(LEADER, [])
    windows = [(f.test_start, f.test_end) for f in eval_folds]
    min_days = GATES_V1["g1"]["regime_phase_min_days"]
    g1_in = G1Input(
        expectancy_r=m["expectancy_r"], expectancy_ci=tuple(m["expectancy_ci"]) if m["expectancy_ci"] else None,
        expectancy_r_cost15=cost15, clusters=m["clusters"],
        stress_phases=regime_phases(leader, windows, Regime.STRESS, min_days), down_phases=regime_phases(leader, windows, Regime.DOWN, min_days),
        dsr=dsr, n_trials=n_trials, pbo=pbo_val, pbo_variants=pbo_n,
        plateau=plat, plateau_note=plat_note, max_dd=m["max_dd"] if trades else None, total_return=m["net_return"],
        ratio=None if not m["max_dd"] else m["net_return"] / m["max_dd"], matched_total=bl.matched.total, matched_ratio=bl.matched.ratio,
        random_p95=bl.random_p95, pbo_applicable=pbo_applicable,
    )
    g1 = evaluate_g1(g1_in)
    metrics["PBO"] = _metric(fmt(pbo_val) if pbo_applicable else "nicht anwendbar",
                             note=f"CSCV, S = 10, {pbo_n} Varianten" if pbo_applicable else "vorab festgelegte Version, keine Auswahl")
    metrics["Plateau"] = _metric("bestanden" if plat else ("nicht bestimmbar" if plat is None else "nicht bestanden"), note=plat_note)
    summary["reasons"] = g1.reasons
    ctx.progress["gate"] = {"G1": g1.result, "criteria": g1.criteria_json()}

    if kind == "WALK_FORWARD":
        ensure_version(conn, version_of(ctx.fam, chosen_params))
        write_gate(conn, vid, g1, int(ctx.exp["id"]), now)
        outcome = {"PASSED": "KANDIDAT", "FAILED": "ABGELEHNT", "INSUFFICIENT": "ZU_WENIG_DATEN"}[g1.result]
        summary["headline"] = _headline("G1", g1, vid, m, 1)
        return outcome, summary

    # OPTIMIZE: gepaarter Vergleich gegen den Champion (Variante 0) auf identischen Testfenstern
    champion = _fold_outs(trials[0])
    champ_by_fold = {fo.fold: sum(t.r for t in fo.trades) for fo in champion}
    chall_by_fold = {fo.fold: sum(t.r for t in fo.trades) for fo in outs}
    diffs = [chall_by_fold.get(f.index, 0.0) - champ_by_fold.get(f.index, 0.0) for f in folds]
    paired = paired_bootstrap(diffs, seed, n_boot)
    better = paired is not None and paired[1] > 0
    if paired:
        metrics["Differenz zum Champion je Testfenster"] = _metric(fmt(paired[0]), "R", [fmt(paired[1]), fmt(paired[2])],
                                                                  "gepaart auf identischen Testfenstern; Intervall muss 0 ausschliessen")
    champion_id = ctx.fam.base.id if not ctx.cfg.get("version_id") else str(ctx.cfg["version_id"])
    if chosen_params == variants[0]:
        ctx.progress["final"]["version_id"] = champion_id
        summary["warnings"].insert(0, "Die Auswahl im jüngsten Trainingsfenster ist der Champion selbst – es entsteht keine neue Version.")
        outcome = {"PASSED": "KEIN_BELASTBARER_FORTSCHRITT", "FAILED": "ABGELEHNT", "INSUFFICIENT": "ZU_WENIG_DATEN"}[g1.result]
        summary["headline"] = _headline("G1", g1, champion_id, m, 1) + " Der Champion bleibt."
        return outcome, summary
    ensure_version(conn, version_of(ctx.fam, chosen_params), os.environ.get("VERCEL_GIT_COMMIT_SHA"))
    write_gate(conn, vid, g1, int(ctx.exp["id"]), now)
    if g1.result == "PASSED" and better:
        outcome = "KANDIDAT"
    elif g1.result == "PASSED":
        outcome = "KEIN_BELASTBARER_FORTSCHRITT"
    else:
        outcome = {"FAILED": "ABGELEHNT", "INSUFFICIENT": "ZU_WENIG_DATEN"}[g1.result]
    summary["headline"] = _headline("G1", g1, vid, m, 1) + ("" if better else f" Gegenüber {champion_id} kein belastbarer Unterschied.")
    return outcome, summary


def _sum_deltas(outs: list[FoldOut]) -> dict[Any, float]:
    out: dict[Any, float] = {}
    for fo in outs:
        for d, v in fo.deltas.items():
            out[d] = out.get(d, 0.0) + v
    return out


def _headline(gate: str, res: GateResult, vid: str, m: dict[str, Any], access: int) -> str:
    codes = ", ".join(res.reasons)
    if gate == "G2" and access > 1:
        return f"{HOLDOUT_CONSUMED_TEXT} ({vid}, Zugriff Nr. {access})."
    if res.result == "PASSED":
        nxt = "Holdout-Prüfung (G2)" if gate == "G1" else "Forward-Paper (G3)"
        return (f"{gate} bestanden für {vid}: {fmt(m['expectancy_r'])} R je Trade aus {m['clusters']} Fällen – Kandidat für die {nxt}, "
                "kein Vorteilsbeweis.")
    if res.result == "INSUFFICIENT":
        return (f"Zu wenig Evidenz für {vid}: {m['clusters']} unabhängige Fälle, {fmt(m['expectancy_r'])} R je Trade"
                + (f" ({codes})" if codes else "") + " – kein belastbares Urteil möglich.")
    return f"Abgelehnt: {vid} erfüllt {gate} nicht ({codes}); {fmt(m['expectancy_r'])} R je Trade aus {m['clusters']} Fällen."


def _optimize_origin(conn: Conn, vid: str) -> dict[str, Any] | None:
    row = conn.execute(
        """select progress from experiment where kind = 'OPTIMIZE' and status = 'DONE' and progress->'final'->>'version_id' = %s
           order by finished_at desc limit 1""", (vid,)).fetchone()
    if row is None or not isinstance(row["progress"].get("pbo"), dict):
        return None
    return dict(row["progress"]["pbo"])


def _wf_reference(conn: Conn, vid: str) -> list[float] | None:
    """Out-of-sample-R der jüngsten Walk-forward-Bewertung mit bestandenem G1 für diese Version."""
    row = conn.execute(
        """select e.progress from experiment e join gate_evaluation g on g.experiment_id = e.id
           where g.strategy_version_id = %s and g.gate = 'G1' and g.result = 'PASSED' and e.kind in ('WALK_FORWARD', 'OPTIMIZE')
           order by g.evaluated_at desc limit 1""", (vid,)).fetchone()
    if row is None or not row["progress"] or "final" not in row["progress"]:
        return None
    vals = [float(x) for x in row["progress"]["final"].get("oos_trade_r", [])]
    return vals or None


def forward_checks(conn: Conn, now: datetime, every: timedelta = timedelta(hours=24)) -> list[dict[str, Any]]:
    """G3 für validierte Versionen (höchstens einmal je `every`); bei G1–G3 bestanden ein Freigabe-Vorschlag PENDING."""
    out = []
    rows = conn.execute("select id from strategy_version where lifecycle_status in ('VALIDIERT', 'FORWARD_PAPER') order by id").fetchall()
    for r in rows:
        vid = r["id"]
        last = latest_gate(conn, vid, "G3")
        if last is not None and last["evaluated_at"] > now - every:
            continue
        res = evaluate_g3(forward_input(conn, vid, now, _wf_reference(conn, vid)))
        write_gate(conn, vid, res, None, now)
        proposed = propose_approval(conn, vid, now) if res.result == "PASSED" else False
        out.append({"version": vid, "G3": res.result, "reasons": res.reasons, "approval_proposed": proposed})
    return out
