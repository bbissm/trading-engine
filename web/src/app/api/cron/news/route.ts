import { NextResponse } from "next/server";
import { hasDb } from "@/db/client";
import { runNewsCycle } from "@/lib/news/pipeline";
import { cronAuthorized } from "@/lib/notify/cron";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

/**
 * Vercel cron (web/vercel.json, alle 4 Stunden): RSS-Feeds holen, neue Meldungen an Claude (Bridge) geben,
 * einmal am Tag das Markt-Briefing anfragen. Vom Login ausgenommen (src/proxy.ts), prüft CRON_SECRET selbst.
 */
export async function GET(req: Request) {
  if (!cronAuthorized(req.headers.get("authorization"))) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  if (!hasDb()) return NextResponse.json({ error: "DATABASE_URL is not configured" }, { status: 503 });
  try {
    return NextResponse.json(await runNewsCycle());
  } catch (e) {
    console.error("[news]", e instanceof Error ? e.message : "Fehler");
    return NextResponse.json({ error: "news cycle failed" }, { status: 500 });
  }
}
