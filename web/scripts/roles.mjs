// Creates/updates the database roles of the engine (least privilege, row-level security per mode) from
// scripts/roles.sql and prints the non-secret connection coordinates (host, database) for configuring the engine.
// Runs after the migrations in the Vercel build, where the database credentials are available.
// Skipped when TE_ENGINE_DB_PASSWORD or TE_LIVE_DB_PASSWORD is not set. Idempotent.
import { readFileSync } from "node:fs";

const url = process.env.DATABASE_URL_UNPOOLED ?? process.env.DATABASE_URL ?? process.env.POSTGRES_URL;
const passwords = { ENGINE_PASSWORD: process.env.TE_ENGINE_DB_PASSWORD, LIVE_PASSWORD: process.env.TE_LIVE_DB_PASSWORD };

if (!url || !passwords.ENGINE_PASSWORD || !passwords.LIVE_PASSWORD) {
  console.log("[roles] DATABASE_URL, TE_ENGINE_DB_PASSWORD or TE_LIVE_DB_PASSWORD not set – skipping engine roles");
  process.exit(0);
}
for (const [name, value] of Object.entries(passwords)) {
  if (!/^[A-Za-z0-9]{32,}$/.test(value)) {
    console.error(`[roles] ${name === "ENGINE_PASSWORD" ? "TE_ENGINE_DB_PASSWORD" : "TE_LIVE_DB_PASSWORD"} must be at least 32 alphanumeric characters`);
    process.exit(1);
  }
}

const template = readFileSync(new URL("./roles.sql", import.meta.url), "utf8");
const statements = template
  .replaceAll("{{ENGINE_PASSWORD}}", passwords.ENGINE_PASSWORD)
  .replaceAll("{{LIVE_PASSWORD}}", passwords.LIVE_PASSWORD)
  .split(/^-- @@.*$/m)
  .map((s) => s.trim())
  .filter((s) => s.replace(/^--.*$/gm, "").trim().length > 0);

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
console.log(`[roles] engine roles te_engine, te_live ready: host=${parsed.hostname} port=${parsed.port || 5432} database=${parsed.pathname.slice(1)}`);
