import "server-only";
import { asc, eq } from "drizzle-orm";
import { db } from "@/db/client";
import { feedStatus, heartbeats, schemaMeta } from "@/db/schema";
import type { FeedRow, HeartbeatRow } from "@/lib/health";

export interface HealthData {
  heartbeats: HeartbeatRow[];
  feeds: FeedRow[];
  /** `schema_meta.version`, null when the row is missing. */
  dbSchemaVersion: number | null;
}

/** Heartbeats of the engine services, feed states and the schema version of the database. */
export async function loadHealth(): Promise<HealthData> {
  const [hb, feeds, meta] = await Promise.all([
    db().select().from(heartbeats).orderBy(asc(heartbeats.service)),
    db().select().from(feedStatus).orderBy(asc(feedStatus.instrumentId), asc(feedStatus.timeframe), asc(feedStatus.feed)),
    db().select({ version: schemaMeta.version }).from(schemaMeta).where(eq(schemaMeta.id, 1)),
  ]);
  return { heartbeats: hb, feeds, dbSchemaVersion: meta[0]?.version ?? null };
}
