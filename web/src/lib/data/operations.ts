import "server-only";
import { desc, sql } from "drizzle-orm";
import { db } from "@/db/client";
import { commands } from "@/db/schema";
import { heartbeatState, type FeedRow, type HeartbeatState } from "@/lib/health";
import { loadHealth } from "./health";

export type CommandRow = typeof commands.$inferSelect;

export interface OperationsData {
  /** Reference time of this snapshot (ages are relative to it). */
  now: number;
  heartbeats: HeartbeatState[];
  feeds: FeedRow[];
  dbSchemaVersion: number | null;
  commands: CommandRow[];
}

export async function loadOperations(now = Date.now()): Promise<OperationsData> {
  const [health, cmds] = await Promise.all([loadHealth(), db().select().from(commands).orderBy(desc(commands.id)).limit(10)]);
  return { now, heartbeats: health.heartbeats.map((h) => heartbeatState(h, now)), feeds: health.feeds, dbSchemaVersion: health.dbSchemaVersion, commands: cmds };
}

/**
 * Writes a PING command for the engine plus its audit event. One statement, so both rows exist or none
 * (the Neon HTTP driver has no interactive transactions). The web app never calls the engine directly;
 * the engine picks the row up, sets status DONE and a result.
 */
export async function issuePing(user: string): Promise<void> {
  const actor = `user:${user}`;
  await db().execute(sql`
    with c as (
      insert into "command" ("type", "issued_by") values ('PING', ${actor}::text) returning "id"
    )
    insert into "audit_event" ("actor", "kind", "object", "data")
    select ${actor}::text, 'COMMAND_ISSUED', 'command:' || c."id", jsonb_build_object('type', 'PING', 'commandId', c."id") from c
  `);
}
