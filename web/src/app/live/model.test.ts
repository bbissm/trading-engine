import { describe, expect, it } from "vitest";
import { assignEffect, canActivate, controlEffects, liveState, preconditions, validateAssign, validateMandate, type LiveFacts } from "./model";

const NOW = Date.UTC(2026, 9, 4, 12, 0, 0);
const min = (m: number) => new Date(NOW - m * 60_000);

const green: LiveFacts = {
  flag: true,
  liveSeenAt: min(1),
  account: { id: "live-kraken", permissions: { checked_at: "2026-10-04T11:00:00Z", withdraw: false, trade: true, withdraw_check: "API_PERMISSION_DENIED" } },
  lastRecon: { status: "OK", at: min(1) },
  testAckAt: min(60 * 24),
  fxDate: "2026-10-03",
  strategies: [{ id: "s2-volume-breakout@1", gates: { G1: "PASSED", G2: "PASSED", G3: "PASSED" }, approvedLive: true }],
  mandate: null,
  unknownOrders: 0,
};

describe("Live-Assistent: Voraussetzungen", () => {
  it("all green except the form fields allows activation", () => {
    const checks = preconditions(green, NOW);
    expect(checks.filter((c) => c.blocking && c.status !== "ok")).toEqual([]);
    expect(canActivate(checks)).toBe(true);
    expect(checks.find((c) => c.key === "budget")?.status).toBe("missing");
  });

  it.each([
    ["flag", { flag: false }],
    ["account", { account: null }],
    ["key", { account: { id: "live-kraken", permissions: { checked_at: "x", withdraw: true, trade: true } } }],
    ["reconciliation", { lastRecon: { status: "OK", at: min(6) } }],
    ["reconciliation", { lastRecon: { status: "DIFF", at: min(1) } }],
    ["unknown", { unknownOrders: 1 }],
    ["notification", { testAckAt: min(60 * 24 * 8) }],
    ["fx", { fxDate: null }],
    ["gates", { strategies: [{ id: "s", gates: { G1: "PASSED", G2: "PASSED", G3: "INSUFFICIENT" }, approvedLive: true }] }],
    ["approval", { strategies: [{ id: "s", gates: { G1: "PASSED", G2: "PASSED", G3: "PASSED" }, approvedLive: false }] }],
  ] as [string, Partial<LiveFacts>][])("missing %s blocks «Mandat aktivieren»", (key, override) => {
    const checks = preconditions({ ...green, ...override }, NOW);
    expect(checks.find((c) => c.key === key)?.status).toBe("missing");
    expect(canActivate(checks)).toBe(false);
  });

  it("flag is shown as true/false only", () => {
    expect(preconditions(green, NOW).find((c) => c.key === "flag")?.detail).toMatch(/^true/);
    expect(preconditions({ ...green, flag: false }, NOW).find((c) => c.key === "flag")?.detail).toMatch(/^false/);
  });
});

describe("Mandat-Validierung", () => {
  const base = { budget: "500", budgetConfirm: "500", autonomyLevel: 3, strategyIds: ["s2-volume-breakout@1"], instrumentIds: ["KRAKEN:BTC/USD"], emergency: "HOLD_PROTECTED" };
  const eligible = ["s2-volume-breakout@1"];
  it("accepts a complete mandate", () => expect(validateMandate(base, eligible)).toBeNull());
  it("requires the budget to be retyped", () => expect(validateMandate({ ...base, budgetConfirm: "5000" }, eligible)).toMatch(/Bestätigung/));
  it("rejects versions without APPROVED_LIVE", () => expect(validateMandate({ ...base, strategyIds: ["s1-trend-pullback@1"] }, eligible)).toMatch(/Nicht für Live/));
  it("rejects zero or malformed budgets", () => {
    expect(validateMandate({ ...base, budget: "0", budgetConfirm: "0" }, eligible)).toMatch(/Budget/);
    expect(validateMandate({ ...base, budget: "1e5", budgetConfirm: "1e5" }, eligible)).toMatch(/Budget/);
  });
  it("needs instruments and an emergency policy", () => {
    expect(validateMandate({ ...base, instrumentIds: [] }, eligible)).toMatch(/Instrument/);
    expect(validateMandate({ ...base, emergency: "YOLO" }, eligible)).toMatch(/Notfallpolicy/);
  });
});

describe("Bedienung: Wirkung im Dialog", () => {
  it("close-all names positions, costs and gap risk", () => {
    const e = controlEffects({ positions: 2, entryOrders: 1, notional: "1’000.00", estCost: "8.00", emergency: "HOLD_PROTECTED" });
    expect(e.LIVE_CLOSE_ALL).toMatch(/2 Positionen/);
    expect(e.LIVE_CLOSE_ALL).toMatch(/8.00 USD/);
    expect(e.LIVE_CLOSE_ALL).toMatch(/Kurslücken/);
    expect(e.LIVE_EMERGENCY).toMatch(/halten mit Schutz/);
    expect(controlEffects({ positions: 1, entryOrders: 0, notional: null, estCost: null, emergency: "CLOSE" }).LIVE_EMERGENCY).toMatch(/marktnah verkauft/);
  });
  it("unknown state reads «Nicht eingerichtet»", () => expect(liveState(null).label).toBe("Nicht eingerichtet"));
});

describe("Fremde Position zuordnen", () => {
  const ctx = { approved: ["s2@1"], foreign: { "K:ETH/USD": "1.5", "K:BTC/USD": "0.25", "K:SOL/USD": "0.000" }, managed: ["K:BTC/USD"] };
  it("validates approval, foreign quantity, managed position and stop", () => {
    expect(validateAssign({ instrumentId: "K:ETH/USD", strategyVersionId: "s2@1", stop: "2500.5" }, ctx)).toBeNull();
    expect(validateAssign({ instrumentId: "K:ETH/USD", strategyVersionId: "s1@1", stop: "2500" }, ctx)).toMatch(/APPROVED_LIVE/);
    expect(validateAssign({ instrumentId: "K:SOL/USD", strategyVersionId: "s2@1", stop: "2" }, ctx)).toMatch(/keine fremde Menge/);
    expect(validateAssign({ instrumentId: "K:BTC/USD", strategyVersionId: "s2@1", stop: "2" }, ctx)).toMatch(/verwaltete Position/);
    expect(validateAssign({ instrumentId: "K:ETH/USD", strategyVersionId: "s2@1", stop: "0.00" }, ctx)).toMatch(/Stop-Preis/);
  });
  it("effect text names full quantity, stop at Kraken, gap risk and unknown cost basis", () => {
    const text = assignEffect({ instrumentId: "K:ETH/USD", qty: "1.5", strategy: "s2@1", stop: "2500" }).join(" ");
    expect(text).toMatch(/ganze fremde Menge von 1\.5/);
    expect(text).toMatch(/Stop-Loss bei Kraken über die ganze Menge zu 2500 USD/);
    expect(text).toMatch(/Kurslücken/);
    expect(text).toMatch(/Kostenbasis vor der Zuordnung bleibt unbekannt/);
    expect(text).not.toMatch(/Gewinn/);
  });
});
