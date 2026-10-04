import { beforeAll, describe, expect, it } from "vitest";
import type { Database } from "@/db/client";
import { accounts, candles, episodes, equitySnapshots, fills, fxRates, instruments, orders, signalOutcomes, signals, strategyVersions, trades } from "@/db/schema";
import { createTestDb } from "@/test/db";
import { BOM, buildExport, TAX_NOTE } from "./export";
import { buyHoldNet, classifyExit, loadJournal, loadTradeDetail, parseJournalFilter } from "./journal";

const NOW = Date.UTC(2026, 9, 4, 12, 0, 0);
const t = (iso: string) => new Date(iso);
const BTC = "KRAKEN:BTC/USD";
const ETH = "KRAKEN:ETH/USD";
const S1 = "s1-trend-pullback@1";
const A = "paper-trend";
const L = "live-kraken";

let database: Database;
let ep1: number;
let ep2: number;
let epL: number;

beforeAll(async () => {
  database = await createTestDb();
  await database.insert(instruments).values([
    { id: BTC, kind: "CRYPTO_SPOT", venue: "KRAKEN", venueSymbol: "XBTUSD", name: "Bitcoin / US-Dollar", quoteCurrency: "USD", inUniverse: true },
    { id: ETH, kind: "CRYPTO_SPOT", venue: "KRAKEN", venueSymbol: "ETHUSD", name: "Ether / US-Dollar", quoteCurrency: "USD", inUniverse: true },
  ]);
  await database.insert(strategyVersions).values({ id: S1, strategy: "s1-trend-pullback", version: 1, params: {}, regimeRuleVersion: "regime@1" });
  await database.insert(accounts).values([
    { id: A, mode: "PAPER", name: "Trend 4h", currency: "USD", createdAt: t("2026-08-01T00:00:00Z") },
    { id: L, mode: "LIVE", name: "Echtgeld", currency: "USD", createdAt: t("2026-08-02T00:00:00Z") },
  ]);
  const ep = { policy: {}, strategyVersionIds: [S1], costModel: "kraken-spot-tier1@1", simVersion: "sim@1", signalsThrough: t("2026-10-04T08:00:00Z") };
  const inserted = await database
    .insert(episodes)
    .values([
      { ...ep, accountId: A, number: 1, reason: "NEW", startCash: "5000", cash: "4890", simThrough: t("2026-08-31T00:00:00Z"), startedAt: t("2026-08-01T00:00:00Z"), endedAt: t("2026-08-31T00:00:00Z") },
      { ...ep, accountId: A, number: 2, reason: "RESET", startCash: "10000", cash: "8128.75", simThrough: t("2026-10-04T08:00:00Z"), startedAt: t("2026-09-01T00:00:00Z") },
      { ...ep, accountId: L, number: 1, reason: "NEW", startCash: "500", cash: "510", simThrough: t("2026-10-04T08:00:00Z"), startedAt: t("2026-09-01T00:00:00Z") },
    ])
    .returning({ id: episodes.id, accountId: episodes.accountId, number: episodes.number });
  ep1 = inserted.find((e) => e.accountId === A && e.number === 1)!.id;
  ep2 = inserted.find((e) => e.accountId === A && e.number === 2)!.id;
  epL = inserted.find((e) => e.accountId === L)!.id;

  const [sig] = await database
    .insert(signals)
    .values({
      instrumentId: BTC, timeframe: "4h", candleClose: t("2026-09-07T04:00:00Z"), strategyVersionId: S1, action: "BUY", regime: "UP", score: 64, entry: "64000", stop: "62000",
      triggers: ["Rücksetzer an EMA20", "Schluss über Vorkerzenhoch"], counter: ["Volumen unter Median"], dataSource: "kraken", dataAgeS: 12, maxHoldBars: 40,
    })
    .returning({ id: signals.id });
  await database.insert(signalOutcomes).values({ signalId: sig.id, accountId: A, episodeId: ep2, status: "ORDERED", reasons: [], values: { menge: "0.02", geplantes_risiko: "40.00" } });

  const base = { accountId: A, episodeId: ep2, strategyVersionId: S1, timeframe: "4h", highestClose: "65000", exitPlan: { stop: "62000", max_hold_bars: 40, trail_atr: 3 } };
  await database.insert(trades).values([
    // stop exit with a gap: executed 61000 against a stop of 62000
    { ...base, id: "c1", instrumentId: BTC, signalId: sig.id, status: "CLOSED", openedAt: t("2026-09-07T08:00:00Z"), closedAt: t("2026-09-09T08:00:00Z"), qty: "0.02", entryValue: "1280", entryFees: "3.3", exitValue: "1220", exitFees: "3.2", net: "-66.5", plannedStop: "62000", plannedRisk: "40", currentStop: "62000", exitReason: "Stop", managedThrough: t("2026-09-09T08:00:00Z") },
    // target, opened on a Saturday (previous fixing Friday)
    { ...base, id: "c2", instrumentId: ETH, status: "CLOSED", openedAt: t("2026-09-12T08:00:00Z"), closedAt: t("2026-09-14T08:00:00Z"), qty: "0.5", entryValue: "1500", entryFees: "1.5", exitValue: "1650", exitFees: "1.65", net: "146.85", plannedStop: "2900", plannedRisk: "50", currentStop: "2950", exitReason: "Ziel", managedThrough: t("2026-09-14T08:00:00Z") },
    // time limit; fees ate the gross gain; exit falls into the fx gap → «Kurs fehlt»
    { ...base, id: "c3", instrumentId: BTC, status: "CLOSED", openedAt: t("2026-09-20T08:00:00Z"), closedAt: t("2026-09-22T08:00:00Z"), qty: "0.01", entryValue: "650", entryFees: "1.3", exitValue: "651", exitFees: "1.3", net: "-1.6", plannedStop: "63700", plannedRisk: "13", currentStop: "64000", exitReason: "Maximale Haltedauer von 40 Kerzen erreicht", managedThrough: t("2026-09-22T08:00:00Z") },
    { ...base, id: "o1", instrumentId: BTC, status: "OPEN", openedAt: t("2026-10-01T08:00:00Z"), qty: "0.03", entryValue: "1950", entryFees: "5.07", plannedStop: "63000", plannedRisk: "60", currentStop: "63500", managedThrough: t("2026-10-04T08:00:00Z") },
    { ...base, id: "old", episodeId: ep1, instrumentId: BTC, status: "CLOSED", openedAt: t("2026-08-08T08:00:00Z"), closedAt: t("2026-08-10T08:00:00Z"), qty: "0.01", entryValue: "600", entryFees: "1.5", exitValue: "495", exitFees: "1.25", net: "-107.75", plannedStop: "55000", plannedRisk: "30", currentStop: "55000", exitReason: "Stop", managedThrough: t("2026-08-10T08:00:00Z") },
    { ...base, id: "live1", accountId: L, episodeId: epL, instrumentId: BTC, status: "CLOSED", openedAt: t("2026-09-14T08:00:00Z"), closedAt: t("2026-09-15T08:00:00Z"), qty: "0.001", entryValue: "64", entryFees: "0.1", exitValue: "74.2", exitFees: "0.1", net: "10", plannedStop: "62000", plannedRisk: "2", currentStop: "62000", exitReason: "Ziel", managedThrough: t("2026-09-15T08:00:00Z") },
  ]);
  const o = { mode: "PAPER", accountId: A, episodeId: ep2, strategyVersionId: S1, timeframe: "4h", updatedAt: t("2026-09-09T08:00:00Z") };
  await database.insert(orders).values([
    { ...o, id: "c1-entry", intentKey: "c1-entry", instrumentId: BTC, signalId: sig.id, tradeId: "c1", side: "BUY", type: "LIMIT", role: "ENTRY", qty: "0.02", limitPrice: "64000", state: "FILLED", createdAt: t("2026-09-07T04:00:00Z") },
    { ...o, id: "c1-stop", intentKey: "c1-stop", instrumentId: BTC, tradeId: "c1", side: "SELL", type: "STOP", role: "PROTECT", qty: "0.02", stopPrice: "62000", state: "FILLED", createdAt: t("2026-09-07T08:00:00Z") },
    { ...o, id: "c2-entry", intentKey: "c2-entry", instrumentId: ETH, tradeId: "c2", side: "BUY", type: "LIMIT", role: "ENTRY", qty: "0.5", limitPrice: "3000", state: "FILLED", createdAt: t("2026-09-12T04:00:00Z") },
    { ...o, id: "c2-target", intentKey: "c2-target", instrumentId: ETH, tradeId: "c2", side: "SELL", type: "LIMIT", role: "EXIT", qty: "0.5", limitPrice: "3300", state: "FILLED", createdAt: t("2026-09-12T08:00:00Z") },
    { ...o, id: "c3-entry", intentKey: "c3-entry", instrumentId: BTC, tradeId: "c3", side: "BUY", type: "LIMIT", role: "ENTRY", qty: "0.01", limitPrice: "65000", state: "FILLED", createdAt: t("2026-09-20T04:00:00Z") },
    { ...o, id: "c3-exit", intentKey: "c3-exit", instrumentId: BTC, tradeId: "c3", side: "SELL", type: "MARKET", role: "EXIT", qty: "0.01", state: "FILLED", reason: "Maximale Haltedauer; erzeugt am 2026-09-22", createdAt: t("2026-09-22T04:00:00Z") },
    { ...o, id: "o1-entry", intentKey: "o1-entry", instrumentId: BTC, tradeId: "o1", side: "BUY", type: "LIMIT", role: "ENTRY", qty: "0.03", limitPrice: "65000", state: "FILLED", createdAt: t("2026-10-01T04:00:00Z") },
    { ...o, id: "old-entry", intentKey: "old-entry", episodeId: ep1, instrumentId: BTC, tradeId: "old", side: "BUY", type: "LIMIT", role: "ENTRY", qty: "0.01", limitPrice: "60000", state: "FILLED", createdAt: t("2026-08-08T04:00:00Z") },
    { ...o, id: "live1-entry", intentKey: "live1-entry", mode: "LIVE", accountId: L, episodeId: epL, instrumentId: BTC, tradeId: "live1", side: "BUY", type: "LIMIT", role: "ENTRY", qty: "0.001", limitPrice: "64000", state: "FILLED", createdAt: t("2026-09-14T04:00:00Z") },
  ]);
  await database.insert(fills).values([
    { id: "f-c1-in", orderId: "c1-entry", qty: "0.02", price: "64000", fee: "3.3", feeCurrency: "USD", time: t("2026-09-07T08:00:00Z"), simulated: true },
    { id: "f-c1-out", orderId: "c1-stop", qty: "0.02", price: "61000", fee: "3.2", feeCurrency: "USD", time: t("2026-09-09T08:00:00Z"), simulated: true },
    { id: "f-c2-in", orderId: "c2-entry", qty: "0.5", price: "3000", fee: "1.5", feeCurrency: "USD", time: t("2026-09-12T08:00:00Z"), simulated: true },
    { id: "f-c2-out", orderId: "c2-target", qty: "0.5", price: "3300", fee: "1.65", feeCurrency: "USD", time: t("2026-09-14T08:00:00Z"), simulated: true },
    { id: "f-c3-in", orderId: "c3-entry", qty: "0.01", price: "65000", fee: "1.3", feeCurrency: "USD", time: t("2026-09-20T08:00:00Z"), simulated: true },
    { id: "f-c3-out", orderId: "c3-exit", qty: "0.01", price: "65100", fee: "1.3", feeCurrency: "USD", time: t("2026-09-22T08:00:00Z"), simulated: true },
    { id: "f-o1-in", orderId: "o1-entry", qty: "0.03", price: "65000", fee: "5.07", feeCurrency: "USD", time: t("2026-10-01T08:00:00Z"), simulated: true },
    { id: "f-old-in", orderId: "old-entry", qty: "0.01", price: "60000", fee: "1.5", feeCurrency: "USD", time: t("2026-08-08T08:00:00Z"), simulated: true },
    { id: "f-live1-in", orderId: "live1-entry", qty: "0.001", price: "64000", fee: "0.1", feeCurrency: "USD", time: t("2026-09-14T08:00:00Z"), simulated: false },
  ]);
  const usd = (date: string, rate: string) => ({ base: "USD", quote: "CHF", date, rate, source: "ecb-frankfurter" });
  await database.insert(fxRates).values([usd("2026-09-04", "0.8"), usd("2026-09-07", "0.801"), usd("2026-09-08", "0.802"), usd("2026-09-09", "0.803"), usd("2026-09-11", "0.805"), usd("2026-09-14", "0.806"), usd("2026-10-01", "0.81")]);

  await database.insert(equitySnapshots).values(
    [
      ["2026-09-01T04:00:00Z", "10000", "0"],
      ["2026-09-08T04:00:00Z", "9950", "1280"],
      ["2026-09-10T04:00:00Z", "9933.5", "0"],
      ["2026-09-13T04:00:00Z", "10050", "1500"],
      ["2026-09-15T04:00:00Z", "10080.35", "0"],
      ["2026-10-02T04:00:00Z", "10100", "1950"],
    ].map(([ts, equity, invested]) => ({ accountId: A, episodeId: ep2, ts: t(ts), equity, cash: equity, invested })),
  );
  const candle = (instrumentId: string, open: string, close: string, price: string) => ({ instrumentId, timeframe: "4h", openTime: t(open), closeTime: t(close), open: price, high: price, low: price, close: price, volume: "1", source: "kraken" });
  await database.insert(candles).values([
    candle(BTC, "2026-09-01T00:00:00Z", "2026-09-01T04:00:00Z", "60000"),
    candle(BTC, "2026-09-07T04:00:00Z", "2026-09-07T08:00:00Z", "64000"),
    candle(BTC, "2026-09-09T04:00:00Z", "2026-09-09T08:00:00Z", "61000"),
    candle(BTC, "2026-10-04T04:00:00Z", "2026-10-04T08:00:00Z", "66000"),
    candle(ETH, "2026-09-01T00:00:00Z", "2026-09-01T04:00:00Z", "2800"),
    candle(ETH, "2026-10-04T00:00:00Z", "2026-10-04T04:00:00Z", "3080"),
  ]);
});

describe("journal", () => {
  it("parses filters and drops malformed values", () => {
    expect(parseJournalFilter({ account: A, episode: "2", status: "CLOSED", from: "2026-09-01", to: "2026-13-45x" })).toEqual({ account: A, episode: 2, instrument: undefined, strategy: undefined, status: "CLOSED", from: "2026-09-01", to: undefined });
    expect(parseJournalFilter({ status: "WHATEVER", episode: "-1" })).toMatchObject({ status: "ALL", episode: undefined });
  });

  it("defaults to the first paper account and its current episode; never mixes accounts", async () => {
    const d = await loadJournal(parseJournalFilter({}), NOW);
    expect(d.accounts.map((a) => [a.id, a.mode])).toEqual([
      [A, "PAPER"],
      [L, "LIVE"],
    ]);
    expect(d.account?.id).toBe(A);
    expect(d.episode?.number).toBe(2);
    expect(d.trades.map((x) => x.id)).toEqual(["o1", "c3", "c2", "c1"]);
    expect(d.instruments).toEqual([BTC, ETH]);
    expect(d.strategies).toEqual([S1]);
  });

  it("converts to CHF with entry and exit fixings (weekend → previous, gap → Kurs fehlt)", async () => {
    const d = await loadJournal(parseJournalFilter({ account: A, episode: "2" }), NOW);
    const by = Object.fromEntries(d.trades.map((x) => [x.id, x]));
    expect(by.c1.chf.entry).toMatchObject({ rate: "0.8010000000", date: "2026-09-07" });
    expect(by.c1.chf.exit).toMatchObject({ rate: "0.8030000000", date: "2026-09-09", source: "ecb-frankfurter" });
    expect(by.c1.chf.split).toEqual({ total: "-50.8329", trading: "-53.3929", fxEffect: "2.56" });
    expect(by.c2.chf.entry).toMatchObject({ date: "2026-09-11", previous: true }); // Saturday
    expect(by.c2.chf.split?.total).toBe("119.8626");
    expect(by.c3.chf.entry).toMatchObject({ date: "2026-09-14", previous: true }); // 6 days back: still valid
    expect(by.c3.chf.exit).toBeNull(); // 8 days after the last fixing
    expect(by.c3.chf.split).toBeNull();
    expect(by.o1.chf.split).toBeNull(); // open
  });

  it("computes KPIs with exact decimals and suppresses the uncertainty below 10 trades", async () => {
    const { kpis, equity } = await loadJournal(parseJournalFilter({ account: A, episode: "2" }), NOW);
    expect(kpis).toMatchObject({
      closed: 3,
      open: 1,
      net: "78.75",
      fees: "12.25",
      gross: "91",
      netChf: "69.0297",
      chfMissing: 1,
      wins: 1,
      losses: 2,
      avgWin: "146.85",
      avgLoss: "-34.05",
      profitFactor: "2.1564",
      expectancyR: "0.3838",
      expectancyCI: null,
    });
    expect(kpis.avgHoldHours).toBe(48);
    expect(equity).toMatchObject({ snapshots: 6, baseEquity: "10000.0000000000", lastEquity: "10100.0000000000", return: "0.01", maxDrawdown: "0.006650", exposure: "0.500000", dailyReturns: 5, sharpe: null });
  });

  it("filters by instrument, status and period (Zurich days)", async () => {
    const eth = await loadJournal(parseJournalFilter({ account: A, episode: "2", instrument: ETH }), NOW);
    expect(eth.trades.map((x) => x.id)).toEqual(["c2"]);
    expect(eth.equityScopeDiffers).toBe(true);
    const open = await loadJournal(parseJournalFilter({ account: A, episode: "2", status: "OPEN" }), NOW);
    expect(open.trades.map((x) => x.id)).toEqual(["o1"]);
    const sept = await loadJournal(parseJournalFilter({ account: A, episode: "2", from: "2026-09-10", to: "2026-09-21" }), NOW);
    expect(sept.trades.map((x) => x.id)).toEqual(["c3", "c2"]);
    const old = await loadJournal(parseJournalFilter({ account: A, episode: "1" }), NOW);
    expect(old.trades.map((x) => x.id)).toEqual(["old"]);
    const live = await loadJournal(parseJournalFilter({ account: L }), NOW);
    expect(live.trades.map((x) => x.id)).toEqual(["live1"]);
  });

  it("baselines: buy-and-hold of the traded instruments with the selection's fee rate", async () => {
    expect(buyHoldNet("5000", "60000", "66000", "0.002")).toBe("478.0439121756");
    const { baselines: b } = await loadJournal(parseJournalFilter({ account: A, episode: "2" }), NOW);
    expect(b?.feeRate).toBe("0.0019458488"); // 17.32 / 8901
    expect(b?.buyHold.legs.map((l) => [l.instrumentId, l.p0, l.p1])).toEqual([
      [BTC, "60000.0000000000", "66000.0000000000"],
      [ETH, "2800.0000000000", "3080.0000000000"],
    ]);
    expect(b?.buyHold.missing).toEqual([]);
    expect(Number(b?.buyHold.net)).toBeCloseTo(2 * Number(buyHoldNet("5000", "60000", "66000", "0.0019458488")), 6);
    expect(b?.strategyNet).toBe("78.75");
    expect(b?.strategyEquityChange).toBe("100");
  });
});

describe("trade detail", () => {
  it("loads the decision chain, orders, fills and the plan comparison", async () => {
    expect(await loadTradeDetail("nope", NOW)).toBeNull();
    const d = (await loadTradeDetail("c1", NOW))!;
    expect(d.account).toMatchObject({ id: A, mode: "PAPER" });
    expect(d.episodeNumber).toBe(2);
    expect(d.signal).toMatchObject({ score: 64, regime: "UP", dataAgeS: 12, triggers: ["Rücksetzer an EMA20", "Schluss über Vorkerzenhoch"], counter: ["Volumen unter Median"] });
    expect(d.outcome).toMatchObject({ status: "ORDERED", values: { menge: "0.02" } });
    expect(d.orders.map((o) => [o.id, o.state, o.filledQty, o.avgFill])).toEqual([
      ["c1-entry", "FILLED", "0.0200000000", "64000.0000000000"],
      ["c1-stop", "FILLED", "0.0200000000", "61000.0000000000"],
    ]);
    expect(d.fills.map((f) => [f.id, f.simulated])).toEqual([
      ["f-c1-in", true],
      ["f-c1-out", true],
    ]);
    expect(d.plan).toMatchObject({ stopNeverWidened: true, entryWithinLimit: true, vsPlannedStop: "-20", vsCurrentStop: "-20", realizedR: "-1.6625", feesOfRisk: "0.1625" });
    expect(d.classification.key).toBe("GAP_SLIPPAGE");
    expect(d.markers.map((m) => m.text)).toEqual(["Einstieg", "Ausstieg"]);
    expect(d.candles.length).toBeGreaterThan(0);
  });

  it("classifies the main deviation rule-based", () => {
    const base = { status: "CLOSED", net: "-40", fees: "2", plannedRisk: "40", currentStop: "62000", qty: "0.02", avgExit: "61950" };
    expect(classifyExit({ ...base, exitReason: "Stop" }).key).toBe("STOP_AS_PLANNED"); // 1 USD under the stop ≤ 4 USD tolerance
    expect(classifyExit({ ...base, exitReason: "Stop", avgExit: "61000" }).key).toBe("GAP_SLIPPAGE");
    expect(classifyExit({ ...base, exitReason: "Manuell geschlossen" }).key).toBe("MANUAL");
    expect(classifyExit({ ...base, exitReason: "Ziel", net: "50" }).key).toBe("TARGET");
    expect(classifyExit({ ...base, exitReason: "Maximale Haltedauer von 40 Kerzen erreicht", net: "-1", fees: "2" }).key).toBe("FEES");
    expect(classifyExit({ ...base, exitReason: "Maximale Haltedauer von 40 Kerzen erreicht" }).key).toBe("TIME");
    expect(classifyExit({ ...base, exitReason: "Regimewechsel auf DOWN" }).key).toBe("REGIME");
    expect(classifyExit({ ...base, exitReason: "Gescheiterter Ausbruch (Schluss zurück unter das Ausbruchsniveau)" }).key).toBe("FAILED_BREAKOUT");
    expect(classifyExit({ ...base, exitReason: null }).key).toBe("UNCLASSIFIED");
    expect(classifyExit({ ...base, status: "OPEN", exitReason: null }).key).toBe("OPEN");
  });
});

const parse = (csv: string) => csv.replace(BOM, "").trimEnd().split("\r\n");

describe("exports", () => {
  it("every row carries the mode; without account filter both modes appear, each labelled", async () => {
    const f = await buildExport("trades", {});
    expect(f.csv.startsWith(BOM)).toBe(true);
    const [head, ...rows] = parse(f.csv);
    expect(head.split(";")[0]).toBe("mode");
    expect(rows.map((r) => r.split(";")[0]).sort()).toEqual(["LIVE", "PAPER", "PAPER", "PAPER", "PAPER", "PAPER"]);
    const c1 = rows.find((r) => r.split(";")[3] === "c1")!.split(";");
    const col = (name: string) => c1[head.split(";").indexOf(name)];
    expect(col("opened_utc")).toBe("2026-09-07T08:00:00.000Z");
    expect(col("opened_zurich")).toBe("2026-09-07 10:00:00");
    expect(col("net_chf")).toBe("-50.8329");
    expect(col("fx_effect_chf")).toBe("2.56");
    const c3 = rows.find((r) => r.split(";")[3] === "c3")!.split(";");
    expect(c3[head.split(";").indexOf("fx_source")]).toBe("Kurs fehlt");
  });

  it("filters by account, episode and year", async () => {
    const fills = await buildExport("fills", { account: A, episode: 2 });
    expect(fills.rows).toBe(7);
    expect(fills.filename).toBe("tradingengine-fills-paper-trend-ep2.csv");
    expect(parse(fills.csv).slice(1).every((r) => r.startsWith("PAPER;paper-trend;2;"))).toBe(true);
    expect((await buildExport("orders", { account: A, episode: 1 })).rows).toBe(1);
    expect((await buildExport("orders", { account: A, episode: 9 })).rows).toBe(0);
    expect((await buildExport("fills", { year: 2025 })).rows).toBe(0);
    expect((await buildExport("fills", { account: L })).rows).toBe(1);
    const fees = parse((await buildExport("fees", { account: L })).csv);
    expect(fees[1]).toContain("LIVE;live-kraken;1;f-live1-in");
    expect(fees[1]).toContain(";0.1000000000;USD;0.8060000000;2026-09-14;ecb-frankfurter;0.0806;false");
    const fx = parse((await buildExport("fx", { account: A, episode: 2 })).csv);
    expect(fx.length).toBe(1 + 6); // 3 closed trades × entry/exit
  });

  it("year overview: per account and year, with the tax note as the first line", async () => {
    const lines = parse((await buildExport("year", {})).csv);
    expect(lines[0]).toBe(`"${TAX_NOTE}"`); // quoted: the note contains the separator
    expect(lines[1]).toBe("mode;account_id;account_name;year;currency;closed_trades;net_realized;fees;net_realized_chf;trades_without_fx_rate");
    expect(lines.slice(2)).toEqual([
      "LIVE;live-kraken;Echtgeld;2026;USD;1;10;0.2;8.06;0", // (74.2 − 0.1) × 0.806 − (64 + 0.1) × 0.806
      "PAPER;paper-trend;Trend 4h;2026;USD;4;-29;15;;2",
    ]);
    const ep2 = parse((await buildExport("year", { account: A, episode: 2, year: 2026 })).csv);
    expect(ep2.slice(2)).toEqual(["PAPER;paper-trend;Trend 4h;2026;USD;3;78.75;12.25;;1"]);
  });
});
