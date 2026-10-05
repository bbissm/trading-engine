import { beforeAll, describe, expect, it } from "vitest";
import { eq } from "drizzle-orm";
import type { Database } from "@/db/client";
import { accounts, approvals, auditEvents, autopilots, commands, episodes, gateEvaluations, instruments, mandates, reconciliations, strategyVersions, trades } from "@/db/schema";
import { createTestDb } from "@/test/db";
import { activateMandate, assignForeignPosition, decideLiveApproval, issueLiveCommand, loadLiveOverview, loadLiveSummary } from "./live";
import { listPaperPositions } from "./paper";

const BTC = "KRAKEN:BTC/USD";
const S2 = "s2-volume-breakout@1";
const S1 = "s1-trend-pullback@1";
const LIVE = "live-kraken";
const STEP_UP = new Date(Date.UTC(2026, 9, 4, 12, 0, 0));
const FAKE_SECRET = "FAKESECRET-never-stored-anywhere";

let database: Database;

beforeAll(async () => {
  database = await createTestDb();
  await database.insert(instruments).values({ id: BTC, kind: "CRYPTO_SPOT", venue: "KRAKEN", venueSymbol: "XBTUSD", name: "Bitcoin / US-Dollar", quoteCurrency: "USD", inUniverse: true });
  await database.insert(strategyVersions).values([
    { id: S1, strategy: "s1-trend-pullback", version: 1, params: {}, regimeRuleVersion: "r1" },
    { id: S2, strategy: "s2-volume-breakout", version: 1, params: {}, regimeRuleVersion: "r1" },
  ]);
  for (const gate of ["G1", "G2", "G3"]) {
    await database.insert(gateEvaluations).values({ strategyVersionId: S2, gate, result: "PASSED", criteria: [], reasons: [], gateConfigVersion: "g@1" });
  }
  await database.insert(gateEvaluations).values({ strategyVersionId: S1, gate: "G1", result: "FAILED", criteria: [], reasons: [], gateConfigVersion: "g@1" });
  await database.insert(accounts).values([
    { id: "paper-a", mode: "PAPER", name: "Paper A", currency: "USD" },
    { id: LIVE, mode: "LIVE", name: "Kraken", currency: "USD", provider: "KRAKEN", permissions: { checked_at: "2026-10-04T11:00:00Z", withdraw: false, trade: true, key_fingerprint: "abc" } },
  ]);
  await database.insert(autopilots).values({ accountId: LIVE, state: "SETUP", reason: "Konto verbunden – Mandat fehlt" });
  const [ep] = await database
    .insert(episodes)
    .values({ accountId: "paper-a", number: 1, reason: "NEW", startCash: "1000", cash: "900", policy: {}, strategyVersionIds: [S2], costModel: "c", simVersion: "s", simThrough: new Date(), signalsThrough: new Date() })
    .returning({ id: episodes.id });
  const trade = { instrumentId: BTC, strategyVersionId: S2, timeframe: "4h", status: "OPEN", openedAt: new Date(), entryFees: "0.4", plannedStop: "96", plannedRisk: "5", currentStop: "96", highestClose: "100", exitPlan: {}, managedThrough: new Date() };
  await database.insert(trades).values([
    { ...trade, id: "paper-trade", accountId: "paper-a", episodeId: ep.id, qty: "1", entryValue: "100" },
    { ...trade, id: "live-trade", accountId: LIVE, episodeId: 0, qty: "0.5", entryValue: "50" },
  ]);
  await database.insert(reconciliations).values({
    accountId: LIVE,
    status: "OK",
    diffs: [
      { kind: "SNAPSHOT", severity: "INFO", balances: { ZUSD: "950", XXBT: "0.75" }, managed: { [BTC]: "0.5" }, foreign: { [BTC]: "0.25" }, system: "online" },
      { kind: "FOREIGN_POSITION", severity: "INFO", instrument: BTC, qty: "0.25", note: "fremd – nicht verwaltet" },
    ],
  });
});

describe("Live data", () => {
  it("loads only LIVE rows and never mixes paper positions in", async () => {
    const o = await loadLiveOverview();
    expect(o.account?.id).toBe(LIVE);
    expect(o.positions.map((p) => p.id)).toEqual(["live-trade"]);
    expect(o.snapshot?.foreign[BTC]).toBe("0.25");
    expect(o.exposure.costBasis).toBe("50.00");
    expect(o.facts.strategies.find((s) => s.id === S2)?.gates.G3).toBe("PASSED");
    const paper = await listPaperPositions();
    expect(paper.flatMap((p) => p.positions.map((x) => x.id))).toEqual(["paper-trade"]);
    const summary = await loadLiveSummary();
    expect(summary).not.toHaveProperty("total");
  });

  it("close-all and resume need step-up; approval decide needs id and decision", async () => {
    expect(await issueLiveCommand("test", { type: "LIVE_CLOSE_ALL" })).toEqual({ ok: false, error: expect.stringMatching(/Step-up/) });
    expect(await issueLiveCommand("test", { type: "LIVE_RESUME" })).toEqual({ ok: false, error: expect.stringMatching(/Step-up/) });
    expect((await issueLiveCommand("test", { type: "ORDER_APPROVAL_DECIDE" })).ok).toBe(false);
    expect((await issueLiveCommand("test", { type: "LIVE_BUY_NOW" })).ok).toBe(false);
    const r = await issueLiveCommand("test", { type: "LIVE_CLOSE_ALL" }, STEP_UP);
    expect(r.ok).toBe(true);
    const [cmd] = await database.select().from(commands).where(eq(commands.type, "LIVE_CLOSE_ALL"));
    expect(cmd.params).toEqual({ step_up_at: STEP_UP.toISOString() });
    expect(cmd.target).toBe(LIVE);
    const audit = await database.select().from(auditEvents).where(eq(auditEvents.kind, "COMMAND_ISSUED"));
    expect(audit.length).toBeGreaterThan(0);
  });

  it("pause is written without step-up (risk-reducing)", async () => {
    expect((await issueLiveCommand("test", { type: "LIVE_PAUSE" })).ok).toBe(true);
    expect((await issueLiveCommand("test", { type: "LIVE_EMERGENCY" })).ok).toBe(true);
  });

  it("APPROVED_LIVE only after step-up and only with G1–G3 passed", async () => {
    expect((await decideLiveApproval("test", S2, "APPROVED_LIVE")).ok).toBe(false);
    expect(await decideLiveApproval("test", S1, "APPROVED_LIVE", STEP_UP)).toEqual({ ok: false, error: expect.stringMatching(/G1/) });
    expect((await decideLiveApproval("test", S2, "APPROVED_LIVE", STEP_UP)).ok).toBe(true);
    const rows = await database.select().from(approvals).where(eq(approvals.strategyVersionId, S2));
    expect(rows[0].decision).toBe("APPROVED_LIVE");
    expect((await loadLiveOverview()).facts.strategies.find((s) => s.id === S2)?.approvedLive).toBe(true);
  });

  it("activates a mandate with step-up time and rejects versions without approval", async () => {
    const input = { autonomyLevel: "3", strategyIds: [S2], instrumentIds: [BTC], budget: "500", budgetConfirm: "500", emergency: "HOLD_PROTECTED" };
    expect((await activateMandate("test", { ...input, strategyIds: [S1] }, STEP_UP)).ok).toBe(false);
    expect((await activateMandate("test", { ...input, budgetConfirm: "50" }, STEP_UP)).ok).toBe(false);
    expect((await activateMandate("test", { ...input, instrumentIds: ["KRAKEN:DOGE/USD"] }, STEP_UP)).ok).toBe(false);
    const r = await activateMandate("test", input, STEP_UP);
    expect(r.ok).toBe(true);
    const [m] = await database.select().from(mandates);
    expect(m.status).toBe("ACTIVE");
    expect(m.stepUpAt?.toISOString()).toBe(STEP_UP.toISOString());
    expect(m.activatedBy).toBe("user:test");
    expect(m.policy).toEqual({ emergency: "HOLD_PROTECTED", protection: "STOP_AT_EXCHANGE" });
  });

  it("never stores a Kraken secret: the web app has no input for it", async () => {
    const r = await issueLiveCommand("test", { type: "LIVE_ACCOUNT_REGISTER", apiSecret: FAKE_SECRET });
    expect(r.ok).toBe(true);
    const all = JSON.stringify([await database.select().from(commands), await database.select().from(auditEvents), await database.select().from(accounts)]);
    expect(all).not.toContain(FAKE_SECRET);
  });
});

describe("Fremde Position zuordnen", () => {
  const ETH = "KRAKEN:ETH/USD";

  it("validates step-up, APPROVED_LIVE, foreign quantity, managed position and stop; writes command + audit", async () => {
    await database.insert(instruments).values({ id: ETH, kind: "CRYPTO_SPOT", venue: "KRAKEN", venueSymbol: "ETHUSD", name: "Ether / US-Dollar", quoteCurrency: "USD", inUniverse: true });
    await database.insert(approvals).values({ strategyVersionId: S2, proposedBy: "user:test", decision: "APPROVED_LIVE", decidedAt: new Date(), decidedBy: "user:test" });
    await database.insert(reconciliations).values({
      accountId: LIVE,
      at: new Date(Date.now() + 1000),
      status: "OK",
      diffs: [{ kind: "SNAPSHOT", severity: "INFO", balances: {}, managed: { [BTC]: "0.5" }, foreign: { [BTC]: "0.25", [ETH]: "1.5" }, system: "online" }],
    });
    const ok = { instrumentId: ETH, strategyVersionId: S2, stop: "2500.5" };
    expect(await assignForeignPosition("test", ok)).toEqual({ ok: false, error: expect.stringMatching(/Step-up/) });
    expect(await assignForeignPosition("test", { ...ok, strategyVersionId: S1 }, STEP_UP)).toEqual({ ok: false, error: expect.stringMatching(/APPROVED_LIVE/) });
    expect(await assignForeignPosition("test", { ...ok, instrumentId: "KRAKEN:SOL/USD" }, STEP_UP)).toEqual({ ok: false, error: expect.stringMatching(/keine fremde Menge/) });
    expect(await assignForeignPosition("test", { ...ok, instrumentId: BTC }, STEP_UP)).toEqual({ ok: false, error: expect.stringMatching(/verwaltete Position/) });
    for (const stop of ["", "0", "-5", "1,5", "abc", "1e3"]) {
      expect(await assignForeignPosition("test", { ...ok, stop }, STEP_UP)).toEqual({ ok: false, error: expect.stringMatching(/Stop-Preis/) });
    }
    expect((await issueLiveCommand("test", { type: "LIVE_ASSIGN_POSITION" }, STEP_UP)).ok).toBe(false);

    const r = await assignForeignPosition("test", ok, STEP_UP);
    expect(r.ok).toBe(true);
    const [cmd] = await database.select().from(commands).where(eq(commands.type, "LIVE_ASSIGN_POSITION"));
    expect(cmd.target).toBe(LIVE);
    expect(cmd.status).toBe("PENDING");
    expect(cmd.params).toEqual({ instrument_id: ETH, strategy_version_id: S2, stop: "2500.5", step_up_at: STEP_UP.toISOString() });
    const audit = await database.select().from(auditEvents).where(eq(auditEvents.object, `command:${cmd.id}`));
    expect(audit).toHaveLength(1);
    expect((await loadLiveOverview()).commands.some((c) => c.id === cmd.id)).toBe(true);
  });
});
