import { NextResponse } from "next/server";
import { hasDb } from "@/db/client";
import { issuePaperCommand } from "@/lib/data/paper";
import { userName } from "@/lib/session";

export const dynamic = "force-dynamic";

/**
 * Same as the buttons in the UI, for scripts: writes one PAPER_* command (plus audit event) for the engine.
 * Body: { type, accountId?, name?, startCash? } — validated in `issuePaperCommand`; only PAPER_* types exist.
 * Protected by the app login like every other route (src/proxy.ts). No live order route exists.
 */
export async function POST(req: Request) {
  if (!hasDb()) return NextResponse.json({ error: "DATABASE_URL is not configured" }, { status: 503 });
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "JSON erwartet" }, { status: 400 });
  }
  const r = await issuePaperCommand(userName(), body);
  return r.ok ? NextResponse.json({ commandId: r.commandId }, { status: 201 }) : NextResponse.json({ error: r.error }, { status: 422 });
}
