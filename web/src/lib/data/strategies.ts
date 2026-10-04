import "server-only";
import { asc, count, eq, max, sql } from "drizzle-orm";
import { db } from "@/db/client";
import { signals, strategyVersions } from "@/db/schema";

export interface StrategyVersionRow {
  id: string;
  strategy: string;
  version: number;
  params: Record<string, unknown>;
  regimeRuleVersion: string;
  lifecycleStatus: string;
  createdAt: Date;
  /** Stored decisions of this version (incl. NO_TRADE) and how many of them were BUY. */
  decisions: number;
  buys: number;
  lastDecisionAt: Date | null;
}

/** Strategy versions as registered by the engine, with plain counts of their stored decisions. No performance figures. */
export async function listStrategyVersions(): Promise<StrategyVersionRow[]> {
  return db()
    .select({
      id: strategyVersions.id,
      strategy: strategyVersions.strategy,
      version: strategyVersions.version,
      params: strategyVersions.params,
      regimeRuleVersion: strategyVersions.regimeRuleVersion,
      lifecycleStatus: strategyVersions.lifecycleStatus,
      createdAt: strategyVersions.createdAt,
      decisions: count(signals.id),
      buys: sql<number>`count(*) filter (where ${signals.action} = 'BUY')`.mapWith(Number),
      lastDecisionAt: max(signals.createdAt),
    })
    .from(strategyVersions)
    .leftJoin(signals, eq(signals.strategyVersionId, strategyVersions.id))
    .groupBy(strategyVersions.id)
    .orderBy(asc(strategyVersions.id));
}

export async function loadStrategies(now = Date.now()): Promise<{ now: number; versions: StrategyVersionRow[] }> {
  return { now, versions: await listStrategyVersions() };
}
