"use client";

import { useActionState } from "react";
import { paperCommandAction } from "./actions";

/** "Virtuelles Konto anlegen": writes a PAPER_CREATE command; the engine creates the account within about a minute. */
export function CreateAccountForm() {
  const [state, action, pending] = useActionState(paperCommandAction, undefined);
  return (
    <form action={action} className="space-y-3">
      <input type="hidden" name="type" value="PAPER_CREATE" />
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-xs font-medium text-muted">
          Name
          <input name="name" required maxLength={60} placeholder="z. B. Trend 4h" className="mt-1 block w-full" autoComplete="off" />
        </label>
        <label className="block text-xs font-medium text-muted">
          Startkapital in USD (virtuell)
          <input name="startCash" required defaultValue="10000" inputMode="decimal" className="mt-1 block w-full tabular" autoComplete="off" />
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" disabled={pending} className="btn disabled:opacity-60">
          {pending ? "Sende …" : "Virtuelles Konto anlegen"}
        </button>
        {state?.error && (
          <p role="alert" className="text-sm text-critical">
            {state.error}
          </p>
        )}
        {state?.commandId && (
          <p role="status" className="text-sm text-ink-2">
            Befehl Nr. {state.commandId} gesendet – die Engine quittiert innert etwa einer Minute.
          </p>
        )}
      </div>
      <p className="text-xs text-ink-2">Keine Einzahlung, kein Handelskonto nötig. Alle Ausführungen sind simuliert.</p>
    </form>
  );
}
