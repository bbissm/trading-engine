"use client";

import { useActionState, useState } from "react";
import { labCommandAction, type LabActionState } from "./actions";

const KIND_LABEL: Record<string, string> = {
  BACKTEST: "Backtest",
  WALK_FORWARD: "Walk-forward",
  OPTIMIZE: "Begrenzte Optimierung",
  HOLDOUT: "Holdout-Prüfung (einmalig je Familie)",
  METALABEL: "Meta-Labeling",
};

function Result({ state, pending }: { state: LabActionState | undefined; pending: boolean }) {
  if (pending) return <p className="text-sm text-ink-2">Sende …</p>;
  if (state?.error)
    return (
      <p role="alert" className="text-sm text-critical">
        {state.error}
      </p>
    );
  if (state?.commandId)
    return (
      <p role="status" className="text-sm text-ink-2">
        Befehl Nr. {state.commandId} gespeichert – die Engine übernimmt ihn beim nächsten Lauf.
      </p>
    );
  return null;
}

/** «Experiment starten»: writes LAB_RUN {kind, strategy}. The lab budgets are enforced by the engine. */
export function StartExperimentForm({ kinds, strategies }: { kinds: readonly string[]; strategies: readonly string[] }) {
  const [state, action, pending] = useActionState(labCommandAction, undefined);
  return (
    <form action={action} className="space-y-3">
      <input type="hidden" name="type" value="LAB_RUN" />
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-xs font-medium text-muted">
          Art
          <select name="kind" defaultValue="WALK_FORWARD" className="mt-1 block w-full">
            {kinds.map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k] ?? k}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs font-medium text-muted">
          Strategiefamilie
          <select name="strategy" className="mt-1 block w-full">
            {strategies.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" disabled={pending} className="btn disabled:opacity-60">
          Experiment starten
        </button>
        <Result state={state} pending={pending} />
      </div>
      <p className="text-xs text-ink-2">Das Labor rechnet nur mit historischen Daten; es entsteht keine Order. Ein Ergebnis wird nie automatisch aktiv.</p>
    </form>
  );
}

/** Pause / resume / abort one experiment — each with a confirmation step. */
export function ExperimentControls({ experimentId, status }: { experimentId: number; status: string }) {
  const [open, setOpen] = useState<string | null>(null);
  const [state, action, pending] = useActionState(async (prev: LabActionState | undefined, form: FormData) => {
    const r = await labCommandAction(prev, form);
    if (!r.error) setOpen(null);
    return r;
  }, undefined);
  const finished = status === "DONE" || status === "ABORTED";
  if (finished) return <p className="text-xs text-ink-2">Lauf abgeschlossen – keine Bedienung mehr möglich.</p>;
  const buttons = [
    { type: "LAB_PAUSE", label: "Pausieren", enabled: status === "RUNNING", effect: "Der Lauf hält nach der aktuellen Variante an; bisherige Varianten bleiben gezählt." },
    { type: "LAB_RESUME", label: "Fortsetzen", enabled: status === "PAUSED", effect: "Der Lauf rechnet weiter, innerhalb der verbleibenden Budgets." },
    { type: "LAB_ABORT", label: "Abbrechen", enabled: true, effect: "Der Lauf endet mit Ergebnis ABGEBROCHEN. Alle bisher getesteten Varianten bleiben im Protokoll und zählen für die Mehrfachtest-Korrektur." },
  ];
  const current = buttons.find((b) => b.type === open);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {buttons.map((b) => (
          <button key={b.type} type="button" disabled={!b.enabled || pending} onClick={() => setOpen(open === b.type ? null : b.type)} className={`btn-ghost disabled:cursor-not-allowed disabled:opacity-45 ${open === b.type ? "ring-2 ring-accent" : ""}`}>
            {b.label}
          </button>
        ))}
      </div>
      {current && (
        <form action={action} className="rounded-lg border border-line bg-surface-2 p-3">
          <input type="hidden" name="type" value={current.type} />
          <input type="hidden" name="experimentId" value={experimentId} />
          <p className="text-sm">{current.effect}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button type="submit" disabled={pending} className="btn disabled:opacity-60">
              Bestätigen: {current.label}
            </button>
            <button type="button" className="btn-ghost" onClick={() => setOpen(null)}>
              Zurück
            </button>
          </div>
        </form>
      )}
      <Result state={state} pending={pending} />
    </div>
  );
}

/** Decision on a proposal: only «Ablehnen» and «Als Shadow beobachten». Live approval is not offered here. */
export function ApprovalDecisionForm({ approvalId }: { approvalId: number }) {
  const [state, action, pending] = useActionState(labCommandAction, undefined);
  return (
    <form action={action} className="space-y-2">
      <input type="hidden" name="type" value="APPROVAL_DECIDE" />
      <input type="hidden" name="approvalId" value={approvalId} />
      <label className="block text-xs font-medium text-muted">
        Notiz (optional)
        <input name="note" maxLength={500} className="mt-1 block w-full" autoComplete="off" />
      </label>
      <div className="flex flex-wrap gap-2">
        <button type="submit" name="decision" value="SHADOW" disabled={pending} className="btn disabled:opacity-60">
          Als Shadow beobachten
        </button>
        <button type="submit" name="decision" value="REJECTED" disabled={pending} className="btn-ghost disabled:opacity-60">
          Ablehnen
        </button>
      </div>
      <p className="text-xs text-ink-2">Live-Freigabe erfordert Step-up-Anmeldung und ein Live-Mandat (noch nicht eingerichtet).</p>
      <Result state={state} pending={pending} />
    </form>
  );
}
