"use server";

import { refresh } from "next/cache";
import { hasDb } from "@/db/client";
import { issuePing } from "@/lib/data/operations";
import { userName } from "@/lib/session";

/**
 * "Engine-Ping": writes a PING row into `command` (plus audit event). The engine answers within seconds
 * by setting status DONE and a result. Harmless by design — the only command the interim login may issue.
 * TODO(E0-4): every further command needs passkey + TOTP, risk-increasing ones a step-up.
 */
export async function pingAction(): Promise<{ error?: string }> {
  if (!hasDb()) return { error: "Keine Datenbank verbunden." };
  try {
    await issuePing(userName());
  } catch (e) {
    console.error("[ping]", e);
    return { error: "Befehl konnte nicht gespeichert werden." };
  }
  refresh();
  return {};
}
