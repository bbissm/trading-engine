import type { Tone } from "@/components/ui";

/** Labels for alerts, deliveries and channels (German UI, Swiss spelling). Pure, shared by page, webhook and tests. */

export const LEVEL: Record<string, { label: string; tone: Tone; icon: string }> = {
  CRITICAL: { label: "Kritisch", tone: "critical", icon: "⛔" },
  WARNING: { label: "Warnung", tone: "warning", icon: "⚠️" },
  SIGNAL: { label: "Signal", tone: "accent", icon: "⚡" },
  INFO: { label: "Information", tone: "neutral", icon: "ℹ️" },
};
export const levelOf = (level: string) => LEVEL[level] ?? { label: level, tone: "neutral" as Tone, icon: "·" };

export const ALERT_STATUS: Record<string, { label: string; tone: Tone }> = {
  OPEN: { label: "Offen", tone: "warning" },
  ACKNOWLEDGED: { label: "Bestätigt", tone: "good" },
  RESOLVED: { label: "Erledigt", tone: "neutral" },
};

/** Prefix every message starts with (docs/06, 4.2). */
export const MODE_PREFIX: Record<string, string> = { PAPER: "[PAPER]", LIVE: "[LIVE]", RESEARCH: "[FORSCHUNG]", SYSTEM: "[SYSTEM]" };

export const DELIVERY: Record<string, { label: string; tone: Tone; hint: string }> = {
  SENT: { label: "Angenommen", tone: "good", hint: "Vom Kanal angenommen – das heisst nicht gelesen. Nur die Bestätigung stoppt die Eskalation." },
  FAILED: { label: "Fehler", tone: "critical", hint: "Der Kanal hat die Meldung nicht angenommen." },
  SKIPPED_QUIET_HOURS: { label: "Ruhezeit", tone: "neutral", hint: "Wegen der Ruhezeit zurückgehalten; wird danach zugestellt." },
  SKIPPED_DISABLED: { label: "Nicht aktiv", tone: "neutral", hint: "Kanal nicht eingerichtet oder abgeschaltet." },
};

export function deliveryOf(status: string, providerRef: string | null) {
  if (providerRef?.startsWith("cancel:")) return { label: "Wiederholung beendet", tone: "neutral" as Tone, hint: "Pushover-Wiederholung nach Bestätigung beendet." };
  if (providerRef?.startsWith("report:")) return { label: "Im Tagesbericht", tone: "good" as Tone, hint: "Im Tagesbericht gebündelt zugestellt." };
  return DELIVERY[status] ?? { label: status, tone: "neutral" as Tone, hint: "" };
}

export interface QuietHours {
  start: string;
  end: string;
  critical_bypass: boolean;
}
export const DEFAULT_QUIET: QuietHours = { start: "22:00", end: "07:00", critical_bypass: true };
export const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

export function quietHoursOf(value: unknown): QuietHours {
  const v = value as Partial<QuietHours> | null;
  return v && typeof v.start === "string" && HHMM.test(v.start) && typeof v.end === "string" && HHMM.test(v.end) && typeof v.critical_bypass === "boolean"
    ? { start: v.start, end: v.end, critical_bypass: v.critical_bypass }
    : DEFAULT_QUIET;
}

export function enabledChannelsOf(value: unknown): Record<"TELEGRAM" | "PUSHOVER" | "EMAIL", boolean> {
  const out = { TELEGRAM: true, PUSHOVER: true, EMAIL: true };
  if (value && typeof value === "object") {
    for (const k of Object.keys(out) as (keyof typeof out)[]) {
      const v = (value as Record<string, unknown>)[k];
      if (typeof v === "boolean") out[k] = v;
    }
  }
  return out;
}
