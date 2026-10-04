import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

/**
 * Non-secret connection coordinates of the database (direct, unpooled endpoint) for configuring the
 * engine server. No credentials. Protected by the app login like every other route (src/proxy.ts).
 */
export function GET() {
  const url = process.env.DATABASE_URL_UNPOOLED ?? process.env.DATABASE_URL ?? process.env.POSTGRES_URL;
  if (!url) return NextResponse.json({ error: "DATABASE_URL is not configured" }, { status: 503 });
  const parsed = new URL(url);
  return NextResponse.json({ host: parsed.hostname, port: Number(parsed.port || 5432), database: parsed.pathname.slice(1), engineRole: "te_engine" });
}
