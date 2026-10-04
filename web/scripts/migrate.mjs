// Applies the Drizzle migrations in ./drizzle. Not part of the plain `build`; on Vercel it runs in
// `vercel-build`, because the (sensitive) database credentials are only available there.
// The engine checks schema_meta.version on start and refuses to run on a mismatch.
// Neon URLs use the HTTP driver, everything else (local Postgres, CI) node-postgres.
import { fileURLToPath } from "node:url";

const url = process.env.DATABASE_URL ?? process.env.POSTGRES_URL;
if (!url) {
  console.error("[migrate] DATABASE_URL is not set");
  process.exit(1);
}
const migrationsFolder = fileURLToPath(new URL("../drizzle", import.meta.url));

if (/\.neon\.tech|neon\.build/.test(url)) {
  const { neon } = await import("@neondatabase/serverless");
  const { drizzle } = await import("drizzle-orm/neon-http");
  const { migrate } = await import("drizzle-orm/neon-http/migrator");
  await migrate(drizzle(neon(url)), { migrationsFolder });
} else {
  const { default: pg } = await import("pg");
  const { drizzle } = await import("drizzle-orm/node-postgres");
  const { migrate } = await import("drizzle-orm/node-postgres/migrator");
  const pool = new pg.Pool({ connectionString: url, max: 1 });
  try {
    await migrate(drizzle(pool), { migrationsFolder });
  } finally {
    await pool.end();
  }
}
console.log("[migrate] migrations applied");
