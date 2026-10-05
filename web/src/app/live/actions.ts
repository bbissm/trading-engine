"use server";

import { refresh } from "next/cache";
import { hasDb } from "@/db/client";
import { requireStepUp } from "@/lib/auth/step-up";
import { activateMandate, assignForeignPosition, decideLiveApproval, issueLiveCommand } from "@/lib/data/live";
import { userName } from "@/lib/session";
import { STEP_UP_COMMANDS, type LiveCommandType } from "./model";

export interface LiveActionState {
  error?: string;
  /** id of the written row (command, mandate or approval) */
  id?: number;
  done?: string;
}

const fail = (e: unknown, what: string): LiveActionState => {
  console.error(`[live-${what}]`, e);
  return { error: "Konnte nicht gespeichert werden." };
};

/**
 * Writes one LIVE_* command (table `command`). The web app never calls the engine and never sees Kraken keys.
 * Close-all, resume and the manual «no withdrawal right» confirmation need a fresh step-up (≤ 5 min); the engine
 * re-checks `step_up_at`. Pause, stop, emergency and suspend are risk-reducing and need only the login.
 */
export async function liveCommandAction(_prev: LiveActionState | undefined, form: FormData): Promise<LiveActionState> {
  if (!hasDb()) return { error: "Keine Datenbank verbunden." };
  const type = String(form.get("type") ?? "") as LiveCommandType;
  const confirmWithdraw = form.get("withdrawAbsentConfirmed") === "on";
  let stepUpAt: Date | undefined;
  if (STEP_UP_COMMANDS.includes(type) || confirmWithdraw) {
    const s = await requireStepUp(300);
    if (!s.ok) return { error: `Step-up nötig: ${s.reason}` };
    stepUpAt = s.at;
  }
  try {
    const r = await issueLiveCommand(userName(), { type, approvalId: form.get("approvalId") ?? undefined, decision: form.get("decision") ?? undefined, withdrawAbsentConfirmed: confirmWithdraw || undefined }, stepUpAt);
    if (!r.ok) return { error: r.error };
    refresh();
    return { id: r.id, done: "Befehl gesendet – die Live-Funktion quittiert innert etwa einer Minute." };
  } catch (e) {
    return fail(e, "command");
  }
}

/**
 * «Einer Strategie zuordnen» for a foreign position: step-up first (≤ 5 min), then server-side validation; writes
 * LIVE_ASSIGN_POSITION with `step_up_at` plus audit atomically. The engine re-checks and places the protective stop.
 */
export async function assignPositionAction(_prev: LiveActionState | undefined, form: FormData): Promise<LiveActionState> {
  if (!hasDb()) return { error: "Keine Datenbank verbunden." };
  const s = await requireStepUp(300);
  if (!s.ok) return { error: `Step-up nötig: ${s.reason}` };
  try {
    const r = await assignForeignPosition(
      userName(),
      { instrumentId: String(form.get("instrumentId") ?? ""), strategyVersionId: String(form.get("strategyVersionId") ?? ""), stop: String(form.get("stop") ?? "") },
      s.at,
    );
    if (!r.ok) return { error: r.error };
    refresh();
    return { id: r.id, done: "Zuordnung gesendet – die Live-Funktion prüft Kurs und Risikogrenzen und quittiert innert etwa einer Minute." };
  } catch (e) {
    return fail(e, "assign");
  }
}

/** «Mandat aktivieren»: step-up first, then server-side validation; writes the mandate (ACTIVE). */
export async function activateMandateAction(_prev: LiveActionState | undefined, form: FormData): Promise<LiveActionState> {
  if (!hasDb()) return { error: "Keine Datenbank verbunden." };
  const s = await requireStepUp(300);
  if (!s.ok) return { error: `Step-up nötig: ${s.reason}` };
  try {
    const r = await activateMandate(
      userName(),
      {
        autonomyLevel: form.get("autonomyLevel"),
        strategyIds: form.getAll("strategyIds").map(String),
        instrumentIds: form.getAll("instrumentIds").map(String),
        budget: String(form.get("budget") ?? ""),
        budgetConfirm: String(form.get("budgetConfirm") ?? ""),
        emergency: form.get("emergency"),
      },
      s.at,
    );
    if (!r.ok) return { error: r.error };
    refresh();
    return { id: r.id, done: "Mandat gespeichert. Die Live-Funktion übernimmt es nach Prüfung der Step-up-Zeit innert etwa einer Minute." };
  } catch (e) {
    return fail(e, "mandate");
  }
}

/** Live approval of a strategy version (APPROVED_LIVE after step-up) or taking it back (WITHDRAWN). */
export async function liveApprovalAction(_prev: LiveActionState | undefined, form: FormData): Promise<LiveActionState> {
  if (!hasDb()) return { error: "Keine Datenbank verbunden." };
  const decision = form.get("decision") === "WITHDRAWN" ? "WITHDRAWN" : "APPROVED_LIVE";
  let stepUpAt: Date | undefined;
  if (decision === "APPROVED_LIVE") {
    const s = await requireStepUp(300);
    if (!s.ok) return { error: `Step-up nötig: ${s.reason}` };
    stepUpAt = s.at;
  }
  try {
    const r = await decideLiveApproval(userName(), String(form.get("strategyVersionId") ?? ""), decision, stepUpAt);
    if (!r.ok) return { error: r.error };
    refresh();
    return { id: r.id, done: decision === "APPROVED_LIVE" ? "Für Live freigegeben (wirkt nur auf neue Entscheidungen)." : "Freigabe zurückgenommen." };
  } catch (e) {
    return fail(e, "approval");
  }
}
