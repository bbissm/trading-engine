import "server-only";
import { and, desc, eq, ne, type SQL } from "drizzle-orm";
import { db } from "@/db/client";
import { instruments, signals } from "@/db/schema";

export interface SignalRow {
  id: string;
  instrumentId: string;
  instrumentName: string | null;
  quoteCurrency: string | null;
  timeframe: string;
  candleClose: Date;
  createdAt: Date;
  strategyVersionId: string;
  action: string;
  regime: string;
  score: number | null;
  /** numeric columns arrive as strings — format with `decimal`/`price`, never float math */
  entry: string | null;
  stop: string | null;
  target: string | null;
  triggers: string[];
  counter: string[];
}

export interface SignalFilter {
  /** Default false: only actionable signals (action != NO_TRADE). */
  includeNoTrade?: boolean;
  instrumentId?: string;
  timeframe?: string;
  limit?: number;
}

/** Latest stored signals, newest candle first. Read-only: signals are append-only and never recomputed here. */
export async function listSignals({ includeNoTrade = false, instrumentId, timeframe, limit = 100 }: SignalFilter = {}): Promise<SignalRow[]> {
  const where: SQL[] = [];
  if (!includeNoTrade) where.push(ne(signals.action, "NO_TRADE"));
  if (instrumentId) where.push(eq(signals.instrumentId, instrumentId));
  if (timeframe) where.push(eq(signals.timeframe, timeframe));
  return db()
    .select({
      id: signals.id,
      instrumentId: signals.instrumentId,
      instrumentName: instruments.name,
      quoteCurrency: instruments.quoteCurrency,
      timeframe: signals.timeframe,
      candleClose: signals.candleClose,
      createdAt: signals.createdAt,
      strategyVersionId: signals.strategyVersionId,
      action: signals.action,
      regime: signals.regime,
      score: signals.score,
      entry: signals.entry,
      stop: signals.stop,
      target: signals.target,
      triggers: signals.triggers,
      counter: signals.counter,
    })
    .from(signals)
    .leftJoin(instruments, eq(instruments.id, signals.instrumentId))
    .where(where.length ? and(...where) : undefined)
    .orderBy(desc(signals.candleClose), desc(signals.createdAt))
    .limit(limit);
}
