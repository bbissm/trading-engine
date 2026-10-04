import { neon } from "@neondatabase/serverless";
import { drizzle as drizzleNeon } from "drizzle-orm/neon-http";
import { drizzle as drizzlePg } from "drizzle-orm/node-postgres";
import type { PgDatabase, PgQueryResultHKT } from "drizzle-orm/pg-core";
import { Pool } from "pg";
import * as schema from "./schema";

export type Database = PgDatabase<PgQueryResultHKT, typeof schema>;

let instance: Database | undefined;

/** Neon-URLs nutzen den HTTP-Treiber (serverless-optimiert), alle anderen node-postgres. */
export const isNeonUrl = (url: string) => /\.neon\.tech|neon\.build/.test(url);

export const databaseUrl = () => process.env.DATABASE_URL ?? process.env.POSTGRES_URL;

/** Ohne DB zeigen die Seiten einen Einrichtungshinweis statt abzustürzen. */
export const hasDb = () => !!instance || !!databaseUrl();

/**
 * Lazy, damit Build und Seiten ohne konfigurierte DB funktionieren.
 * Hinweis: der Neon-HTTP-Treiber unterstützt keine interaktiven Transaktionen.
 */
export function db(): Database {
  if (!instance) {
    const url = databaseUrl();
    if (!url) throw new Error("DATABASE_URL is not configured");
    instance = (isNeonUrl(url)
      ? drizzleNeon(neon(url), { schema })
      : drizzlePg(new Pool({ connectionString: url, max: Number(process.env.DATABASE_POOL_MAX ?? 5) }), { schema })) as unknown as Database;
  }
  return instance;
}

/** Nur für Tests/Skripte: eigene DB-Instanz injizieren. */
export function setDb(database: Database | undefined) {
  instance = database;
}

export { schema };
