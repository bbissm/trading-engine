"use client";

import { useActionState, useState } from "react";
import { activateMandateAction, liveApprovalAction, liveCommandAction, type LiveActionState } from "./actions";
import { EMERGENCY_POLICIES, LIVE_COMMAND_LABEL, type LiveCommandType } from "./model";

function Feedback({ state }: { state: LiveActionState | undefined }) {
  if (!state) return null;
  if (state.error)
    return (
      <p role="alert" className="mt-2 break-words text-sm text-critical">
        {state.error}
      </p>
    );
  return state.done ? (
    <p role="status" className="mt-2 text-sm">
      {state.done} {state.id ? <span className="text-ink-2">(Nr. {state.id})</span> : null}
    </p>
  ) : null;
}

interface ControlProps {
  state: string | null;
  effects: Record<string, string>;
  /** a live command is still PENDING */
  waiting: boolean;
  /** managed live positions (close-all needs at least one) */
  positions: number;
}

/**
 * Live controls. Each button first shows the concrete effect (incl. gap risk for close-all and emergency);
 * only the confirmation writes the command. Close-all and resume additionally need a fresh step-up.
 */
export function LiveControls({ state, effects, waiting, positions }: ControlProps) {
  const [open, setOpen] = useState<LiveCommandType | null>(null);
  const [result, action, pending] = useActionState(async (prev: LiveActionState | undefined, form: FormData) => {
    const r = await liveCommandAction(prev, form);
    if (!r.error) setOpen(null);
    return r;
  }, undefined);
  const s = state ?? "";
  const buttons: { type: LiveCommandType; enabled: boolean; danger?: boolean; stepUp?: boolean }[] = [
    { type: "LIVE_PAUSE", enabled: ["ACTIVE", "EMERGENCY", "READY", "RECOVERY", "ERROR"].includes(s) },
    { type: "LIVE_STOP", enabled: !!s && s !== "STOPPED" && (s !== "SETUP" || positions > 0) },
    { type: "LIVE_CLOSE_ALL", enabled: !!s && positions > 0, danger: true, stepUp: true },
    { type: "LIVE_EMERGENCY", enabled: !!s && (s !== "SETUP" || positions > 0), danger: true },
    { type: "LIVE_RESUME", enabled: ["ENTRIES_PAUSED", "EMERGENCY", "STOPPED", "WINDING_DOWN"].includes(s), stepUp: true },
  ];
  const current = buttons.find((b) => b.type === open);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Bedienung Live-Autopilot">
        {buttons.map((b) => (
          <button
            key={b.type}
            type="button"
            disabled={!b.enabled || waiting || pending}
            aria-expanded={open === b.type}
            onClick={() => setOpen(open === b.type ? null : b.type)}
            className={`${b.danger ? "btn" : "btn-ghost"} disabled:cursor-not-allowed disabled:opacity-45 ${open === b.type ? "ring-2 ring-accent" : ""}`}
          >
            {LIVE_COMMAND_LABEL[b.type]}
          </button>
        ))}
      </div>
      {waiting && <p className="text-xs text-ink-2">Ein Live-Befehl wartet auf die Live-Funktion. Weitere Befehle sind möglich, sobald er quittiert ist.</p>}
      {current && (
        <form action={action} className={`rounded-lg border p-3 ${current.danger ? "border-mode-live" : "border-line"} bg-surface-2`}>
          <input type="hidden" name="type" value={current.type} />
          <div className="text-sm font-semibold">
            {LIVE_COMMAND_LABEL[current.type]} – Wirkung <span className="text-xs font-normal">(Echtgeld)</span>
          </div>
          <p className="mt-1 text-sm">{effects[current.type]}</p>
          {current.stepUp && <p className="mt-1 text-xs text-ink-2">Verlangt eine Step-up-Anmeldung (höchstens 5 Minuten alt).</p>}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button type="submit" disabled={pending} className="btn disabled:opacity-60">
              {pending ? "Sende …" : `Bestätigen: ${LIVE_COMMAND_LABEL[current.type]}`}
            </button>
            <button type="button" className="btn-ghost" onClick={() => setOpen(null)} disabled={pending}>
              Abbrechen
            </button>
          </div>
        </form>
      )}
      <Feedback state={result} />
    </div>
  );
}

/** «Kraken-Konto verbinden»: the live function checks the key (no key ever passes through the browser). */
export function RegisterAccount({ connected }: { connected: boolean }) {
  const [result, action, pending] = useActionState(liveCommandAction, undefined);
  return (
    <form action={action} className="space-y-2">
      <input type="hidden" name="type" value="LIVE_ACCOUNT_REGISTER" />
      <label className="flex items-start gap-2 text-xs">
        <input type="checkbox" name="withdrawAbsentConfirmed" className="mt-0.5" />
        <span>Nur falls die automatische Prüfung «unklar» meldet: Ich bestätige, dass der Schlüssel kein Auszahlungsrecht (Withdraw Funds) hat. Verlangt Step-up.</span>
      </label>
      <button type="submit" disabled={pending} className="btn-ghost disabled:opacity-60">
        {pending ? "Sende …" : connected ? "Schlüsselrechte neu prüfen" : "Kraken-Konto verbinden"}
      </button>
      <Feedback state={result} />
    </form>
  );
}

interface MandateProps {
  eligible: string[];
  instruments: { id: string; name: string }[];
  ready: boolean;
  blockers: string[];
}

/** Mandate form: account (Kraken), autonomy level, APPROVED_LIVE versions only, instruments, budget (retyped), emergency policy. */
export function MandateForm({ eligible, instruments, ready, blockers }: MandateProps) {
  const [result, action, pending] = useActionState(activateMandateAction, undefined);
  return (
    <form action={action} className="space-y-4">
      <fieldset className="space-y-1">
        <legend className="text-xs font-medium text-muted">Konto</legend>
        <p className="text-sm">Kraken Spot (live-kraken) · Echtgeld</p>
      </fieldset>
      <fieldset className="space-y-1">
        <legend className="text-xs font-medium text-muted">Autonomiestufe</legend>
        {[
          [1, "1 · Informieren – Signale und Meldungen, keine Orders"],
          [2, "2 · Vorbereiten – Order wartet auf deine Freigabe; ohne Antwort verfällt sie"],
          [3, "3 · Mandat – automatischer Handel innerhalb von Budget und Grenzen"],
        ].map(([v, label]) => (
          <label key={v} className="flex items-start gap-2 text-sm">
            <input type="radio" name="autonomyLevel" value={v} defaultChecked={v === 2} className="mt-1" />
            <span>{label}</span>
          </label>
        ))}
      </fieldset>
      <fieldset className="space-y-1">
        <legend className="text-xs font-medium text-muted">Strategieversionen (nur APPROVED_LIVE mit G1–G3 bestanden)</legend>
        {eligible.length ? (
          eligible.map((id) => (
            <label key={id} className="flex items-center gap-2 text-sm">
              <input type="checkbox" name="strategyIds" value={id} />
              <span className="break-all font-mono text-xs">{id}</span>
            </label>
          ))
        ) : (
          <p className="text-sm text-ink-2">Keine Version ist für Live freigegeben.</p>
        )}
      </fieldset>
      <fieldset className="space-y-1">
        <legend className="text-xs font-medium text-muted">Instrumente</legend>
        <div className="grid gap-1 sm:grid-cols-2">
          {instruments.map((i) => (
            <label key={i.id} className="flex items-center gap-2 text-sm">
              <input type="checkbox" name="instrumentIds" value={i.id} />
              <span>{i.name}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-xs font-medium text-muted">
          Budget in USD (Echtgeld, Kapitalgrenze des Mandats)
          <input name="budget" required inputMode="decimal" autoComplete="off" className="mt-1 block w-full tabular" />
        </label>
        <label className="block text-xs font-medium text-muted">
          Budget zur Bestätigung erneut eingeben
          <input name="budgetConfirm" required inputMode="decimal" autoComplete="off" className="mt-1 block w-full tabular" />
        </label>
      </div>
      <fieldset className="space-y-1">
        <legend className="text-xs font-medium text-muted">Notfallpolicy</legend>
        {(Object.keys(EMERGENCY_POLICIES) as (keyof typeof EMERGENCY_POLICIES)[]).map((k) => (
          <label key={k} className="flex items-start gap-2 text-sm">
            <input type="radio" name="emergency" value={k} defaultChecked={k === "HOLD_PROTECTED"} className="mt-1" />
            <span>
              {EMERGENCY_POLICIES[k]}
              {k === "HOLD_PROTECTED" ? " (Standard: Positionen bleiben mit Stop-Loss bei Kraken)" : " (Positionen werden marktnah verkauft – Kurslückenrisiko)"}
            </span>
          </label>
        ))}
      </fieldset>
      {!ready && (
        <div role="note" className="rounded-lg border border-line bg-surface-2 p-3 text-xs">
          «Mandat aktivieren» erscheint erst, wenn alle Voraussetzungen erfüllt sind. Offen: {blockers.join(" · ")}
        </div>
      )}
      {ready && (
        <button type="submit" disabled={pending} className="btn disabled:opacity-60">
          {pending ? "Prüfe …" : "Mandat aktivieren (Step-up)"}
        </button>
      )}
      <Feedback state={result} />
    </form>
  );
}

/** APPROVED_LIVE for a version whose G1–G3 passed (step-up), or taking an approval back. */
export function LiveApprovalButton({ strategyVersionId, approved, selectable }: { strategyVersionId: string; approved: boolean; selectable: boolean }) {
  const [result, action, pending] = useActionState(liveApprovalAction, undefined);
  return (
    <form action={action} className="flex flex-wrap items-center gap-2">
      <input type="hidden" name="strategyVersionId" value={strategyVersionId} />
      <input type="hidden" name="decision" value={approved ? "WITHDRAWN" : "APPROVED_LIVE"} />
      <button type="submit" disabled={pending || (!approved && !selectable)} className="btn-ghost disabled:cursor-not-allowed disabled:opacity-45" title={!approved && !selectable ? "G1–G3 nicht bestanden" : undefined}>
        {approved ? "Freigabe zurücknehmen" : "Für Live freigeben (Step-up)"}
      </button>
      <Feedback state={result} />
    </form>
  );
}

/** Autonomy level 2: approve or reject one prepared live order before it expires (silence is not permission). */
export function OrderApprovalButtons({ approvalId }: { approvalId: number }) {
  const [result, action, pending] = useActionState(liveCommandAction, undefined);
  return (
    <form action={action} className="flex flex-wrap items-center gap-2">
      <input type="hidden" name="type" value="ORDER_APPROVAL_DECIDE" />
      <input type="hidden" name="approvalId" value={approvalId} />
      <button type="submit" name="decision" value="APPROVED" disabled={pending} className="btn disabled:opacity-60">
        Freigeben
      </button>
      <button type="submit" name="decision" value="REJECTED" disabled={pending} className="btn-ghost disabled:opacity-60">
        Ablehnen
      </button>
      <Feedback state={result} />
    </form>
  );
}

/** Suspend the active mandate (risk-reducing, no step-up). */
export function SuspendMandate() {
  const [result, action, pending] = useActionState(liveCommandAction, undefined);
  return (
    <form action={action}>
      <input type="hidden" name="type" value="MANDATE_SUSPEND" />
      <button type="submit" disabled={pending} className="btn-ghost disabled:opacity-60">
        Mandat aussetzen
      </button>
      <Feedback state={result} />
    </form>
  );
}
