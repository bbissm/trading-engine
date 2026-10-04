"use server";

import { refresh } from "next/cache";
import { hasDb } from "@/db/client";
import { issueNotifyCommand } from "@/lib/data/alerts";
import { userName } from "@/lib/session";

export interface NotifyActionState {
  error?: string;
  commandId?: number;
}

/**
 * Writes one notification command (ALERT_ACK, NOTIFY_TEST, SETTINGS_SET) plus audit event for the engine — the web
 * app never calls the engine directly. Validated with zod in `issueNotifyCommand`. Acknowledging stops the escalation
 * of an alert; it never approves a trade or an order.
 */
export async function notifyCommandAction(_prev: NotifyActionState | undefined, form: FormData): Promise<NotifyActionState> {
  if (!hasDb()) return { error: "Keine Datenbank verbunden." };
  const type = form.get("type");
  let raw: unknown;
  if (type === "ALERT_ACK") raw = { type, alertId: form.get("alertId") };
  else if (type === "NOTIFY_TEST") raw = { type };
  else if (type === "SETTINGS_SET" && form.get("key") === "notify.quiet_hours")
    raw = { type, key: "notify.quiet_hours", value: { start: form.get("start"), end: form.get("end"), critical_bypass: form.get("criticalBypass") === "on" } };
  else if (type === "SETTINGS_SET" && form.get("key") === "notify.channels")
    raw = { type, key: "notify.channels", value: { TELEGRAM: form.get("TELEGRAM") === "on", PUSHOVER: form.get("PUSHOVER") === "on", EMAIL: form.get("EMAIL") === "on" } };
  else return { error: "Unbekannter Befehl." };
  try {
    const r = await issueNotifyCommand(userName(), raw);
    if (!r.ok) return { error: r.error };
    refresh();
    return { commandId: r.commandId };
  } catch (e) {
    console.error("[notify-command]", e);
    return { error: "Befehl konnte nicht gespeichert werden." };
  }
}
