import { NextResponse } from "next/server";
import { requireStepUp } from "@/lib/auth/step-up";
import { effectiveMaxAge, STEP_UP_DEFAULT_MAX_AGE_SECONDS } from "@/lib/auth/step-up-core";

export const dynamic = "force-dynamic";

/**
 * Stand der Step-up-Bestätigung dieser Sitzung (für die Oberfläche): `{ ok, at?, expiresAt?, reason? }`.
 * Nur mit Sitzung erreichbar (Proxy); bestätigt selbst nichts.
 */
export async function GET() {
  const r = await requireStepUp();
  if (!r.ok) return NextResponse.json({ ok: false, reason: r.reason }, { headers: { "cache-control": "no-store" } });
  const expiresAt = new Date(r.at.getTime() + effectiveMaxAge(STEP_UP_DEFAULT_MAX_AGE_SECONDS) * 1000);
  return NextResponse.json({ ok: true, at: r.at.toISOString(), expiresAt: expiresAt.toISOString() }, { headers: { "cache-control": "no-store" } });
}
