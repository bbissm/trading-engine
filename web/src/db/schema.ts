import { boolean, bigserial, index, integer, jsonb, numeric, pgTable, primaryKey, text, timestamp, uniqueIndex, uuid } from "drizzle-orm/pg-core";

/**
 * Schema-Hoheit liegt hier (Drizzle). Die Engine (Python) liest und schreibt dieselben Tabellen
 * und prüft beim Start `schema_meta.version`. Regeln: nur additive Änderungen (expand/contract),
 * bei jeder Migration SCHEMA_VERSION erhöhen (hier und in engine/src/tradingengine/schema_version.py).
 * Preise/Mengen sind `numeric` – nie Float. Zeitstempel immer UTC.
 */
export const SCHEMA_VERSION = 1;

const price = (name: string) => numeric(name, { precision: 28, scale: 10 });
const ts = (name: string) => timestamp(name, { withTimezone: true });

/** Eine Zeile (id = 1); wird von der Migration gesetzt. */
export const schemaMeta = pgTable("schema_meta", {
  id: integer("id").primaryKey(),
  version: integer("version").notNull(),
  updatedAt: ts("updated_at").notNull().defaultNow(),
});

/** Handelbares Objekt: immer mit Handelsplatz und Handelswährung, nie nur ein Ticker. */
export const instruments = pgTable("instrument", {
  /** z. B. "KRAKEN:BTC/USD" */
  id: text("id").primaryKey(),
  /** CRYPTO_SPOT | STOCK | ETF */
  kind: text("kind").notNull(),
  venue: text("venue").notNull(),
  /** Symbol beim Anbieter, z. B. "XBTUSD" */
  venueSymbol: text("venue_symbol").notNull(),
  name: text("name").notNull(),
  baseAsset: text("base_asset"),
  quoteCurrency: text("quote_currency").notNull(),
  tickSize: price("tick_size"),
  minQty: price("min_qty"),
  minNotional: price("min_notional"),
  /** Freigegebenes Universum – der Scanner arbeitet nur hierin. */
  inUniverse: boolean("in_universe").notNull().default(false),
  /** Leitinstrument für den Marktkontext (z. B. BTC für Crypto). */
  leaderId: text("leader_id"),
  status: text("status").notNull().default("ACTIVE"),
  createdAt: ts("created_at").notNull().defaultNow(),
});

/** Abgeschlossene Kerzen. `available_at` = wann die Kerze bei uns vorlag (kein Zukunftswissen). */
export const candles = pgTable(
  "candle",
  {
    instrumentId: text("instrument_id").notNull().references(() => instruments.id),
    /** "4h" | "1d" | … */
    timeframe: text("timeframe").notNull(),
    openTime: ts("open_time").notNull(),
    closeTime: ts("close_time").notNull(),
    open: price("open").notNull(),
    high: price("high").notNull(),
    low: price("low").notNull(),
    close: price("close").notNull(),
    volume: price("volume").notNull(),
    trades: integer("trades"),
    source: text("source").notNull(),
    availableAt: ts("available_at").notNull().defaultNow(),
  },
  (t) => [primaryKey({ columns: [t.instrumentId, t.timeframe, t.openTime] })],
);

/** Zustand je Datenstrom (Instrument × Zeitebene) für Datenqualität und Betrieb. */
export const feedStatus = pgTable(
  "feed_status",
  {
    feed: text("feed").notNull(),
    instrumentId: text("instrument_id").notNull().references(() => instruments.id),
    timeframe: text("timeframe").notNull(),
    lastCandleClose: ts("last_candle_close"),
    lastOkAt: ts("last_ok_at"),
    /** OK | STALE | GAP | ERROR */
    status: text("status").notNull(),
    detail: text("detail"),
    updatedAt: ts("updated_at").notNull().defaultNow(),
  },
  (t) => [primaryKey({ columns: [t.feed, t.instrumentId, t.timeframe] })],
);

/** Unveränderliche Strategie-/Modellversion. Jede Änderung = neue Zeile. */
export const strategyVersions = pgTable("strategy_version", {
  /** z. B. "s1-trend-pullback@1" */
  id: text("id").primaryKey(),
  strategy: text("strategy").notNull(),
  version: integer("version").notNull(),
  params: jsonb("params").$type<Record<string, unknown>>().notNull(),
  regimeRuleVersion: text("regime_rule_version").notNull(),
  codeCommit: text("code_commit"),
  /** IDEE | HISTORISCH_GEPRUEFT | VALIDIERT | FORWARD_PAPER | … (Dokument 01, 3.5) */
  lifecycleStatus: text("lifecycle_status").notNull().default("IDEE"),
  createdAt: ts("created_at").notNull().defaultNow(),
});

/** Regime und Features je abgeschlossener Kerze, versioniert; Grundlage für Chart und Training. */
export const featureSnapshots = pgTable(
  "feature_snapshot",
  {
    instrumentId: text("instrument_id").notNull().references(() => instruments.id),
    timeframe: text("timeframe").notNull(),
    candleClose: ts("candle_close").notNull(),
    regimeRuleVersion: text("regime_rule_version").notNull(),
    /** UP | DOWN | SIDEWAYS | STRESS | UNKNOWN */
    regime: text("regime").notNull(),
    features: jsonb("features").$type<Record<string, number | null>>().notNull(),
    createdAt: ts("created_at").notNull().defaultNow(),
  },
  (t) => [primaryKey({ columns: [t.instrumentId, t.timeframe, t.candleClose, t.regimeRuleVersion] })],
);

/**
 * Signal = Strategieausgabe für genau eine abgeschlossene Kerze. Append-only:
 * Anwendungsrollen erhalten kein UPDATE/DELETE; Chart-Marker lesen nur diese Tabelle.
 */
export const signals = pgTable(
  "signal",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    instrumentId: text("instrument_id").notNull().references(() => instruments.id),
    timeframe: text("timeframe").notNull(),
    candleClose: ts("candle_close").notNull(),
    strategyVersionId: text("strategy_version_id").notNull().references(() => strategyVersions.id),
    /** BUY | HOLD | REDUCE | EXIT | NO_TRADE */
    action: text("action").notNull(),
    regime: text("regime").notNull(),
    /** Setup-Score 0–100, keine Gewinnwahrscheinlichkeit. */
    score: integer("score"),
    entry: price("entry"),
    stop: price("stop"),
    target: price("target"),
    maxHoldBars: integer("max_hold_bars"),
    validUntil: ts("valid_until"),
    /** Beobachtbare Auslöser bzw. Gründe für NO_TRADE. */
    triggers: jsonb("triggers").$type<string[]>().notNull(),
    /** Faktoren, die gegen den Trade sprechen. */
    counter: jsonb("counter").$type<string[]>().notNull(),
    dataSource: text("data_source").notNull(),
    /** Sekunden zwischen Kerzenschluss und Signalerzeugung. */
    dataAgeS: integer("data_age_s").notNull(),
    createdAt: ts("created_at").notNull().defaultNow(),
  },
  (t) => [
    uniqueIndex("signal_unique_idx").on(t.strategyVersionId, t.instrumentId, t.timeframe, t.candleClose),
    index("signal_instrument_idx").on(t.instrumentId, t.candleClose),
  ],
);

/** Lebenszeichen der Engine-Dienste. */
export const heartbeats = pgTable("heartbeat", {
  service: text("service").primaryKey(),
  lastSeen: ts("last_seen").notNull(),
  schemaVersion: integer("schema_version").notNull(),
  detail: jsonb("detail").$type<Record<string, unknown>>(),
});

/** Bedienhandlung Web → Engine. Die Web-App ruft die Engine nie direkt auf. */
export const commands = pgTable(
  "command",
  {
    id: bigserial("id", { mode: "number" }).primaryKey(),
    /** z. B. PING */
    type: text("type").notNull(),
    target: text("target"),
    params: jsonb("params").$type<Record<string, unknown>>().notNull().default({}),
    issuedBy: text("issued_by").notNull(),
    issuedAt: ts("issued_at").notNull().defaultNow(),
    /** PENDING | DONE | REJECTED */
    status: text("status").notNull().default("PENDING"),
    result: jsonb("result").$type<Record<string, unknown>>(),
    handledAt: ts("handled_at"),
  },
  (t) => [index("command_pending_idx").on(t.status, t.id)],
);

/** Lückenloses Protokoll, append-only. */
export const auditEvents = pgTable(
  "audit_event",
  {
    id: bigserial("id", { mode: "number" }).primaryKey(),
    ts: ts("ts").notNull().defaultNow(),
    /** user:<name> | engine:<service> */
    actor: text("actor").notNull(),
    kind: text("kind").notNull(),
    object: text("object"),
    data: jsonb("data").$type<Record<string, unknown>>(),
  },
  (t) => [index("audit_ts_idx").on(t.ts)],
);
