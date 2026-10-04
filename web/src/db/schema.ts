import { boolean, bigserial, index, integer, jsonb, numeric, pgTable, primaryKey, text, timestamp, uniqueIndex, uuid } from "drizzle-orm/pg-core";

/**
 * Schema-Hoheit liegt hier (Drizzle). Die Engine (Python) liest und schreibt dieselben Tabellen
 * und prüft beim Start `schema_meta.version`. Regeln: nur additive Änderungen (expand/contract),
 * bei jeder Migration SCHEMA_VERSION erhöhen (hier und in engine/src/tradingengine/schema_version.py).
 * Preise/Mengen sind `numeric` – nie Float. Zeitstempel immer UTC.
 */
export const SCHEMA_VERSION = 2;

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
    /** Referenzniveau der Strategie (z. B. Ausbruchsniveau) für den Exit-Plan. */
    refLevel: price("ref_level"),
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

// ───────────────────────── Schema v2: Konten, Episoden, Orders, Fills, Trades (Etappe 2) ─────────────────────────

const qty = (name: string) => numeric(name, { precision: 28, scale: 10 });
const money = (name: string) => numeric(name, { precision: 28, scale: 10 });

/** Geldtopf. Orders tragen Konto und Modus; die Migration erzwingt per Fremdschlüssel, dass beide zusammenpassen. */
export const accounts = pgTable(
  "account",
  {
    id: text("id").primaryKey(),
    /** PAPER | LIVE */
    mode: text("mode").notNull(),
    name: text("name").notNull(),
    currency: text("currency").notNull(),
    createdAt: ts("created_at").notNull().defaultNow(),
  },
  (t) => [uniqueIndex("account_id_mode_idx").on(t.id, t.mode)],
);

/** Abschnitt eines Paper-Kontos. Ein Reset beendet die Episode und legt eine neue an; alte bleiben unverändert. */
export const episodes = pgTable(
  "episode",
  {
    id: bigserial("id", { mode: "number" }).primaryKey(),
    accountId: text("account_id").notNull().references(() => accounts.id),
    number: integer("number").notNull(),
    /** NEW | RESET */
    reason: text("reason").notNull(),
    startCash: money("start_cash").notNull(),
    cash: money("cash").notNull(),
    feesPaid: money("fees_paid").notNull().default("0"),
    realized: money("realized").notNull().default("0"),
    /** Risikopolicy, eingefroren beim Start der Episode. */
    policy: jsonb("policy").$type<Record<string, unknown>>().notNull(),
    strategyVersionIds: jsonb("strategy_version_ids").$type<string[]>().notNull(),
    costModel: text("cost_model").notNull(),
    simVersion: text("sim_version").notNull(),
    /** Schluss der letzten 4h-Kerze, bis zu der die Simulation fortgeschrieben ist. */
    simThrough: ts("sim_through").notNull(),
    /** Signale mit created_at ≤ diesem Zeitpunkt sind für diese Episode abgearbeitet. */
    signalsThrough: ts("signals_through").notNull(),
    startedAt: ts("started_at").notNull().defaultNow(),
    endedAt: ts("ended_at"),
  },
  (t) => [uniqueIndex("episode_account_number_idx").on(t.accountId, t.number)],
);

/** Zustand des Autopiloten je Konto (docs/01, 3.1). Der Verlauf steht im Auditprotokoll. */
export const autopilots = pgTable("autopilot", {
  accountId: text("account_id").primaryKey().references(() => accounts.id),
  /** READY | ACTIVE | ENTRIES_PAUSED | WINDING_DOWN | STOPPED | ERROR */
  state: text("state").notNull(),
  reason: text("reason"),
  updatedAt: ts("updated_at").notNull().defaultNow(),
});

/** Rundlauf Einstieg → Ausstieg. Offene Trades tragen ihren Exit-Plan bis zum Schluss. */
export const trades = pgTable(
  "trade",
  {
    id: text("id").primaryKey(),
    accountId: text("account_id").notNull().references(() => accounts.id),
    episodeId: integer("episode_id").notNull(),
    instrumentId: text("instrument_id").notNull().references(() => instruments.id),
    strategyVersionId: text("strategy_version_id").notNull().references(() => strategyVersions.id),
    signalId: uuid("signal_id"),
    timeframe: text("timeframe").notNull(),
    /** OPEN | CLOSED */
    status: text("status").notNull(),
    openedAt: ts("opened_at").notNull(),
    closedAt: ts("closed_at"),
    qty: qty("qty").notNull(),
    entryValue: money("entry_value").notNull(),
    entryFees: money("entry_fees").notNull(),
    exitValue: money("exit_value"),
    exitFees: money("exit_fees"),
    /** Nettoergebnis in Handelswährung (nur bei CLOSED). */
    net: money("net"),
    plannedStop: price("planned_stop").notNull(),
    plannedRisk: money("planned_risk").notNull(),
    currentStop: price("current_stop").notNull(),
    highestClose: price("highest_close").notNull(),
    barsHeld: integer("bars_held").notNull().default(0),
    exitPlan: jsonb("exit_plan").$type<Record<string, unknown>>().notNull(),
    exitReason: text("exit_reason"),
    /** Schluss der letzten Kerze der Signal-Zeitebene, bis zu der die Position betreut wurde. */
    managedThrough: ts("managed_through").notNull(),
  },
  (t) => [index("trade_account_idx").on(t.accountId, t.episodeId, t.status)],
);

/** Auftrag an Simulator bzw. Anbieter (docs/01, 3.4). `intent_key` macht jede wirtschaftliche Absicht eindeutig. */
export const orders = pgTable(
  "trade_order",
  {
    id: text("id").primaryKey(),
    intentKey: text("intent_key").notNull(),
    mode: text("mode").notNull(),
    accountId: text("account_id").notNull(),
    episodeId: integer("episode_id").notNull(),
    instrumentId: text("instrument_id").notNull().references(() => instruments.id),
    strategyVersionId: text("strategy_version_id").notNull().references(() => strategyVersions.id),
    signalId: uuid("signal_id"),
    tradeId: text("trade_id"),
    timeframe: text("timeframe").notNull(),
    /** BUY | SELL */
    side: text("side").notNull(),
    /** LIMIT | MARKET | STOP */
    type: text("type").notNull(),
    /** ENTRY | PROTECT | EXIT */
    role: text("role").notNull(),
    qty: qty("qty").notNull(),
    limitPrice: price("limit_price"),
    stopPrice: price("stop_price"),
    /** PREPARED … UNKNOWN (engine/core/execution.py) */
    state: text("state").notNull(),
    exitPlan: jsonb("exit_plan").$type<Record<string, unknown>>(),
    reason: text("reason"),
    createdAt: ts("created_at").notNull(),
    validUntil: ts("valid_until"),
    updatedAt: ts("updated_at").notNull().defaultNow(),
  },
  (t) => [uniqueIndex("trade_order_intent_idx").on(t.intentKey), index("trade_order_account_idx").on(t.accountId, t.episodeId, t.state)],
);

/** Ausführung. Die gefüllte Menge einer Order ist die Summe ihrer Fills. Append-only. */
export const fills = pgTable(
  "fill",
  {
    id: text("id").primaryKey(),
    orderId: text("order_id").notNull().references(() => orders.id),
    qty: qty("qty").notNull(),
    price: price("price").notNull(),
    fee: money("fee").notNull(),
    feeCurrency: text("fee_currency").notNull(),
    time: ts("time").notNull(),
    /** true = vom Simulator erzeugt, false = vom Anbieter beobachtet */
    simulated: boolean("simulated").notNull(),
  },
  (t) => [index("fill_order_idx").on(t.orderId)],
);

/** Reserviertes Kapital und Risiko offener Einstiegsorders. */
export const reservations = pgTable("reservation", {
  intentKey: text("intent_key").primaryKey(),
  accountId: text("account_id").notNull().references(() => accounts.id),
  episodeId: integer("episode_id").notNull(),
  instrumentId: text("instrument_id").notNull(),
  qty: qty("qty").notNull(),
  cash: money("cash").notNull(),
  risk: money("risk").notNull(),
});

/** Bewertung je simuliertem Kerzenschluss: Grundlage für Kurve, Tagesverlust und Drawdown. Append-only. */
export const equitySnapshots = pgTable(
  "equity_snapshot",
  {
    accountId: text("account_id").notNull().references(() => accounts.id),
    episodeId: integer("episode_id").notNull(),
    ts: ts("ts").notNull(),
    equity: money("equity").notNull(),
    cash: money("cash").notNull(),
    invested: money("invested").notNull(),
  },
  (t) => [primaryKey({ columns: [t.accountId, t.episodeId, t.ts] })],
);

/** Was aus einem Signal für ein Konto wurde (Order erteilt oder blockiert, mit Gründen). Append-only. */
export const signalOutcomes = pgTable(
  "signal_outcome",
  {
    signalId: uuid("signal_id").notNull().references(() => signals.id),
    accountId: text("account_id").notNull().references(() => accounts.id),
    episodeId: integer("episode_id").notNull(),
    /** ORDERED | BLOCKED */
    status: text("status").notNull(),
    reasons: jsonb("reasons").$type<string[]>().notNull(),
    values: jsonb("values").$type<Record<string, string>>().notNull(),
    createdAt: ts("created_at").notNull().defaultNow(),
  },
  (t) => [primaryKey({ columns: [t.signalId, t.accountId, t.episodeId] })],
);
