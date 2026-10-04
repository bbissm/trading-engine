import { NextResponse } from "next/server";
import { sql } from "drizzle-orm";
import { db, hasDb } from "@/db/client";

export const dynamic = "force-dynamic";

/**
 * Kompakter Betriebsstatus für Skripte (Bearer TE_API_TOKEN, siehe src/proxy.ts): Lebenszeichen, Paper-Konten,
 * jüngste Signale und Orders, offene Meldungen, Lernlabor. Nur lesend, keine Geheimnisse, keine Live-Daten.
 */
export async function GET() {
  if (!hasDb()) return NextResponse.json({ error: "DATABASE_URL is not configured" }, { status: 503 });
  const q = async (query: ReturnType<typeof sql>) => {
    const res = (await db().execute(query)) as unknown as { rows?: unknown[] } | unknown[];
    return Array.isArray(res) ? res : (res.rows ?? []);
  };
  const [heartbeats, accounts, signals, orders, alerts, experiments] = await Promise.all([
    q(sql`select service, last_seen, schema_version from heartbeat order by service`),
    q(sql`select a.id, ap.state, ap.reason, e.number as episode, e.start_cash, e.cash, e.realized, e.fees_paid, e.sim_through,
               (select equity from equity_snapshot s where s.episode_id = e.id order by ts desc limit 1) as equity,
               (select count(*) from trade t where t.episode_id = e.id and t.status = 'OPEN')::int as open_trades,
               (select count(*) from trade t where t.episode_id = e.id and t.status = 'CLOSED')::int as closed_trades
          from account a join autopilot ap on ap.account_id = a.id join episode e on e.account_id = a.id and e.ended_at is null
          where a.mode = 'PAPER' order by a.id`),
    q(sql`select instrument_id, timeframe, candle_close, strategy_version_id, action, regime, score from signal
          where action <> 'NO_TRADE' order by created_at desc limit 10`),
    q(sql`select id, account_id, instrument_id, role, type, side, qty, limit_price, stop_price, state, created_at from trade_order
          order by updated_at desc limit 10`),
    q(sql`select level, mode, kind, title, status, occurrences, updated_at from alert where status <> 'RESOLVED' order by updated_at desc limit 20`),
    q(sql`select id, kind, strategy, status, outcome, variants_tested, created_at, finished_at from experiment order by id desc limit 10`),
  ]);
  return NextResponse.json({ heartbeats, accounts, signals, orders, alerts, experiments });
}
