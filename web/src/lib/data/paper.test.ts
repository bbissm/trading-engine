import { beforeAll, describe, expect, it } from "vitest";
import { eq } from "drizzle-orm";
import type { Database } from "@/db/client";
import { accounts, auditEvents, autopilots, commands, episodes, equitySnapshots, fills, instruments, orders, reservations, signalOutcomes, signals, strategyVersions, trades } from "@/db/schema";
import { allowedCommands, orderState, paperAttention } from "@/lib/paper";
import { createTestDb } from "@/test/db";
import { loadOverview } from "./overview";
import { issuePaperCommand, listPaperAccounts, listPaperCommands, listPaperPositions, loadPaperAccount, loadSignalOutcomes } from "./paper";

const NOW = Date.UTC(2026, 9, 4, 12, 0, 0);
const at = (minutesAgo: number) => new Date(NOW - minutesAgo * 60_000);
const BTC = "KRAKEN:BTC/USD";
const ETH = "KRAKEN:ETH/USD";
const S1 = "s1-trend-pullback@1";
const A = "paper-trend";
const B = "paper-leer";
const LIVE = "live-kraken";
const H4 = 240;

let database: Database;
let ep1: number;
let ep2: number;
let epB: number;
let signalId: string;

beforeAll(async () => {
  database = await createTestDb();
  await database.insert(instruments).values([
    { id: BTC, kind: "CRYPTO_SPOT", venue: "KRAKEN", venueSymbol: "XBTUSD", name: "Bitcoin / US-Dollar", quoteCurrency: "USD", inUniverse: true },
    { id: ETH, kind: "CRYPTO_SPOT", venue: "KRAKEN", venueSymbol: "ETHUSD", name: "Ether / US-Dollar", quoteCurrency: "USD", inUniverse: true },
  ]);
  await database.insert(strategyVersions).values({ id: S1, strategy: "s1-trend-pullback", version: 1, params: {}, regimeRuleVersion: "r1" });
  await database.insert(accounts).values([
    { id: A, mode: "PAPER", name: "Trend 4h", currency: "USD", createdAt: at(20_000) },
    { id: B, mode: "PAPER", name: "Leer", currency: "USD", createdAt: at(100) },
    { id: LIVE, mode: "LIVE", name: "Echtgeld", currency: "USD", createdAt: at(50) },
  ]);
  await database.insert(autopilots).values([
    { accountId: A, state: "ACTIVE", reason: "Gestartet", updatedAt: at(5000) },
    { accountId: B, state: "ERROR", reason: "Abweichung im Kontobuch", updatedAt: at(90) },
  ]);
  const ep = { policy: {}, strategyVersionIds: [S1], costModel: "kraken-spot-tier1@1", simVersion: "sim@1", signalsThrough: at(10) };
  const inserted = await database
    .insert(episodes)
    .values([
      { ...ep, accountId: A, number: 1, reason: "NEW", startCash: "5000", cash: "4890.25", feesPaid: "12.5", realized: "-109.75", simThrough: at(10_000), startedAt: at(20_000), endedAt: at(10_000) },
      { ...ep, accountId: A, number: 2, reason: "RESET", startCash: "10000", cash: "6480.1", feesPaid: "31.2", realized: "-45.3", simThrough: at(60), startedAt: at(10_000) },
      { ...ep, accountId: B, number: 1, reason: "NEW", startCash: "2500", cash: "2500", simThrough: at(60), startedAt: at(100) },
    ])
    .returning({ id: episodes.id, accountId: episodes.accountId, number: episodes.number });
  ep1 = inserted.find((e) => e.accountId === A && e.number === 1)!.id;
  ep2 = inserted.find((e) => e.accountId === A && e.number === 2)!.id;
  epB = inserted.find((e) => e.accountId === B)!.id;

  await database.insert(equitySnapshots).values([
    { accountId: A, episodeId: ep1, ts: at(10_000), equity: "4890.25", cash: "4890.25", invested: "0" },
    { accountId: A, episodeId: ep2, ts: at(60 + 2 * H4), equity: "9990", cash: "6480.1", invested: "3400" },
    { accountId: A, episodeId: ep2, ts: at(60 + H4), equity: "9940.5", cash: "6480.1", invested: "3400" },
    { accountId: A, episodeId: ep2, ts: at(60), equity: "9968.4", cash: "6480.1", invested: "3474.6" },
  ]);

  const trade = { accountId: A, instrumentId: BTC, strategyVersionId: S1, timeframe: "4h", plannedStop: "62000", highestClose: "65000", exitPlan: {} };
  await database.insert(trades).values([
    // open: 0.03 BTC for 1930.5 → average entry 64350
    { ...trade, id: "t-open-1", episodeId: ep2, status: "OPEN", openedAt: at(900), qty: "0.03", entryValue: "1930.5", entryFees: "5.02", plannedRisk: "70.5", currentStop: "62500", barsHeld: 3, managedThrough: at(60) },
    { ...trade, id: "t-open-2", episodeId: ep2, instrumentId: ETH, status: "OPEN", openedAt: at(600), qty: "0.5", entryValue: "1544.1", entryFees: "4.01", plannedRisk: "40.25", currentStop: "2950", barsHeld: 2, managedThrough: at(60) },
    { ...trade, id: "t-c1", episodeId: ep2, status: "CLOSED", openedAt: at(5000), closedAt: at(4000), qty: "0.02", entryValue: "1280", entryFees: "3.3", exitValue: "1240", exitFees: "3.2", net: "-46.5", plannedRisk: "40", currentStop: "62000", barsHeld: 4, exitReason: "Stop", managedThrough: at(4000) },
    { ...trade, id: "t-c2", episodeId: ep2, status: "CLOSED", openedAt: at(3900), closedAt: at(3000), qty: "0.02", entryValue: "1260", entryFees: "3.3", exitValue: "1225.5", exitFees: "3.2", net: "-41", plannedRisk: "40", currentStop: "62000", barsHeld: 2, exitReason: "Stop", managedThrough: at(3000) },
    { ...trade, id: "t-c3", episodeId: ep2, status: "CLOSED", openedAt: at(2900), closedAt: at(2000), qty: "0.02", entryValue: "1250", entryFees: "3.2", exitValue: "1298.6", exitFees: "3.2", net: "42.2", plannedRisk: "40", currentStop: "62000", barsHeld: 6, exitReason: "Ziel", managedThrough: at(2000) },
    // old episode: must not leak into episode 2
    { ...trade, id: "t-old", episodeId: ep1, status: "CLOSED", openedAt: at(15_000), closedAt: at(14_000), qty: "0.01", entryValue: "600", entryFees: "1.5", exitValue: "495", exitFees: "1.25", net: "-107.75", plannedRisk: "30", currentStop: "62000", barsHeld: 9, exitReason: "Zeitlimit", managedThrough: at(14_000) },
  ]);

  const order = { mode: "PAPER", accountId: A, episodeId: ep2, instrumentId: BTC, strategyVersionId: S1, timeframe: "4h" };
  await database.insert(orders).values([
    { ...order, id: "o-entry-open", intentKey: "k1", tradeId: "t-open-1", side: "BUY", type: "LIMIT", role: "ENTRY", qty: "0.03", limitPrice: "64350", state: "FILLED", createdAt: at(960) },
    { ...order, id: "o-protect", intentKey: "k2", tradeId: "t-open-1", side: "SELL", type: "STOP", role: "PROTECT", qty: "0.03", stopPrice: "62500", state: "ACCEPTED", createdAt: at(900) },
    { ...order, id: "o-entry-eth", intentKey: "k3", instrumentId: ETH, side: "BUY", type: "LIMIT", role: "ENTRY", qty: "10", limitPrice: "3100", state: "PARTIALLY_FILLED", createdAt: at(120), validUntil: at(-120) },
    { ...order, id: "o-canceled", intentKey: "k4", side: "BUY", type: "LIMIT", role: "ENTRY", qty: "0.01", limitPrice: "60000", state: "CANCELED", createdAt: at(3000) },
  ]);
  await database.insert(fills).values([
    { id: "f1", orderId: "o-entry-open", qty: "0.03", price: "64350", fee: "5.02", feeCurrency: "USD", time: at(900), simulated: true },
    { id: "f2", orderId: "o-entry-eth", qty: "2", price: "3100", fee: "1.6", feeCurrency: "USD", time: at(110), simulated: true },
    { id: "f3", orderId: "o-entry-eth", qty: "1", price: "3099.5", fee: "0.8", feeCurrency: "USD", time: at(100), simulated: true },
  ]);
  await database.insert(reservations).values([
    { intentKey: "k3", accountId: A, episodeId: ep2, instrumentId: ETH, qty: "7", cash: "21756.4", risk: "55" },
    { intentKey: "k9", accountId: A, episodeId: ep2, instrumentId: BTC, qty: "0.01", cash: "643.6", risk: "20.5" },
    { intentKey: "k-old", accountId: A, episodeId: ep1, instrumentId: BTC, qty: "1", cash: "999", risk: "99" },
  ]);

  const base = { strategyVersionId: S1, dataSource: "kraken", dataAgeS: 5, timeframe: "4h", regime: "UP", triggers: [], counter: [] };
  const sig = await database
    .insert(signals)
    .values([
      { ...base, instrumentId: BTC, candleClose: at(60), action: "BUY", entry: "64250.5", stop: "62900" },
      { ...base, instrumentId: ETH, candleClose: at(60), action: "BUY", entry: "3100", stop: "2950" },
    ])
    .returning({ id: signals.id, instrumentId: signals.instrumentId });
  signalId = sig.find((s) => s.instrumentId === BTC)!.id;
  await database.insert(signalOutcomes).values([
    { signalId, accountId: A, episodeId: ep2, status: "ORDERED", reasons: [], values: {} },
    { signalId, accountId: B, episodeId: epB, status: "BLOCKED", reasons: ["Autopilot-Zustand erlaubt keine Einstiege", "Marktdaten nicht frisch"], values: {} },
  ]);
});

describe("paper accounts", () => {
  it("lists only PAPER accounts with autopilot state and the figures of the current episode", async () => {
    const rows = await listPaperAccounts();
    expect(rows.map((r) => r.id)).toEqual([A, B]); // LIVE account is not part of it
    expect(rows[0]).toMatchObject({ name: "Trend 4h", state: "ACTIVE", reason: "Gestartet", equity: "9968.4000000000", openPositions: 2, workingOrders: 2, workingEntryOrders: 1 });
    expect(rows[0].episode).toMatchObject({ number: 2, startCash: "10000.0000000000", cash: "6480.1000000000", realized: "-45.3000000000", feesPaid: "31.2000000000" });
    expect(rows[0].equityAt?.getTime()).toBe(at(60).getTime());
    // no snapshot yet: equity is null (the UI falls back to cash)
    expect(rows[1]).toMatchObject({ state: "ERROR", equity: null, equityAt: null, openPositions: 0, workingOrders: 0 });
    expect(rows[1].episode).toMatchObject({ number: 1, cash: "2500.0000000000" });
  });

  it("loads the current episode with KPIs computed in the database", async () => {
    expect(await loadPaperAccount("paper-nope")).toBeNull();
    expect(await loadPaperAccount(LIVE)).toBeNull();
    const d = (await loadPaperAccount(A))!;
    expect(d.episode.number).toBe(2);
    expect(d.isCurrent).toBe(true);
    expect(d.episodes.map((e) => [e.number, e.endedAt !== null])).toEqual([
      [1, true],
      [2, false],
    ]);
    expect(d.kpi).toMatchObject({
      startCash: "10000.0000000000",
      cash: "6480.1000000000",
      equity: "9968.4000000000",
      invested: "3474.6000000000",
      reserved: "22400.0000000000", // 21756.4 + 643.6, episode 1 reservation excluded
      openRisk: "110.7500000000", // 70.5 + 40.25
      realized: "-45.3000000000",
      feesPaid: "31.2000000000",
      closedTrades: 3,
      closedWithGain: 1,
    });
    expect(d.equityCurve).toHaveLength(3);
    expect(d.equityCurve.map((p) => p.time)).toEqual([...d.equityCurve.map((p) => p.time)].sort((a, b) => a - b));
    expect(d.equityCurve.at(-1)).toEqual({ time: Math.floor(at(60).getTime() / 1000), equity: 9968.4 });

    expect(d.positions.map((p) => [p.id, p.avgEntry, p.quoteCurrency])).toEqual([
      ["t-open-1", "64350.0000000000", "USD"],
      ["t-open-2", "3088.2000000000", "USD"],
    ]);
    // working orders only; the filled quantity is the sum of the fills
    expect(d.orders.map((o) => [o.id, o.state, o.filledQty])).toEqual([
      ["o-entry-eth", "PARTIALLY_FILLED", "3.0000000000"],
      ["o-protect", "ACCEPTED", "0"],
    ]);
    expect(d.closed.map((t) => [t.id, t.fees, t.net])).toEqual([
      ["t-c3", "6.4000000000", "42.2000000000"],
      ["t-c2", "6.5000000000", "-41.0000000000"],
      ["t-c1", "6.5000000000", "-46.5000000000"],
    ]);
    expect(d.fills.map((f) => [f.id, f.role, f.side, f.simulated])).toEqual([
      ["f3", "ENTRY", "BUY", true],
      ["f2", "ENTRY", "BUY", true],
      ["f1", "ENTRY", "BUY", true],
    ]);
  });

  it("selects an ended episode by number; unknown numbers fall back to the current one", async () => {
    const old = (await loadPaperAccount(A, 1))!;
    expect(old.episode.number).toBe(1);
    expect(old.isCurrent).toBe(false);
    expect(old.kpi).toMatchObject({ startCash: "5000.0000000000", equity: "4890.2500000000", reserved: "999.0000000000", openRisk: "0", closedTrades: 1, closedWithGain: 0 });
    expect(old.positions).toEqual([]);
    expect(old.closed.map((t) => t.id)).toEqual(["t-old"]);
    expect(old.equityCurve).toHaveLength(1);
    expect((await loadPaperAccount(A, 99))!.episode.number).toBe(2);
  });

  it("falls back to open entry values when no snapshot exists", async () => {
    const d = (await loadPaperAccount(B))!;
    expect(d.kpi).toMatchObject({ equity: null, invested: "0", reserved: "0", openRisk: "0", closedTrades: 0, closedWithGain: 0 });
    expect(d.equityCurve).toEqual([]);
  });

  it("groups open positions per account without totals", async () => {
    const groups = await listPaperPositions();
    expect(groups.map((g) => [g.account.id, g.positions.map((p) => p.id)])).toEqual([
      [A, ["t-open-1", "t-open-2"]],
      [B, []],
    ]);
  });
});

describe("signal outcomes", () => {
  it("returns what became of a signal per paper account", async () => {
    expect(await loadSignalOutcomes([])).toEqual({});
    const out = await loadSignalOutcomes([signalId, "00000000-0000-4000-8000-000000000000"]);
    expect(Object.keys(out)).toEqual([signalId]);
    expect(out[signalId].map((o) => [o.accountName, o.status, o.episodeNumber, o.reasons])).toEqual([
      ["Leer", "BLOCKED", 1, ["Autopilot-Zustand erlaubt keine Einstiege", "Marktdaten nicht frisch"]],
      ["Trend 4h", "ORDERED", 2, []],
    ]);
  });
});

describe("paper commands", () => {
  it("writes a command together with its audit event", async () => {
    const r = await issuePaperCommand("admin", { type: "PAPER_PAUSE", accountId: A, name: null, startCash: null });
    expect(r.ok).toBe(true);
    const id = (r as { commandId: number }).commandId;
    const [cmd] = await database.select().from(commands).where(eq(commands.id, id));
    expect(cmd).toMatchObject({ type: "PAPER_PAUSE", target: A, params: {}, issuedBy: "user:admin", status: "PENDING", result: null });
    const audit = await database.select().from(auditEvents).where(eq(auditEvents.object, `command:${id}`));
    expect(audit).toHaveLength(1);
    expect(audit[0]).toMatchObject({ actor: "user:admin", kind: "COMMAND_ISSUED", data: { type: "PAPER_PAUSE", commandId: id, target: A, params: {} } });
  });

  it("passes name and start capital as strings", async () => {
    const create = await issuePaperCommand("admin", { type: "PAPER_CREATE", name: "  Mein Konto  ", startCash: "10’000.50" });
    expect(create.ok).toBe(true);
    const reset = await issuePaperCommand("admin", { type: "PAPER_RESET", accountId: A, startCash: "25000" });
    expect(reset.ok).toBe(true);
    const rows = await listPaperCommands({ limit: 2 });
    expect(rows.map((c) => [c.type, c.target, c.params])).toEqual([
      ["PAPER_RESET", A, { start_cash: "25000" }],
      ["PAPER_CREATE", null, { name: "Mein Konto", start_cash: "10000.50" }],
    ]);
  });

  it("rejects invalid input, unknown accounts and non-PAPER accounts without writing anything", async () => {
    const before = { commands: (await database.select().from(commands)).length, audit: (await database.select().from(auditEvents)).length };
    const bad: unknown[] = [
      { type: "PING" },
      { type: "LIVE_START", accountId: A },
      { type: "PAPER_START" },
      { type: "PAPER_START", accountId: "" },
      { type: "PAPER_CREATE", name: "", startCash: "10000" },
      { type: "PAPER_CREATE", name: "x".repeat(61), startCash: "10000" },
      { type: "PAPER_CREATE", name: "Konto", startCash: "0" },
      { type: "PAPER_CREATE", name: "Konto", startCash: "-5" },
      { type: "PAPER_CREATE", name: "Konto", startCash: "100000000.01" },
      { type: "PAPER_CREATE", name: "Konto", startCash: "1e6" },
      { type: "PAPER_CREATE", name: "Konto", startCash: 10000 },
      { type: "PAPER_RESET", accountId: A },
      { type: "PAPER_STOP", accountId: "paper-nope" },
      { type: "PAPER_CLOSE_ALL", accountId: LIVE },
      { type: "PAPER_RESET", accountId: LIVE, startCash: "1000" },
      null,
    ];
    for (const input of bad) {
      const r = await issuePaperCommand("admin", input);
      expect(r.ok, JSON.stringify(input)).toBe(false);
      expect((r as { error: string }).error).toBeTruthy();
    }
    expect((await database.select().from(commands)).length).toBe(before.commands);
    expect((await database.select().from(auditEvents)).length).toBe(before.audit);
  });

  it("lists only PAPER_* commands, optionally only pending ones", async () => {
    await database.insert(commands).values([
      { type: "PING", issuedBy: "user:admin" },
      { type: "PAPER_STOP", target: A, issuedBy: "user:admin", status: "REJECTED", result: { reason: "Im Zustand READY nicht möglich" }, handledAt: at(1) },
    ]);
    const all = await listPaperCommands({ limit: 50 });
    expect(all.every((c) => c.type.startsWith("PAPER_"))).toBe(true);
    expect(all[0]).toMatchObject({ type: "PAPER_STOP", status: "REJECTED" });
    expect((await listPaperCommands({ pendingOnly: true })).every((c) => c.status === "PENDING")).toBe(true);
  });
});

describe("overview", () => {
  it("lists paper accounts separately and raises attention items", async () => {
    await database.insert(commands).values({ type: "PAPER_START", target: B, issuedBy: "user:admin", issuedAt: at(10) });
    const o = await loadOverview(NOW);
    expect(o.paper.map((a) => [a.id, a.state, a.equity, a.episode?.cash])).toEqual([
      [A, "ACTIVE", "9968.4000000000", "6480.1000000000"],
      [B, "ERROR", null, "2500.0000000000"],
    ]);
    const ids = o.attention.map((a) => a.id);
    expect(ids).toContain(`autopilot:${B}`);
    expect(o.attention.find((a) => a.id === `autopilot:${B}`)).toMatchObject({ severity: "critical", detail: "Abweichung im Kontobuch" });
    const stale = o.attention.filter((a) => a.id.startsWith("command:"));
    expect(stale).toHaveLength(1); // commands issued just now are not flagged
    expect(stale[0]).toMatchObject({ severity: "warning" });
  });
});

describe("paper rules (pure)", () => {
  it("flags only ERROR autopilots and commands pending for more than 3 minutes", () => {
    const items = paperAttention(
      {
        accounts: [
          { id: "a", name: "A", state: "ACTIVE", reason: null },
          { id: "b", name: "B", state: "ERROR", reason: null },
        ],
        pending: [
          { id: 1, type: "PAPER_START", target: "a", issuedAt: new Date(NOW - 2 * 60_000) },
          { id: 2, type: "PAPER_STOP", target: "a", issuedAt: new Date(NOW - 4 * 60_000) },
        ],
      },
      NOW,
    );
    expect(items.map((i) => i.id)).toEqual(["autopilot:b", "command:2"]);
  });

  it("enables buttons only in states where the engine accepts the command", () => {
    const flat = { openPositions: 0, workingOrders: 0 };
    const on = (state: string, h = flat) =>
      Object.entries(allowedCommands(state, h))
        .filter(([, v]) => v)
        .map(([k]) => k);
    expect(on("READY")).toEqual(["PAPER_START", "PAPER_RESET"]);
    expect(on("ACTIVE")).toEqual(["PAPER_PAUSE", "PAPER_STOP", "PAPER_CLOSE_ALL"]);
    expect(on("ENTRIES_PAUSED", { openPositions: 1, workingOrders: 1 })).toEqual(["PAPER_START", "PAPER_STOP", "PAPER_CLOSE_ALL"]);
    expect(on("WINDING_DOWN")).toEqual(["PAPER_CLOSE_ALL"]);
    expect(on("STOPPED")).toEqual(["PAPER_START", "PAPER_RESET"]);
    expect(on("ERROR", { openPositions: 1, workingOrders: 0 })).toEqual([]);
  });

  it("words order states precisely", () => {
    expect(orderState("PARTIALLY_FILLED", "3", "10").label).toBe("teilweise ausgeführt (3 von 10)");
    expect(orderState("ACCEPTED").label).toBe("vom Simulator angenommen");
    expect(orderState("CANCELED").label).toBe("storniert");
    expect(orderState("SOMETHING").label).toBe("SOMETHING");
  });
});
