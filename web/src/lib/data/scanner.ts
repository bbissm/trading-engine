import "server-only";
import { asc, desc, eq } from "drizzle-orm";
import { db } from "@/db/client";
import { candles, featureSnapshots, feedStatus, instruments } from "@/db/schema";

export interface ScannerRow {
  id: string;
  name: string;
  kind: string;
  venue: string;
  quoteCurrency: string;
  /** Latest regime per timeframe (from feature_snapshot, latest candle close). */
  regimes: { timeframe: string; regime: string; candleClose: Date }[];
  feeds: { feed: string; timeframe: string; status: string }[];
  /** Close of the most recent stored candle (any timeframe); numeric as string. */
  lastClose: string | null;
  lastCloseTime: Date | null;
}

/** Order of the time levels in tables (unknown ones last, alphabetically). */
const TF_ORDER = ["1h", "4h", "1d", "1w"];
export const byTimeframe = (a: string, b: string) => {
  const ia = TF_ORDER.indexOf(a);
  const ib = TF_ORDER.indexOf(b);
  return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
};

/** Instruments of the approved universe with their stored analysis state. Nothing is computed here. */
export async function loadScanner(): Promise<ScannerRow[]> {
  const [inst, regimes, feeds, last] = await Promise.all([
    db().select().from(instruments).where(eq(instruments.inUniverse, true)).orderBy(asc(instruments.id)),
    db()
      .selectDistinctOn([featureSnapshots.instrumentId, featureSnapshots.timeframe], {
        instrumentId: featureSnapshots.instrumentId,
        timeframe: featureSnapshots.timeframe,
        regime: featureSnapshots.regime,
        candleClose: featureSnapshots.candleClose,
      })
      .from(featureSnapshots)
      .orderBy(featureSnapshots.instrumentId, featureSnapshots.timeframe, desc(featureSnapshots.candleClose), desc(featureSnapshots.createdAt)),
    db().select({ instrumentId: feedStatus.instrumentId, feed: feedStatus.feed, timeframe: feedStatus.timeframe, status: feedStatus.status }).from(feedStatus),
    db()
      .selectDistinctOn([candles.instrumentId], { instrumentId: candles.instrumentId, close: candles.close, closeTime: candles.closeTime })
      .from(candles)
      .orderBy(candles.instrumentId, desc(candles.closeTime), asc(candles.timeframe)),
  ]);

  return inst.map((i) => {
    const c = last.find((x) => x.instrumentId === i.id);
    return {
      id: i.id,
      name: i.name,
      kind: i.kind,
      venue: i.venue,
      quoteCurrency: i.quoteCurrency,
      regimes: regimes.filter((r) => r.instrumentId === i.id).sort((a, b) => byTimeframe(a.timeframe, b.timeframe)),
      feeds: feeds.filter((f) => f.instrumentId === i.id).sort((a, b) => byTimeframe(a.timeframe, b.timeframe)),
      lastClose: c?.close ?? null,
      lastCloseTime: c?.closeTime ?? null,
    };
  });
}
