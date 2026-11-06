import { NextResponse, type NextRequest } from "next/server";
import { bridgeResultSchema, bridgeTokenOk, completeBridgeRequest } from "@/lib/news/bridge";
import { processAnswers } from "@/lib/news/pipeline";

export const dynamic = "force-dynamic";

/** Die lokale Claude Code Bridge liefert eine Antwort (oder den Fehler von `claude -p`); sie wird sofort verarbeitet. */
export async function POST(request: NextRequest) {
  if (!bridgeTokenOk(request.headers.get("authorization"))) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  const parsed = bridgeResultSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: parsed.error.message }, { status: 400 });
  const stored = await completeBridgeRequest(parsed.data);
  if (!stored) return NextResponse.json({ error: "request not claimed" }, { status: 409 });
  // Verarbeitung scheitert nie die Annahme: sonst holt der nächste Nachrichtenlauf sie nach
  await processAnswers().catch((e) => console.error("[llm-bridge] verarbeiten", e instanceof Error ? e.message : e));
  return NextResponse.json({ ok: true });
}
