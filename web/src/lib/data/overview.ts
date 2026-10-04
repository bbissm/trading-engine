import "server-only";
import { and, count, eq, gte, max } from "drizzle-orm";
import { db } from "@/db/client";
import { instruments, signals } from "@/db/schema";
import { attentionItems, heartbeatState, type AttentionItem, type FeedRow, type HeartbeatState } from "@/lib/health";
import { loadHealth } from "./health";

export interface SignalOps {
  /** Instruments in the approved universe. */
  universe: number;
  /** Creation time of the newest stored signal (any action). */
  lastSignalAt: Date | null;
  /** Decisions of the last 7 days by action. */
  buy7d: number;
  noTrade7d: number;
}

export interface OverviewData {
  /** Reference time of this snapshot (ages are relative to it). */
  now: number;
  attention: AttentionItem[];
  heartbeats: HeartbeatState[];
  feeds: FeedRow[];
  dbSchemaVersion: number | null;
  signalOps: SignalOps;
}

const WEEK = 7 * 86_400_000;

export async function loadSignalOps(now = Date.now()): Promise<SignalOps> {
  const since = new Date(now - WEEK);
  const [[uni], [last], byAction] = await Promise.all([
    db().select({ n: count() }).from(instruments).where(eq(instruments.inUniverse, true)),
    db().select({ at: max(signals.createdAt) }).from(signals),
    db().select({ action: signals.action, n: count() }).from(signals).where(and(gte(signals.createdAt, since))).groupBy(signals.action),
  ]);
  const n = (action: string) => byAction.find((r) => r.action === action)?.n ?? 0;
  return { universe: uni?.n ?? 0, lastSignalAt: last?.at ?? null, buy7d: n("BUY"), noTrade7d: n("NO_TRADE") };
}

/** Everything the overview shows. No money figures yet — and never a total across modes. */
export async function loadOverview(now = Date.now()): Promise<OverviewData> {
  const [health, signalOps] = await Promise.all([loadHealth(), loadSignalOps(now)]);
  return {
    now,
    attention: attentionItems(health, now),
    heartbeats: health.heartbeats.map((h) => heartbeatState(h, now)),
    feeds: health.feeds,
    dbSchemaVersion: health.dbSchemaVersion,
    signalOps,
  };
}
