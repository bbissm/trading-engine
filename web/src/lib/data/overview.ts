import "server-only";
import { and, count, eq, gte, max } from "drizzle-orm";
import { db } from "@/db/client";
import { instruments, signals } from "@/db/schema";
import { attentionItems, heartbeatState, type AttentionItem, type FeedRow, type HeartbeatState } from "@/lib/health";
import { paperAttention } from "@/lib/paper";
import { loadHealth } from "./health";
import { listPaperAccounts, listPaperCommands, type PaperAccountSummary } from "./paper";

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
  /** Paper accounts, each on its own — never summed across accounts and never combined with LIVE. */
  paper: PaperAccountSummary[];
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

/** Everything the overview shows. Money figures exist per paper account only — never a total across accounts or modes. */
export async function loadOverview(now = Date.now()): Promise<OverviewData> {
  const [health, signalOps, paper, pending] = await Promise.all([loadHealth(), loadSignalOps(now), listPaperAccounts(), listPaperCommands({ limit: 20, pendingOnly: true })]);
  const attention = [...attentionItems(health, now), ...paperAttention({ accounts: paper, pending }, now)];
  return {
    now,
    // critical first, order within a severity stays stable
    attention: attention.sort((a, b) => Number(b.severity === "critical") - Number(a.severity === "critical")),
    heartbeats: health.heartbeats.map((h) => heartbeatState(h, now)),
    feeds: health.feeds,
    dbSchemaVersion: health.dbSchemaVersion,
    signalOps,
    paper,
  };
}
