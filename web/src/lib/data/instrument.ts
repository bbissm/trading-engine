import "server-only";
import { and, desc, eq, gte } from "drizzle-orm";
import { db } from "@/db/client";
import { candles, featureSnapshots, instruments, signals } from "@/db/schema";
import { listSignals, type SignalRow } from "./signals";

export const TIMEFRAMES = ["4h", "1d"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];
export const parseTimeframe = (v: string | undefined): Timeframe => (TIMEFRAMES.includes(v as Timeframe) ? (v as Timeframe) : "4h");

/** Chart input. `time` = candle open in UTC seconds. Prices become numbers ONLY for drawing — no calculations on them. */
export interface ChartCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}
export interface ChartMarker {
  time: number;
  text: string;
}
export interface ChartRegime {
  time: number;
  regime: string;
}

export interface InstrumentData {
  instrument: typeof instruments.$inferSelect;
  timeframe: Timeframe;
  candles: ChartCandle[];
  /** BUY markers from stored rows of `signal` only. */
  markers: ChartMarker[];
  regimes: ChartRegime[];
  /** Latest stored BUY signal of this timeframe (stop/entry lines). */
  latestBuy: { candleClose: Date; entry: string | null; stop: string | null } | null;
  /** Recent decisions (all actions) of this timeframe. */
  decisions: SignalRow[];
  lastClose: string | null;
  lastCloseTime: Date | null;
}

const CANDLE_LIMIT = 1000;

export async function loadInstrument(id: string, timeframe: Timeframe): Promise<InstrumentData | null> {
  const [instrument] = await db().select().from(instruments).where(eq(instruments.id, id));
  if (!instrument) return null;

  const rows = (
    await db()
      .select({ openTime: candles.openTime, closeTime: candles.closeTime, open: candles.open, high: candles.high, low: candles.low, close: candles.close })
      .from(candles)
      .where(and(eq(candles.instrumentId, id), eq(candles.timeframe, timeframe)))
      .orderBy(desc(candles.openTime))
      .limit(CANDLE_LIMIT)
  ).reverse();

  const from = rows[0]?.closeTime ?? new Date();
  const [snapshots, buys, decisions] = await Promise.all([
    db()
      .select({ candleClose: featureSnapshots.candleClose, regime: featureSnapshots.regime, createdAt: featureSnapshots.createdAt })
      .from(featureSnapshots)
      .where(and(eq(featureSnapshots.instrumentId, id), eq(featureSnapshots.timeframe, timeframe), gte(featureSnapshots.candleClose, from)))
      .orderBy(featureSnapshots.candleClose, featureSnapshots.createdAt),
    db()
      .select({ candleClose: signals.candleClose, entry: signals.entry, stop: signals.stop, score: signals.score })
      .from(signals)
      .where(and(eq(signals.instrumentId, id), eq(signals.timeframe, timeframe), eq(signals.action, "BUY")))
      .orderBy(desc(signals.candleClose), desc(signals.createdAt))
      .limit(CANDLE_LIMIT),
    listSignals({ instrumentId: id, timeframe, includeNoTrade: true, limit: 30 }),
  ]);

  // signals and snapshots refer to the candle close; the chart positions bars by their open time
  const openByClose = new Map(rows.map((r) => [r.closeTime.getTime(), Math.floor(r.openTime.getTime() / 1000)]));
  const markerTimes = new Set<number>();
  for (const b of buys) {
    const t = openByClose.get(b.candleClose.getTime());
    if (t !== undefined) markerTimes.add(t);
  }
  // several regime rule versions for one candle: the snapshot created last wins
  const regimeByTime = new Map<number, string>();
  for (const s of snapshots) {
    const t = openByClose.get(s.candleClose.getTime());
    if (t !== undefined) regimeByTime.set(t, s.regime);
  }

  const lastRow = rows.at(-1);
  return {
    instrument,
    timeframe,
    candles: rows.map((r) => ({ time: Math.floor(r.openTime.getTime() / 1000), open: Number(r.open), high: Number(r.high), low: Number(r.low), close: Number(r.close) })),
    markers: [...markerTimes].sort((a, b) => a - b).map((time) => ({ time, text: "BUY" })),
    regimes: [...regimeByTime].sort((a, b) => a[0] - b[0]).map(([time, regime]) => ({ time, regime })),
    latestBuy: buys[0] ? { candleClose: buys[0].candleClose, entry: buys[0].entry, stop: buys[0].stop } : null,
    decisions,
    lastClose: lastRow?.close ?? null,
    lastCloseTime: lastRow?.closeTime ?? null,
  };
}
