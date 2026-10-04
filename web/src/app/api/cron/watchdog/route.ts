import { NextResponse } from "next/server";
import { hasDb } from "@/db/client";
import { cronAuthorized } from "@/lib/notify/cron";
import { runWatchdog } from "@/lib/notify/watchdog";

export const dynamic = "force-dynamic";

/**
 * Vercel cron (web/vercel.json, every 5 minutes): independent watchdog for the engine heartbeat.
 * Excluded from the login guard in src/proxy.ts; authenticates itself with CRON_SECRET.
 */
export async function GET(req: Request) {
  if (!cronAuthorized(req.headers.get("authorization"))) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  if (!hasDb()) return NextResponse.json({ error: "DATABASE_URL is not configured" }, { status: 503 });
  try {
    return NextResponse.json(await runWatchdog());
  } catch (e) {
    console.error("[watchdog]", e instanceof Error ? e.name : "Fehler");
    return NextResponse.json({ error: "watchdog failed" }, { status: 500 });
  }
}
