"use client";

import { useActionState, type ReactNode } from "react";
import { notifyCommandAction } from "./actions";

function Feedback({ state }: { state: { error?: string; commandId?: number } | undefined }) {
  if (state?.error)
    return (
      <p role="alert" className="text-sm text-critical">
        {state.error}
      </p>
    );
  if (state?.commandId)
    return (
      <p role="status" className="text-xs text-ink-2">
        Befehl Nr. {state.commandId} gespeichert – die Engine führt ihn innert etwa einer Minute aus.
      </p>
    );
  return null;
}

/** «Bestätigen»: stops the escalation of this alert. It approves nothing — no trade, no order. */
export function AckButton({ alertId, waiting }: { alertId: number; waiting: boolean }) {
  const [state, action, pending] = useActionState(notifyCommandAction, undefined);
  const sent = waiting || !!state?.commandId;
  return (
    <form action={action} className="flex flex-wrap items-center gap-2">
      <input type="hidden" name="type" value="ALERT_ACK" />
      <input type="hidden" name="alertId" value={alertId} />
      <button type="submit" disabled={pending || sent} className="btn disabled:opacity-60" title="Stoppt Wiederholung und Eskalation. Genehmigt keinen Trade.">
        {pending ? "Sende …" : sent ? "Bestätigung wartet auf Engine" : "Bestätigen"}
      </button>
      {state?.error && (
        <p role="alert" className="text-sm text-critical">
          {state.error}
        </p>
      )}
    </form>
  );
}

export function TestAlarmButton() {
  const [state, action, pending] = useActionState(notifyCommandAction, undefined);
  return (
    <form action={action} className="space-y-2">
      <input type="hidden" name="type" value="NOTIFY_TEST" />
      <button type="submit" disabled={pending} className="btn-ghost disabled:opacity-60">
        {pending ? "Sende …" : "Testalarm senden"}
      </button>
      <Feedback state={state} />
    </form>
  );
}

function SaveRow({ pending, label, state }: { pending: boolean; label: string; state: { error?: string; commandId?: number } | undefined }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <button type="submit" disabled={pending} className="btn-ghost disabled:opacity-60">
        {pending ? "Sende …" : label}
      </button>
      <Feedback state={state} />
    </div>
  );
}

export function QuietHoursForm({ start, end, criticalBypass }: { start: string; end: string; criticalBypass: boolean }) {
  const [state, action, pending] = useActionState(notifyCommandAction, undefined);
  return (
    <form action={action} className="space-y-3">
      <input type="hidden" name="type" value="SETTINGS_SET" />
      <input type="hidden" name="key" value="notify.quiet_hours" />
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-xs font-medium text-muted">
          Ruhezeit ab
          <input type="time" name="start" required defaultValue={start} className="mt-1 block w-full tabular" />
        </label>
        <label className="block text-xs font-medium text-muted">
          bis
          <input type="time" name="end" required defaultValue={end} className="mt-1 block w-full tabular" />
        </label>
      </div>
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" name="criticalBypass" defaultChecked={criticalBypass} className="mt-1" />
        <span>Kritische Alarme übergehen die Ruhezeit (empfohlen)</span>
      </label>
      <SaveRow pending={pending} label="Ruhezeit speichern" state={state} />
    </form>
  );
}

export function ChannelsForm({ enabled, labels }: { enabled: Record<string, boolean>; labels: Record<string, ReactNode> }) {
  const [state, action, pending] = useActionState(notifyCommandAction, undefined);
  return (
    <form action={action} className="space-y-3">
      <input type="hidden" name="type" value="SETTINGS_SET" />
      <input type="hidden" name="key" value="notify.channels" />
      <fieldset className="space-y-2">
        <legend className="sr-only">Eingeschaltete Kanäle</legend>
        {Object.entries(labels).map(([key, label]) => (
          <label key={key} className="flex items-start gap-2 text-sm">
            <input type="checkbox" name={key} defaultChecked={enabled[key]} className="mt-1" />
            <span>{label}</span>
          </label>
        ))}
      </fieldset>
      <SaveRow pending={pending} label="Kanäle speichern" state={state} />
    </form>
  );
}
