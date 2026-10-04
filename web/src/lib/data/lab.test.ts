import { beforeAll, describe, expect, it } from "vitest";
import { eq } from "drizzle-orm";
import type { Database } from "@/db/client";
import { approvals, auditEvents, commands, experiments, experimentTrials, gateEvaluations, holdoutAccesses, strategyVersions } from "@/db/schema";
import { createTestDb } from "@/test/db";
import { issueLabCommand, labCommandInput, loadExperiment, loadLabOverview, parseSummary } from "./lab";

const NOW = Date.UTC(2026, 9, 4, 12, 0, 0);
const at = (iso: string) => new Date(iso);
let database: Database;
let expDone: number;
let expRunning: number;
let expAborted: number;
let approvalId: number;

beforeAll(async () => {
  database = await createTestDb();
  await database.insert(strategyVersions).values([
    { id: "s1-trend-pullback@1", strategy: "s1-trend-pullback", version: 1, params: { atr: 2 }, regimeRuleVersion: "regime@1", lifecycleStatus: "IDEE" },
    { id: "s1-trend-pullback@2", strategy: "s1-trend-pullback", version: 2, params: { atr: 2.5 }, regimeRuleVersion: "regime@1", lifecycleStatus: "HISTORISCH_GEPRUEFT" },
    { id: "s3-mean-reversion@1", strategy: "s3-mean-reversion", version: 1, params: {}, regimeRuleVersion: "regime@1", lifecycleStatus: "ABGELEHNT" },
  ]);
  const exp = { hypothesis: "ATR-Faktor 2.5 verbessert den Netto-Erwartungswert", config: {}, issuedBy: "user:martin", strategy: "s1-trend-pullback" };
  const rows = await database
    .insert(experiments)
    .values([
      { ...exp, kind: "OPTIMIZE", strategyVersionId: "s1-trend-pullback@2", status: "DONE", outcome: "KANDIDAT", variantsTested: 24, cpuSeconds: "1830.5", createdAt: at("2026-09-28T10:00:00Z"), finishedAt: at("2026-09-28T10:40:00Z"), datasetHash: "abc123def4567890", summary: { headline: "Kandidat", metrics: { expectancy_r: { value: "0.12", unit: "R", ci: ["-0.02", "0.25"] }, broken: { unit: "x" } }, reasons: [], extra: 1 } },
      { ...exp, kind: "WALK_FORWARD", strategyVersionId: "s1-trend-pullback@1", status: "RUNNING", variantsTested: 3, createdAt: at("2026-10-02T10:00:00Z") },
      { ...exp, kind: "BACKTEST", strategy: "s3-mean-reversion", strategyVersionId: "s3-mean-reversion@1", status: "ABORTED", outcome: "ABGEBROCHEN", createdAt: at("2026-10-03T10:00:00Z") },
    ])
    .returning({ id: experiments.id, kind: experiments.kind });
  expDone = rows.find((r) => r.kind === "OPTIMIZE")!.id;
  expRunning = rows.find((r) => r.kind === "WALK_FORWARD")!.id;
  expAborted = rows.find((r) => r.kind === "BACKTEST")!.id;
  await database.insert(experimentTrials).values([
    { experimentId: expDone, variant: { atr: 2 }, metrics: { net: "-12.5" } },
    { experimentId: expDone, variant: { atr: 2.5 }, metrics: { net: "40.1" } },
  ]);
  await database.insert(gateEvaluations).values([
    { strategyVersionId: "s1-trend-pullback@2", gate: "G1", result: "FAILED", criteria: [{ name: "Unabhängige Fälle", actual: "42", required: "≥ 100", passed: false }], reasons: ["ZU_WENIG_FAELLE"], gateConfigVersion: "gates@1", experimentId: expDone, evaluatedAt: at("2026-09-28T10:41:00Z") },
    { strategyVersionId: "s1-trend-pullback@2", gate: "G1", result: "PASSED", criteria: [], reasons: [], gateConfigVersion: "gates@1", evaluatedAt: at("2026-09-01T10:00:00Z") },
  ]);
  await database.insert(holdoutAccesses).values({ strategy: "s1-trend-pullback", experimentId: expDone });
  const [ap] = await database.insert(approvals).values({ strategyVersionId: "s1-trend-pullback@2", proposedBy: "engine:lab" }).returning({ id: approvals.id });
  approvalId = ap.id;
});

describe("lab overview", () => {
  it("builds version cards with the latest gate per version, columns and budgets", async () => {
    const d = await loadLabOverview(NOW);
    expect(d.versions.map((v) => v.id)).toEqual(["s1-trend-pullback@1", "s1-trend-pullback@2", "s3-mean-reversion@1"]);
    const v2 = d.versions[1];
    expect(v2.gates.G1).toMatchObject({ result: "FAILED", reasons: ["ZU_WENIG_FAELLE"] }); // latest wins
    expect(v2.learned).toBe(true);
    expect(v2.sourceExperimentId).toBe(expDone);
    expect(d.learned.map((v) => v.id)).toEqual(["s1-trend-pullback@2"]);
    expect(d.proposed.map((v) => v.approval.id)).toEqual([approvalId]);
    expect(d.live).toEqual([]);
    expect(d.experiments.map((e) => e.outcome)).toEqual(["ABGEBROCHEN", null, "KANDIDAT"]); // failed/aborted are listed too
    expect(d.budget).toEqual({ runsThisWeek: 3, cpuSecondsThisMonth: "0.000" }); // week from Mon 28 Sep (NOW is Sun 4 Oct); October has no CPU time yet
  });

  it("loads an experiment with trials, gates and holdout accesses", async () => {
    expect(await loadExperiment(999_999)).toBeNull();
    const d = (await loadExperiment(expDone, NOW))!;
    expect(d.trials).toHaveLength(2);
    expect(d.gates).toHaveLength(1);
    expect(d.holdout).toHaveLength(1);
    expect(d.summary.metrics).toEqual([["expectancy_r", { value: "0.12", unit: "R", ci: ["-0.02", "0.25"] }]]);
  });
});

describe("summary contract", () => {
  it("is optional-safe and ignores malformed and unknown fields", () => {
    expect(parseSummary(null)).toEqual({ headline: null, metrics: [], baselines: [], sensitivity: [], equity: [], folds: [], reasons: [], warnings: [] });
    const s = parseSummary({
      headline: "Kein belastbarer Fortschritt",
      baselines: [{ name: "Cash", metric: "net", value: "0" }, { name: 3 }],
      sensitivity: [{ scenario: "Kosten × 1.5", trades: 80, net: "-12", expectancy_r: null }, { scenario: "x" }],
      equity: [["2026-01-01T00:00:00Z", "10000"], ["kein Datum", "1"], [1]],
      folds: [{ train: ["2023-01-01", "2024-12-31"], test: ["2025-01-01", "2025-06-30"], trades: 31, net: "12.5" }],
      reasons: ["NETTO_NEGATIV", 4],
      warnings: "nein",
      surprise: { a: 1 },
    });
    expect(s.baselines).toEqual([{ name: "Cash", metric: "net", value: "0" }]);
    expect(s.sensitivity).toEqual([{ scenario: "Kosten × 1.5", trades: 80, net: "-12", expectancyR: null }]);
    expect(s.equity).toEqual([["2026-01-01T00:00:00Z", "10000"]]);
    expect(s.folds[0]).toEqual({ train: ["2023-01-01", "2024-12-31"], test: ["2025-01-01", "2025-06-30"], trades: 31, net: "12.5" });
    expect(s.reasons).toEqual(["NETTO_NEGATIV"]);
    expect(s.warnings).toEqual([]);
  });
});

describe("lab commands", () => {
  it("rejects unknown types, unknown kinds/strategies and APPROVED_LIVE", () => {
    expect(labCommandInput.safeParse({ type: "PAPER_START", accountId: "x" }).success).toBe(false);
    expect(labCommandInput.safeParse({ type: "LIVE_ORDER" }).success).toBe(false);
    expect(labCommandInput.safeParse({ type: "LAB_RUN", kind: "YOLO", strategy: "s1-trend-pullback" }).success).toBe(false);
    expect(labCommandInput.safeParse({ type: "LAB_RUN", kind: "BACKTEST", strategy: "s9-magic" }).success).toBe(false);
    expect(labCommandInput.safeParse({ type: "APPROVAL_DECIDE", approvalId: "1", decision: "APPROVED_LIVE" }).success).toBe(false);
    expect(labCommandInput.safeParse({ type: "LAB_ABORT", experimentId: "1; drop table" }).success).toBe(false);
  });

  it("writes command and audit event together; checks targets in the same statement", async () => {
    const run = await issueLabCommand("martin", { type: "LAB_RUN", kind: "WALK_FORWARD", strategy: "s2-volume-breakout" });
    expect(run.ok).toBe(true);
    const id = run.ok ? run.commandId : -1;
    const [c] = await database.select().from(commands).where(eq(commands.id, id));
    expect(c).toMatchObject({ type: "LAB_RUN", target: null, params: { kind: "WALK_FORWARD", strategy: "s2-volume-breakout" }, issuedBy: "user:martin", status: "PENDING" });
    const audit = await database.select().from(auditEvents).where(eq(auditEvents.object, `command:${id}`));
    expect(audit).toHaveLength(1);

    expect(await issueLabCommand("martin", { type: "LAB_PAUSE", experimentId: String(expRunning) })).toMatchObject({ ok: true });
    expect(await issueLabCommand("martin", { type: "LAB_ABORT", experimentId: String(expAborted) })).toMatchObject({ ok: false });
    expect(await issueLabCommand("martin", { type: "LAB_RESUME", experimentId: "424242" })).toMatchObject({ ok: false });

    const decided = await issueLabCommand("martin", { type: "APPROVAL_DECIDE", approvalId: String(approvalId), decision: "SHADOW", note: " beobachten " });
    expect(decided.ok).toBe(true);
    const [d] = await database.select().from(commands).where(eq(commands.id, decided.ok ? decided.commandId : -1));
    expect(d).toMatchObject({ target: String(approvalId), params: { decision: "SHADOW", note: "beobachten" } });
    expect(await issueLabCommand("martin", { type: "APPROVAL_DECIDE", approvalId: String(approvalId), decision: "APPROVED_LIVE" })).toMatchObject({ ok: false });

    const before = (await database.select().from(commands)).length;
    await issueLabCommand("martin", { type: "APPROVAL_DECIDE", approvalId: "999", decision: "REJECTED" });
    expect((await database.select().from(commands)).length).toBe(before);
  });
});
