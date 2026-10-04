import { beforeAll, describe, expect, it } from "vitest";
import { sql } from "drizzle-orm";
import type { Database } from "@/db/client";
import { auditEvents, candles, commands, featureSnapshots, feedStatus, heartbeats, instruments, SCHEMA_VERSION, signals, strategyVersions } from "@/db/schema";
import { createTestDb } from "@/test/db";
import { loadInstrument } from "./instrument";
import { issuePing, loadOperations } from "./operations";
import { loadOverview } from "./overview";
import { loadScanner } from "./scanner";
import { listSignals } from "./signals";
import { listStrategyVersions } from "./strategies";

const NOW = Date.UTC(2026, 9, 4, 12, 0, 0);
const at = (minutesAgo: number) => new Date(NOW - minutesAgo * 60_000);
const BTC = "KRAKEN:BTC/USD";
const ETH = "KRAKEN:ETH/USD";
const H4 = 4 * 60;

let database: Database;

beforeAll(async () => {
  database = await createTestDb();
  await database.insert(instruments).values([
    { id: BTC, kind: "CRYPTO_SPOT", venue: "KRAKEN", venueSymbol: "XBTUSD", name: "Bitcoin / US-Dollar", baseAsset: "BTC", quoteCurrency: "USD", inUniverse: true },
    { id: ETH, kind: "CRYPTO_SPOT", venue: "KRAKEN", venueSymbol: "ETHUSD", name: "Ether / US-Dollar", baseAsset: "ETH", quoteCurrency: "USD", inUniverse: true },
    { id: "KRAKEN:DOGE/USD", kind: "CRYPTO_SPOT", venue: "KRAKEN", venueSymbol: "XDGUSD", name: "Dogecoin / US-Dollar", quoteCurrency: "USD", inUniverse: false },
  ]);
  await database.insert(strategyVersions).values({ id: "s1-trend-pullback@1", strategy: "s1-trend-pullback", version: 1, params: {}, regimeRuleVersion: "r1" });
  // three closed 4h candles for BTC, the newest closed 60 minutes ago
  await database.insert(candles).values(
    [2, 1, 0].map((i) => ({
      instrumentId: BTC,
      timeframe: "4h",
      openTime: at(60 + (i + 1) * H4),
      closeTime: at(60 + i * H4),
      open: "64000",
      high: "64900.5",
      low: "63800",
      close: i === 0 ? "64250.5" : "64100",
      volume: "12.5",
      source: "kraken",
    })),
  );
  await database.insert(featureSnapshots).values([
    { instrumentId: BTC, timeframe: "4h", candleClose: at(60 + H4), regimeRuleVersion: "r1", regime: "SIDEWAYS", features: {} },
    { instrumentId: BTC, timeframe: "4h", candleClose: at(60), regimeRuleVersion: "r1", regime: "UP", features: { adx: 28 } },
    { instrumentId: BTC, timeframe: "1d", candleClose: at(600), regimeRuleVersion: "r1", regime: "DOWN", features: {} },
  ]);
  const base = { strategyVersionId: "s1-trend-pullback@1", dataSource: "kraken", dataAgeS: 5 };
  await database.insert(signals).values([
    { ...base, instrumentId: BTC, timeframe: "4h", candleClose: at(60), action: "BUY", regime: "UP", score: 71, entry: "64250.5", stop: "62900", triggers: ["Pullback an EMA20"], counter: ["Volumen unter Durchschnitt"], createdAt: at(59) },
    { ...base, instrumentId: BTC, timeframe: "4h", candleClose: at(60 + H4), action: "NO_TRADE", regime: "SIDEWAYS", triggers: ["Regime seitwärts"], counter: [], createdAt: at(59 + H4) },
    { ...base, instrumentId: ETH, timeframe: "4h", candleClose: at(60), action: "NO_TRADE", regime: "DOWN", triggers: ["Regime abwärts"], counter: [], createdAt: at(58) },
    // older than 7 days: not part of the weekly counts
    { ...base, instrumentId: ETH, timeframe: "1d", candleClose: at(9 * 1440), action: "BUY", regime: "UP", score: 60, entry: "3000", stop: "2800", triggers: [], counter: [], createdAt: at(9 * 1440) },
  ]);
  await database.insert(heartbeats).values([
    { service: "guardian", lastSeen: at(1), schemaVersion: SCHEMA_VERSION },
    { service: "marketdata", lastSeen: at(10), schemaVersion: SCHEMA_VERSION + 1 },
  ]);
  await database.insert(feedStatus).values([
    { feed: "kraken", instrumentId: BTC, timeframe: "4h", lastCandleClose: at(60), lastOkAt: at(1), status: "OK" },
    { feed: "kraken", instrumentId: ETH, timeframe: "4h", lastCandleClose: at(900), lastOkAt: at(900), status: "STALE", detail: "Letzte Kerze fehlt" },
  ]);
});

describe("overview", () => {
  it("aggregates heartbeats, feeds and signal operation", async () => {
    const o = await loadOverview(NOW);
    expect(o.dbSchemaVersion).toBe(SCHEMA_VERSION); // set by the migration itself
    expect(o.heartbeats.map((h) => [h.service, h.ok, h.schemaOk])).toEqual([
      ["guardian", true, true],
      ["marketdata", false, false],
    ]);
    expect(o.signalOps).toEqual({ universe: 2, lastSignalAt: at(58), buy7d: 1, noTrade7d: 2 });
    expect(o.attention.map((a) => a.id)).toEqual(["heartbeat:marketdata", "schema:marketdata", `feed:kraken:${ETH}:4h`]);
    expect(o.attention[0].severity).toBe("critical");
  });

  it("flags a missing schema version", async () => {
    await database.execute(sql`update schema_meta set version = ${SCHEMA_VERSION + 1}`);
    const o = await loadOverview(NOW);
    expect(o.attention[0]).toMatchObject({ id: "schema:db", severity: "critical" });
    await database.execute(sql`update schema_meta set version = ${SCHEMA_VERSION}`);
  });
});

describe("signal list", () => {
  it("hides NO_TRADE decisions by default, newest candle first", async () => {
    const rows = await listSignals();
    expect(rows.map((r) => [r.instrumentId, r.action])).toEqual([
      [BTC, "BUY"],
      [ETH, "BUY"],
    ]);
    // numeric columns stay strings (no float)
    expect(rows[0]).toMatchObject({ entry: "64250.5000000000", stop: "62900.0000000000", score: 71, quoteCurrency: "USD", triggers: ["Pullback an EMA20"], counter: ["Volumen unter Durchschnitt"] });
  });

  it("includes NO_TRADE with all=1 and filters by instrument and timeframe", async () => {
    expect(await listSignals({ includeNoTrade: true })).toHaveLength(4);
    const btc = await listSignals({ includeNoTrade: true, instrumentId: BTC, timeframe: "4h" });
    expect(btc.map((r) => r.action)).toEqual(["BUY", "NO_TRADE"]);
    expect(await listSignals({ includeNoTrade: true, limit: 1 })).toHaveLength(1);
  });
});

describe("scanner and instrument", () => {
  it("lists the universe with latest regime per timeframe, feed status and last close", async () => {
    const rows = await loadScanner();
    expect(rows.map((r) => r.id)).toEqual([BTC, ETH]);
    expect(rows[0].regimes.map((r) => [r.timeframe, r.regime])).toEqual([
      ["4h", "UP"],
      ["1d", "DOWN"],
    ]);
    expect(rows[0].lastClose).toBe("64250.5000000000");
    expect(rows[0].feeds).toMatchObject([{ feed: "kraken", timeframe: "4h", status: "OK" }]);
    expect(rows[1]).toMatchObject({ regimes: [], lastClose: null });
  });

  it("builds chart data from stored rows only", async () => {
    expect(await loadInstrument("KRAKEN:NOPE/USD", "4h")).toBeNull();
    const d = (await loadInstrument(BTC, "4h"))!;
    expect(d.candles).toHaveLength(3);
    expect(d.candles.map((c) => c.time)).toEqual([...d.candles.map((c) => c.time)].sort((a, b) => a - b));
    const lastOpen = Math.floor(at(60 + H4).getTime() / 1000);
    expect(d.candles.at(-1)).toEqual({ time: lastOpen, open: 64000, high: 64900.5, low: 63800, close: 64250.5 });
    // the BUY marker sits on the candle whose close the signal refers to
    expect(d.markers).toEqual([{ time: lastOpen, text: "BUY" }]);
    expect(d.regimes.map((r) => r.regime)).toEqual(["SIDEWAYS", "UP"]);
    expect(d.latestBuy).toMatchObject({ entry: "64250.5000000000", stop: "62900.0000000000" });
    expect(d.decisions.map((s) => s.action)).toEqual(["BUY", "NO_TRADE"]);
    const daily = (await loadInstrument(BTC, "1d"))!;
    expect(daily.candles).toEqual([]);
    expect(daily.markers).toEqual([]);
  });
});

describe("command channel", () => {
  it("writes a PING command together with its audit event", async () => {
    await issuePing("admin");
    const [cmd] = await database.select().from(commands);
    expect(cmd).toMatchObject({ type: "PING", issuedBy: "user:admin", status: "PENDING", params: {}, result: null, handledAt: null });
    const [audit] = await database.select().from(auditEvents);
    expect(audit).toMatchObject({ actor: "user:admin", kind: "COMMAND_ISSUED", object: `command:${cmd.id}`, data: { type: "PING", commandId: cmd.id } });
    const ops = await loadOperations(NOW);
    expect(ops.commands).toHaveLength(1);
    expect(ops.feeds).toHaveLength(2);
  });
});

describe("strategy versions", () => {
  it("lists registered versions with plain decision counts", async () => {
    const rows = await listStrategyVersions();
    expect(rows.map((r) => r.id)).toEqual(["s1-trend-pullback@1"]);
    expect(rows[0]).toMatchObject({ lifecycleStatus: "IDEE", decisions: 4, buys: 2 });
    expect(rows[0].lastDecisionAt?.getTime()).toBe(at(58).getTime());
  });
});
