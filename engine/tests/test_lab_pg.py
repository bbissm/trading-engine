"""Lernlabor gegen echtes Postgres (Migrationen aus web/drizzle): Experimente, Gates, Budgets, Holdout, Befehle,
Forschungsdaten-Abruf. Läuft nur mit TE_TEST_DATABASE_URL (leere Wegwerf-Datenbank). Kein Netz."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from lab_synth import market
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from tradingengine.adapters.bitstamp_public import BitstampPublic
from tradingengine.core.candles import Candle
from tradingengine.ports import Command
from tradingengine.research import commands, data, gates, runner

DSN = os.environ.get("TE_TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[2] / "web" / "drizzle"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not DSN, reason="TE_TEST_DATABASE_URL nicht gesetzt")]

Conn = psycopg.Connection[dict[str, Any]]


@pytest.fixture()
def conn() -> Iterator[Conn]:
    assert DSN
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("drop schema public cascade; create schema public;")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            for statement in path.read_text(encoding="utf-8").split("--> statement-breakpoint"):
                if statement.strip():
                    c.execute(statement)  # type: ignore[arg-type]
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as c2:
        c2.execute("insert into setting (key, value, updated_by) values ('lab.budget', %s, 'test')", (Jsonb({"runs_per_week": 100}),))
        yield c2


def _load(conn: Conn, series: dict[str, list[Candle]]) -> list[str]:
    ids = sorted(series)
    leader = ids[0]
    for inst in ids:
        conn.execute(
            "insert into instrument (id, kind, venue, venue_symbol, name, quote_currency, leader_id, in_universe) "
            "values (%s, 'CRYPTO_SPOT', 'TEST', %s, %s, 'USD', %s, false)", (inst, inst, inst, None if inst == leader else leader))
        data.insert_candles(conn, series[inst], datetime(2026, 1, 1, tzinfo=UTC))
    return ids


def _run(conn: Conn, exp_id: int, budget_s: float = 10_000.0) -> dict[str, Any]:
    for _ in range(50):
        runner.run_step(conn, NOW, budget_s)
        row = conn.execute("select * from experiment where id = %s", (exp_id,)).fetchone()
        assert row is not None
        if row["status"] in ("DONE", "ABORTED"):
            return row
    raise AssertionError("Experiment wurde nicht fertig")


def _create(conn: Conn, kind: str, strategy: str, config: dict[str, Any]) -> int:
    exp_id, reason = runner.create_experiment(conn, kind, strategy, config, "user:test", NOW)
    assert exp_id is not None, reason
    return exp_id


SUMMARY_KEYS = {"headline", "metrics", "baselines", "sensitivity", "equity", "folds", "reasons", "warnings"}


def test_known_edge_passes_g1_walk_forward(conn: Conn) -> None:
    ids = _load(conn, market("trend", [f"T:{c}/USD" for c in "ABCDEF"], seed=1))
    exp_id = _create(conn, "WALK_FORWARD", "s1-trend-pullback", {"instruments": ids, "random_draws": 300})
    exp = _run(conn, exp_id)
    gate = conn.execute("select * from gate_evaluation where experiment_id = %s", (exp_id,)).fetchone()
    assert gate is not None, exp["summary"]
    assert gate["result"] == "PASSED", gate["criteria"]
    assert exp["outcome"] == "KANDIDAT" and exp["status"] == "DONE"
    assert conn.execute("select lifecycle_status from strategy_version where id = 's1-trend-pullback@1'").fetchone() == {
        "lifecycle_status": "HISTORISCH_GEPRUEFT"}
    s = exp["summary"]
    assert set(s) == SUMMARY_KEYS and "kein Vorteilsbeweis" in s["headline"]
    assert len(s["equity"]) <= 500 and all(isinstance(v, str) for _, v in s["equity"])
    assert [r["scenario"] for r in s["sensitivity"]] == ["Basis", *runner.SENSITIVITIES]
    assert all(set(m) <= {"value", "unit", "ci", "note"} for m in s["metrics"].values())
    trials = conn.execute("select count(*) as n from experiment_trial where experiment_id = %s", (exp_id,)).fetchone()
    assert trials is not None and trials["n"] == exp["variants_tested"] == 6  # gewählte Version + 5 Nachbarn
    assert exp["dataset_hash"] and float(exp["cpu_seconds"]) > 0
    # Eine Freigabe entsteht nicht aus G1 allein
    assert conn.execute("select count(*) as n from approval").fetchone() == {"n": 0}


def test_pure_noise_does_not_pass(conn: Conn) -> None:
    ids = _load(conn, market("noise", ["N:A/USD", "N:B/USD", "N:C/USD"], seed=2, start=datetime(2016, 1, 1, tzinfo=UTC)))
    exp_id = _create(conn, "WALK_FORWARD", "s1-trend-pullback", {"instruments": ids, "random_draws": 200})
    exp = _run(conn, exp_id)
    gate = conn.execute("select * from gate_evaluation where experiment_id = %s", (exp_id,)).fetchone()
    assert gate is not None and gate["result"] != "PASSED"
    assert exp["outcome"] in ("ABGELEHNT", "ZU_WENIG_DATEN")
    assert gate["reasons"], "ein Ablehnungsgrund muss genannt sein"
    status = conn.execute("select lifecycle_status from strategy_version where id = 's1-trend-pullback@1'").fetchone()
    assert status is not None and status["lifecycle_status"] in ("ABGELEHNT", "ZU_WENIG_EVIDENZ")


def _small(conn: Conn) -> list[str]:
    return _load(conn, market("noise", ["N:A/USD", "N:B/USD"], seed=3, start=datetime(2019, 1, 1, tzinfo=UTC)))


def test_rerun_with_same_hash_and_seed_reproduces_trials(conn: Conn) -> None:
    ids = _small(conn)
    cfg = {"instruments": ids, "seed": 7, "random_draws": 100, "as_of": "2026-06-01T00:00:00+00:00"}
    a, b = _create(conn, "WALK_FORWARD", "s2-volume-breakout", cfg), _create(conn, "WALK_FORWARD", "s2-volume-breakout", cfg)
    ea, eb = _run(conn, a), _run(conn, b)
    assert ea["dataset_hash"] == eb["dataset_hash"]
    ta = conn.execute("select variant, metrics, folds from experiment_trial where experiment_id = %s order by id", (a,)).fetchall()
    tb = conn.execute("select variant, metrics, folds from experiment_trial where experiment_id = %s order by id", (b,)).fetchall()
    assert ta == tb and len(ta) == ea["variants_tested"]
    sa, sb = dict(ea["summary"]), dict(eb["summary"])
    # Kennzahlen identisch; nur die DSR darf sich unterscheiden, weil der zweite Lauf mehr Familienvarianten zählt
    sa["metrics"].pop("Deflated Sharpe Ratio"), sb["metrics"].pop("Deflated Sharpe Ratio")
    assert sa == sb
    # Ein späterer Kerzenimport mit anderem Inhalt im fixierten Zeitraum würde den Hash brechen → Abbruch
    c = conn.execute("select * from candle where instrument_id = %s order by open_time limit 1 offset 500", (ids[0],)).fetchone()
    assert c is not None
    conn.execute("update candle set close = close + 1 where instrument_id = %s and open_time = %s", (ids[0], c["open_time"]))
    redo = _create(conn, "WALK_FORWARD", "s2-volume-breakout", cfg)
    conn.execute("update experiment set dataset_hash = %s, status = 'RUNNING', started_at = %s, progress = %s where id = %s",
                 (ea["dataset_hash"], NOW, Jsonb({**ea["progress"], "phase": "variants", "next": 0}), redo))
    assert _run(conn, redo)["outcome"] == "ABGEBROCHEN"


class FakeClock:
    def __init__(self, step: float) -> None:
        self.t, self.step = 0.0, step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def test_t28_every_variant_counted_including_aborted_runs(conn: Conn) -> None:
    ids = _small(conn)
    cfg = {"instruments": ids, "grid": {"donchian_len": [17, 20, 24], "volume_factor": [1.5]}, "free_params": ["donchian_len", "volume_factor"]}
    exp_id = _create(conn, "OPTIMIZE", "s2-volume-breakout", cfg)
    runner.run_step(conn, NOW, 12.0, FakeClock(1.0))  # nur wenige Einheiten
    partial = conn.execute("select variants_tested, status from experiment where id = %s", (exp_id,)).fetchone()
    assert partial is not None and partial["status"] == "RUNNING" and 1 <= partial["variants_tested"] < 3
    status, _ = commands.handle(conn, Command(1, "LAB_ABORT", str(exp_id), {}, "user:test"), NOW)
    assert status == "DONE"
    aborted = conn.execute("select * from experiment where id = %s", (exp_id,)).fetchone()
    assert aborted is not None and aborted["outcome"] == "ABGEBROCHEN" and aborted["status"] == "ABORTED"
    n_aborted = conn.execute("select count(*) as n from experiment_trial where experiment_id = %s", (exp_id,)).fetchone()
    assert n_aborted is not None and n_aborted["n"] == aborted["variants_tested"] == partial["variants_tested"]
    assert f"Alle {aborted['variants_tested']}" in aborted["summary"]["headline"]
    # Ein späterer Lauf derselben Familie zählt die abgebrochenen Varianten für die DSR mit
    wf_id = _create(conn, "WALK_FORWARD", "s2-volume-breakout", {"instruments": ids, "random_draws": 50})
    wf = _run(conn, wf_id)
    total = n_aborted["n"] + wf["variants_tested"]
    assert runner._family_trials(conn, "s2-volume-breakout")[0] == total
    assert f"N = {total} getestete Varianten" in wf["summary"]["metrics"]["Deflated Sharpe Ratio"]["note"]
    crit = conn.execute("select criteria from gate_evaluation where experiment_id = %s", (wf_id,)).fetchone()
    assert crit is not None and any(f"N = {total} Varianten" in c["actual"] for c in crit["criteria"])


def test_t29_budget_abort_sets_abgebrochen(conn: Conn) -> None:
    ids = _small(conn)
    conn.execute("update setting set value = %s where key = 'lab.budget'", (Jsonb({"runs_per_week": 100, "max_seconds_per_run": 25}),))
    exp_id = _create(conn, "WALK_FORWARD", "s3-mean-reversion", {"instruments": ids})
    runner.run_step(conn, NOW, 10_000.0, FakeClock(10.0))
    exp = conn.execute("select * from experiment where id = %s", (exp_id,)).fetchone()
    assert exp is not None and exp["status"] == "ABORTED" and exp["outcome"] == "ABGEBROCHEN"
    assert float(exp["cpu_seconds"]) >= 25 and "Rechenzeit-Budget" in exp["summary"]["warnings"][0]
    trials = conn.execute("select count(*) as n from experiment_trial where experiment_id = %s", (exp_id,)).fetchone()
    assert trials is not None and trials["n"] == exp["variants_tested"]


def test_budgets_reject_at_creation(conn: Conn) -> None:
    ids = _small(conn)
    conn.execute("update setting set value = %s where key = 'lab.budget'", (Jsonb({"runs_per_week": 2}),))
    assert runner.create_experiment(conn, "BACKTEST", "s1-trend-pullback", {"instruments": ids}, "user:test", NOW)[0]
    assert runner.create_experiment(conn, "BACKTEST", "s1-trend-pullback", {"instruments": ids}, "user:test", NOW)[0]
    exp_id, reason = runner.create_experiment(conn, "BACKTEST", "s1-trend-pullback", {"instruments": ids}, "user:test", NOW)
    assert exp_id is None and reason is not None and "2 Läufe je Woche" in reason
    conn.execute("update setting set value = %s where key = 'lab.budget'", (Jsonb({"runs_per_week": 100, "max_variants": 5}),))
    exp_id, reason = runner.create_experiment(conn, "OPTIMIZE", "s1-trend-pullback", {"instruments": ids}, "user:test", NOW)
    assert exp_id is None and reason is not None and "Varianten" in reason
    for bad in ({"instruments": ["X:NOPE/USD"]}, {"instruments": ids, "timeframe": "1h"},
                {"instruments": ids, "grid": {"stop_atr": [5.0]}, "free_params": ["stop_atr"]}):
        assert runner.create_experiment(conn, "OPTIMIZE", "s1-trend-pullback", bad, "user:test", NOW)[0] is None
    assert runner.create_experiment(conn, "OPTIMIZE", "s9-unknown", {}, "user:test", NOW)[0] is None


def test_optimize_never_changes_existing_version_and_never_approves(conn: Conn) -> None:
    ids = _small(conn)
    gates.ensure_version(conn, runner.family("s2-volume-breakout").base)
    before = conn.execute("select * from strategy_version where id = 's2-volume-breakout@1'").fetchone()
    cfg = {"instruments": ids, "random_draws": 50, "grid": {"donchian_len": [17, 20, 24], "volume_factor": [1.25, 1.5]},
           "free_params": ["donchian_len", "volume_factor"]}
    exp = _run(conn, _create(conn, "OPTIMIZE", "s2-volume-breakout", cfg))
    assert exp["status"] == "DONE" and exp["outcome"] in runner.OUTCOMES and exp["variants_tested"] == 6
    after = conn.execute("select * from strategy_version where id = 's2-volume-breakout@1'").fetchone()
    assert after == before  # Parameter und Lebenszyklus des Champions unverändert
    assert conn.execute("select count(*) as n from approval").fetchone() == {"n": 0}
    others = conn.execute("select * from strategy_version where id <> 's2-volume-breakout@1'").fetchall()
    for v in others:
        assert v["id"].startswith("s2-volume-breakout@1+opt-") and v["lifecycle_status"] in ("ABGELEHNT", "ZU_WENIG_EVIDENZ", "HISTORISCH_GEPRUEFT")
        assert exp["strategy_version_id"] == v["id"]
    if not others:
        assert "Champion" in exp["summary"]["headline"] or exp["outcome"] != "KANDIDAT"


def test_holdout_requires_g1_and_second_access_is_marked(conn: Conn) -> None:
    ids = _load(conn, market("trend", ["H:A/USD", "H:B/USD"], seed=5, start=datetime(2023, 1, 1, tzinfo=UTC), end=datetime(2026, 10, 1, tzinfo=UTC)))
    exp_id, reason = runner.create_experiment(conn, "HOLDOUT", "s1-trend-pullback", {"instruments": ids}, "user:test", NOW)
    assert exp_id is None and reason is not None and "G1" in reason
    gates.ensure_version(conn, runner.family("s1-trend-pullback").base)
    conn.execute("insert into gate_evaluation (strategy_version_id, gate, result, criteria, reasons, gate_config_version) "
                 "values ('s1-trend-pullback@1', 'G1', 'PASSED', '[]', '[]', 'gates@1')")
    first = _run(conn, _create(conn, "HOLDOUT", "s1-trend-pullback", {"instruments": ids, "random_draws": 50}))
    second = _run(conn, _create(conn, "HOLDOUT", "s1-trend-pullback", {"instruments": ids, "random_draws": 50}))
    assert data.holdout_access_count(conn, "s1-trend-pullback") == 2
    assert data.HOLDOUT_CONSUMED_TEXT not in first["summary"]["warnings"]
    assert second["summary"]["warnings"][0] == data.HOLDOUT_CONSUMED_TEXT and data.HOLDOUT_CONSUMED_TEXT in second["summary"]["headline"]
    g2 = conn.execute("select * from gate_evaluation where experiment_id = %s", (second["id"],)).fetchone()
    assert g2 is not None and g2["gate"] == "G2" and g2["result"] == "INSUFFICIENT" and "HOLDOUT_VERBRAUCHT" in g2["reasons"]
    # Holdout-Daten liegen nach dem Entwicklungsende; Trades nur im Holdout-Fenster
    h0, h1 = data.holdout_window()
    trial = conn.execute("select folds from experiment_trial where experiment_id = %s", (first["id"],)).fetchone()
    assert trial is not None
    for t in trial["folds"][0]["trades"]:
        assert h0 < datetime.fromisoformat(t[1]) and datetime.fromisoformat(t[2]) <= h1


def test_commands_pause_resume_abort_and_reject(conn: Conn) -> None:
    ids = _small(conn)
    status, result = commands.handle(conn, Command(1, "LAB_RUN", None, {"kind": "BACKTEST", "strategy": "s3-mean-reversion",
                                                                        "config": {"instruments": ids}}, "user:test"), NOW)
    assert status == "DONE"
    exp_id = result["experiment_id"]
    assert commands.handle(conn, Command(2, "LAB_PAUSE", str(exp_id), {}, "user:test"), NOW)[0] == "DONE"
    runner.run_step(conn, NOW, 1000)
    assert conn.execute("select status from experiment where id = %s", (exp_id,)).fetchone() == {"status": "PAUSED"}
    assert commands.handle(conn, Command(3, "LAB_RESUME", f"experiment:{exp_id}", {}, "user:test"), NOW)[0] == "DONE"
    done = _run(conn, exp_id)
    assert done["status"] == "DONE" and done["outcome"] in ("ZU_WENIG_DATEN", "ABGELEHNT", "KEIN_BELASTBARER_FORTSCHRITT")
    assert "kein Qualitätsnachweis" in done["summary"]["warnings"][0]
    assert commands.handle(conn, Command(4, "LAB_ABORT", str(exp_id), {}, "user:test"), NOW)[0] == "REJECTED"
    assert commands.handle(conn, Command(5, "LAB_RUN", None, {"kind": "NOPE", "strategy": "s1-trend-pullback"}, "user:test"), NOW)[0] == "REJECTED"
    assert commands.handle(conn, Command(6, "LAB_PAUSE", None, {}, "user:test"), NOW)[0] == "REJECTED"


def test_g3_forward_check_insufficient_before_eight_weeks(conn: Conn) -> None:
    ids = _small(conn)
    gates.ensure_version(conn, runner.family("s1-trend-pullback").base)
    conn.execute("update strategy_version set lifecycle_status = 'VALIDIERT' where id = 's1-trend-pullback@1'")
    conn.execute("insert into account (id, mode, name, currency) values ('P1', 'PAPER', 'Paper', 'USD')")
    started = NOW - timedelta(weeks=3)
    ep = conn.execute(
        """insert into episode (account_id, number, reason, start_cash, cash, policy, strategy_version_ids, cost_model, sim_version,
           sim_through, signals_through, started_at) values ('P1', 1, 'NEW', 10000, 10000, '{}', %s, 'k', 'sim@1', %s, %s, %s) returning id""",
        (Jsonb(["s1-trend-pullback@1"]), started, started, started)).fetchone()
    assert ep is not None
    for k in range(3):
        o = started + timedelta(days=3 * k)
        conn.execute(
            """insert into trade (id, account_id, episode_id, instrument_id, strategy_version_id, timeframe, status, opened_at, closed_at, qty,
               entry_value, entry_fees, exit_value, exit_fees, net, planned_stop, planned_risk, current_stop, highest_close, bars_held, exit_plan,
               managed_through) values (%s, 'P1', %s, %s, 's1-trend-pullback@1', '1d', 'CLOSED', %s, %s, 1, 500, 2, 510, 4, 4, 90, 50, 90, 100, 2,
               '{}', %s)""", (f"t{k}", ep["id"], ids[0], o, o + timedelta(days=2), o + timedelta(days=2)))
    out = runner.forward_checks(conn, NOW)
    assert out == [{"version": "s1-trend-pullback@1", "G3": "INSUFFICIENT", "reasons": ["ZU_WENIG_FAELLE"], "approval_proposed": False}]
    g3 = conn.execute("select * from gate_evaluation where gate = 'G3'").fetchone()
    assert g3 is not None
    dur = next(c for c in g3["criteria"] if c["name"] == "Dauer und Fälle")
    assert dur["passed"] is False and dur["actual"].startswith("3.0 Wochen")
    assert conn.execute("select lifecycle_status from strategy_version where id = 's1-trend-pullback@1'").fetchone() == {"lifecycle_status": "VALIDIERT"}
    assert conn.execute("select count(*) as n from approval").fetchone() == {"n": 0}
    assert runner.forward_checks(conn, NOW + timedelta(hours=1)) == []  # höchstens einmal je 24 h


def _bitstamp_transport(calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        start = int(request.url.params.get("start", "0"))
        step = int(request.url.params["step"])
        listing = int(datetime(2018, 1, 1, tzinfo=UTC).timestamp())
        if "ethusd" not in request.url.path:
            return httpx.Response(200, json={"data": {"pair": "X", "ohlc": []}})
        if start + 1000 * step < listing:
            return httpx.Response(200, json={"data": {"pair": "ETH/USD", "ohlc": []}})
        first = max(start, listing) // step * step
        rows = [{"timestamp": str(t), "open": "1.0", "high": "2.0", "low": "0.5", "close": "1.5", "volume": "10"}
                for t in range(first, min(first + 1000 * step, int(NOW.timestamp()) + step), step)]
        return httpx.Response(200, json={"data": {"pair": "ETH/USD", "ohlc": rows}})
    return httpx.MockTransport(handler)


def test_bitstamp_sync_is_incremental_polite_and_skips_running_candle(conn: Conn) -> None:
    calls: list[httpx.Request] = []
    slept: list[float] = []
    client = BitstampPublic(httpx.Client(transport=_bitstamp_transport(calls)))
    eth = [r for r in data.RESEARCH_INSTRUMENTS if r[0] == "BITSTAMP:ETH/USD"]
    for _ in range(6):
        res = data.sync_research_data(conn, client, NOW, max_requests=3, sleep=slept.append, instruments=eth)
        assert res.requests <= 3
    assert slept and all(s == 1.0 for s in slept)
    rows = conn.execute("select count(*) as n, min(open_time) as a, max(close_time) as b, min(source) as s, bool_and(available_at = %s) as av "
                        "from candle where instrument_id = 'BITSTAMP:ETH/USD' and timeframe = '1d'", (NOW,)).fetchone()
    assert rows is not None and rows["a"] == datetime(2018, 1, 1, tzinfo=UTC) and rows["b"] <= NOW and rows["s"] == "bitstamp" and rows["av"]
    assert rows["n"] == (rows["b"] - rows["a"]).days
    inst = conn.execute("select * from instrument where id = 'BITSTAMP:ETH/USD'").fetchone()
    assert inst is not None and inst["in_universe"] is False and inst["venue"] == "BITSTAMP" and inst["leader_id"] == "BITSTAMP:BTC/USD"
    n_before = len(calls)
    data.sync_research_data(conn, client, NOW, max_requests=3, sleep=slept.append, instruments=eth)
    assert all("step=86400" not in str(c.url) for c in calls[n_before:])  # Tageskerzen aktuell: kein erneuter Abruf
