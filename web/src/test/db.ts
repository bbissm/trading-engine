import { PGlite } from "@electric-sql/pglite";
import { drizzle } from "drizzle-orm/pglite";
import { migrate } from "drizzle-orm/pglite/migrator";
import { setDb, schema, type Database } from "@/db/client";

/** Frische In-Memory-Postgres-Instanz mit allen Migrationen aus ./drizzle (dieselben SQL-Dateien wie in Produktion). */
export async function createTestDb(): Promise<Database> {
  const client = await PGlite.create();
  const database = drizzle(client, { schema });
  await migrate(database, { migrationsFolder: "./drizzle" });
  setDb(database as unknown as Database);
  return database as unknown as Database;
}
