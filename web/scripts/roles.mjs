// Creates/updates the database role the engine connects with (least privilege) and prints the
// non-secret connection coordinates (host, database) so the engine server can be configured.
// Runs after the migrations in the Vercel build, where the database credentials are available.
// Skipped when TE_ENGINE_DB_PASSWORD is not set. Idempotent.
const url = process.env.DATABASE_URL_UNPOOLED ?? process.env.DATABASE_URL ?? process.env.POSTGRES_URL;
const password = process.env.TE_ENGINE_DB_PASSWORD;
const ROLE = "te_engine";

if (!url || !password) {
  console.log("[roles] DATABASE_URL or TE_ENGINE_DB_PASSWORD not set – skipping engine role");
  process.exit(0);
}
if (!/^[A-Za-z0-9]{32,}$/.test(password)) {
  console.error("[roles] TE_ENGINE_DB_PASSWORD must be at least 32 alphanumeric characters");
  process.exit(1);
}

// Tables the engine may write. Everything is readable; `signal`, `candle`, `feature_snapshot` and
// `audit_event` are insert-only (append-only), `command` may only be acknowledged.
const GRANTS = {
  instrument: "insert, update",
  feed_status: "insert, update",
  heartbeat: "insert, update",
  strategy_version: "insert",
  candle: "insert",
  feature_snapshot: "insert",
  signal: "insert",
  audit_event: "insert",
  // Paper-Handel (Etappe 2). Getrennte Rollen für Paper/Live/Lab folgen mit dem Live-Orderweg (Etappe 4).
  account: "insert",
  episode: "insert, update",
  autopilot: "insert, update",
  trade: "insert, update",
  trade_order: "insert, update",
  fill: "insert",
  reservation: "insert, update, delete",
  equity_snapshot: "insert",
  signal_outcome: "insert",
  // Pause-Befehl aus einer Telegram-Schaltfläche (nur Paper, risikosenkend) wird als eigener Befehl eingereiht
  command: "insert",
  // Schema v3: Benachrichtigung, FX, Lernlabor, Freigaben, Live-Vorbereitung
  fx_rate: "insert, update",
  setting: "insert, update",
  alert: "insert, update",
  alert_delivery: "insert",
  channel_status: "insert, update",
  experiment: "insert, update",
  experiment_trial: "insert",
  gate_evaluation: "insert",
  holdout_access: "insert",
  approval: "insert",
  mandate: "update",
  policy_change: "update",
  reconciliation: "insert",
  order_approval: "insert, update",
  execution_metric: "insert",
};
// Spaltenrechte: die Engine darf nur den Lebenszyklus einer Strategieversion fortschreiben, nie ihre Parameter.
const COLUMN_GRANTS = [
  `grant update (lifecycle_status) on strategy_version to te_engine`,
  `grant update (status, result, handled_at) on command to te_engine`,
  `grant update (permissions) on account to te_engine`,
];

const statements = [
  `do $$ begin
     if not exists (select from pg_roles where rolname = '${ROLE}') then
       create role ${ROLE} login password '${password}';
     else
       alter role ${ROLE} login password '${password}';
     end if;
   end $$`,
  `grant usage on schema public to ${ROLE}`,
  `revoke all on all tables in schema public from ${ROLE}`,
  `grant select on all tables in schema public to ${ROLE}`,
  `grant usage, select on all sequences in schema public to ${ROLE}`,
  ...Object.entries(GRANTS).map(([table, privileges]) => `grant ${privileges} on "${table}" to ${ROLE}`),
  ...COLUMN_GRANTS,
];

if (/\.neon\.tech|neon\.build/.test(url)) {
  const { neon } = await import("@neondatabase/serverless");
  const sql = neon(url);
  for (const statement of statements) await sql.query(statement);
} else {
  const { default: pg } = await import("pg");
  const client = new pg.Client({ connectionString: url });
  await client.connect();
  try {
    for (const statement of statements) await client.query(statement);
  } finally {
    await client.end();
  }
}

const parsed = new URL(url);
console.log(`[roles] engine role "${ROLE}" ready: host=${parsed.hostname} port=${parsed.port || 5432} database=${parsed.pathname.slice(1)}`);
