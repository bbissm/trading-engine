"use server";

import { refresh } from "next/cache";
import { hasDb } from "@/db/client";
import { issueLabCommand } from "@/lib/data/lab";
import { userName } from "@/lib/session";

export interface LabActionState {
  error?: string;
  commandId?: number;
}

const field = (form: FormData, name: string) => {
  const v = form.get(name);
  return typeof v === "string" ? v : undefined;
};

/**
 * Writes one LAB_* or APPROVAL_DECIDE command (plus audit event) for the engine. Validation in `issueLabCommand`
 * (zod): only the listed kinds and strategy families, experiment/approval must exist and still be open, and the only
 * decisions are REJECTED and SHADOW — APPROVED_LIVE needs step-up auth and a live mandate (not built here).
 */
export async function labCommandAction(_prev: LabActionState | undefined, form: FormData): Promise<LabActionState> {
  if (!hasDb()) return { error: "Keine Datenbank verbunden." };
  const type = field(form, "type");
  const raw =
    type === "LAB_RUN"
      ? { type, kind: field(form, "kind"), strategy: field(form, "strategy") }
      : type === "APPROVAL_DECIDE"
        ? { type, approvalId: field(form, "approvalId"), decision: field(form, "decision"), note: field(form, "note") }
        : { type, experimentId: field(form, "experimentId") };
  try {
    const r = await issueLabCommand(userName(), raw);
    if (!r.ok) return { error: r.error };
    refresh();
    return { commandId: r.commandId };
  } catch (e) {
    console.error("[lab-command]", e);
    return { error: "Befehl konnte nicht gespeichert werden." };
  }
}
