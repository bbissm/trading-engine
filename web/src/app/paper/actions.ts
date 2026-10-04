"use server";

import { refresh } from "next/cache";
import { hasDb } from "@/db/client";
import { issuePaperCommand } from "@/lib/data/paper";
import { userName } from "@/lib/session";

export interface CommandActionState {
  error?: string;
  /** Id of the `command` row that was written. */
  commandId?: number;
}

/**
 * Writes one PAPER_* command (plus audit event) for the engine — the web app never calls the engine directly.
 * Input is validated with zod in `issuePaperCommand`: only the PAPER_* types exist there, the account must exist
 * with mode PAPER, start capital > 0 and ≤ 100 000 000, name 1–60 characters. Paper only: no real money, no order route.
 * TODO(E0-4): passkey + TOTP before any command that could touch a live account exists.
 */
export async function paperCommandAction(_prev: CommandActionState | undefined, form: FormData): Promise<CommandActionState> {
  if (!hasDb()) return { error: "Keine Datenbank verbunden." };
  const raw = { type: form.get("type"), accountId: form.get("accountId"), name: form.get("name"), startCash: form.get("startCash") };
  try {
    const r = await issuePaperCommand(userName(), raw);
    if (!r.ok) return { error: r.error };
    refresh();
    return { commandId: r.commandId };
  } catch (e) {
    console.error("[paper-command]", e);
    return { error: "Befehl konnte nicht gespeichert werden." };
  }
}
