import { NextResponse, type NextRequest } from "next/server";
import { hasDb } from "@/db/client";
import { bridgeTokenOk, claimBridgeRequest } from "@/lib/news/bridge";

export const dynamic = "force-dynamic";

/** Die lokale Claude Code Bridge holt Arbeit: die älteste offene Anfrage oder null. Eigene Prüfung mit LLM_BRIDGE_TOKEN. */
export async function POST(request: NextRequest) {
  if (!bridgeTokenOk(request.headers.get("authorization"))) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  if (!hasDb()) return NextResponse.json({ request: null });
  const next = await claimBridgeRequest();
  if (!next) return NextResponse.json({ request: null });
  // nur das Wire-Format weitergeben (itemIds/day bleiben in der App)
  const { kind, model, system, prompt, schema, schemaName } = next.request;
  return NextResponse.json({ request: { id: next.id, kind, model, system, prompt, schema, schemaName } });
}
