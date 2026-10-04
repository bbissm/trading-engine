import { ago } from "@/lib/format";
import type { AttentionItem } from "@/lib/health";

type Tone = "neutral" | "accent" | "good" | "warning" | "critical";

/** The only command types the web app ever writes for paper accounts (engine: services/paper.py). */
export const PAPER_COMMANDS = ["PAPER_CREATE", "PAPER_START", "PAPER_PAUSE", "PAPER_STOP", "PAPER_CLOSE_ALL", "PAPER_RESET"] as const;
export type PaperCommandType = (typeof PAPER_COMMANDS)[number];
export type AccountCommandType = Exclude<PaperCommandType, "PAPER_CREATE">;

export const COMMAND_LABEL: Record<string, string> = {
  PAPER_CREATE: "Virtuelles Konto anlegen",
  PAPER_START: "Starten / Fortsetzen",
  PAPER_PAUSE: "Einstiege pausieren",
  PAPER_STOP: "Geordnet stoppen",
  PAPER_CLOSE_ALL: "Positionen jetzt schliessen",
  PAPER_RESET: "Neue Episode (Reset)",
};

export const COMMAND_STATUS: Record<string, { label: string; tone: Tone }> = {
  PENDING: { label: "Wartet auf Engine", tone: "warning" },
  DONE: { label: "✓ Quittiert", tone: "good" },
  REJECTED: { label: "✕ Abgelehnt", tone: "critical" },
};

/** Rejection reason the engine stored in `command.result`. */
export function rejectionReason(result: Record<string, unknown> | null | undefined): string | null {
  const r = result?.reason;
  return typeof r === "string" && r ? r : null;
}

/** Autopilot states (docs/01, 3.1): label, tone and one sentence on what the state means. */
export const AUTOPILOT_STATE: Record<string, { label: string; tone: Tone; icon: string; meaning: string }> = {
  READY: { label: "Bereit", tone: "neutral", icon: "○", meaning: "Wartet auf den Start. Es entstehen keine neuen Einstiege." },
  ACTIVE: { label: "Aktiv", tone: "accent", icon: "▶", meaning: "Neue Einstiege sind im Rahmen der Risikogrenzen erlaubt; Positionen und Schutz-Stops werden betreut. Alles simuliert." },
  ENTRIES_PAUSED: { label: "Einstiege pausiert", tone: "warning", icon: "⏸", meaning: "Keine neuen Einstiege. Bestehende Positionen und ihre Schutz-Stops werden weiter betreut." },
  WINDING_DOWN: { label: "Positionen abwickeln", tone: "warning", icon: "↘", meaning: "Keine neuen Einstiege. Positionen laufen bis zu ihren Exits; sobald kein Bestand mehr existiert, folgt «Gestoppt»." },
  STOPPED: { label: "Gestoppt", tone: "neutral", icon: "■", meaning: "Kein Bestand, keine offenen Orders, keine neuen Einstiege. Ein Start oder eine neue Episode ist möglich." },
  ERROR: { label: "Fehler", tone: "critical", icon: "✕", meaning: "Unaufgelöste Abweichung. Keine neuen Einstiege, bis die Ursache geklärt ist." },
};
export const autopilotState = (state: string | null | undefined) =>
  (state && AUTOPILOT_STATE[state]) || { label: state ?? "Nicht eingerichtet", tone: "neutral" as Tone, icon: "·", meaning: "Für dieses Konto ist kein Autopilot-Zustand gespeichert." };

/** In which states the engine accepts which command (the engine remains the authority and may still reject). */
export function allowedCommands(state: string | null | undefined, holdings: { openPositions: number; workingOrders: number }): Record<AccountCommandType, boolean> {
  const s = state ?? "";
  const flat = holdings.openPositions === 0 && holdings.workingOrders === 0;
  return {
    PAPER_START: ["READY", "STOPPED", "ENTRIES_PAUSED"].includes(s),
    PAPER_PAUSE: s === "ACTIVE",
    PAPER_STOP: ["ACTIVE", "ENTRIES_PAUSED"].includes(s),
    PAPER_CLOSE_ALL: ["ACTIVE", "ENTRIES_PAUSED", "WINDING_DOWN"].includes(s),
    PAPER_RESET: flat && ["READY", "STOPPED", "ENTRIES_PAUSED", "ERROR"].includes(s),
  };
}

/** Order states (docs/01, 3.4) in the wording of docs/02, section 1. Paper orders go to the simulator, not to a provider. */
const ORDER_STATE: Record<string, { label: string; tone: Tone }> = {
  PREPARED: { label: "Order vorbereitet", tone: "neutral" },
  CHECKED: { label: "Order geprüft", tone: "neutral" },
  SUBMITTED: { label: "Order gesendet", tone: "neutral" },
  ACCEPTED: { label: "vom Simulator angenommen", tone: "accent" },
  PARTIALLY_FILLED: { label: "teilweise ausgeführt", tone: "accent" },
  FILLED: { label: "ausgeführt", tone: "neutral" },
  CANCEL_REQUESTED: { label: "Stornierung angefragt", tone: "warning" },
  CANCELED: { label: "storniert", tone: "neutral" },
  REJECTED: { label: "abgelehnt", tone: "critical" },
  EXPIRED: { label: "abgelaufen", tone: "neutral" },
  UNKNOWN: { label: "Status unbekannt", tone: "critical" },
};

/** e.g. «teilweise ausgeführt (3 von 10)» — quantities are passed already formatted. */
export function orderState(state: string, filled?: string, qty?: string): { label: string; tone: Tone } {
  const s = ORDER_STATE[state] ?? { label: state, tone: "neutral" as Tone };
  return state === "PARTIALLY_FILLED" && filled && qty ? { ...s, label: `${s.label} (${filled} von ${qty})` } : s;
}

/** Orders that can still fill or need attention. */
export const WORKING_ORDER_STATES = ["PREPARED", "CHECKED", "SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED", "CANCEL_REQUESTED", "UNKNOWN"];

export const ORDER_ROLE: Record<string, string> = { ENTRY: "Einstieg", PROTECT: "Schutz-Stop", EXIT: "Ausstieg" };
export const ORDER_TYPE: Record<string, string> = { LIMIT: "Limit", MARKET: "Market", STOP: "Stop" };
/** Order side, not an execution: «gekauft/verkauft» is reserved for confirmed fills. */
export const ORDER_SIDE: Record<string, string> = { BUY: "Kauf", SELL: "Verkauf" };
export const FILL_SIDE: Record<string, string> = { BUY: "gekauft", SELL: "verkauft" };

export const PAPER_COMMAND_MAX_PENDING_MS = 3 * 60_000;

/** Attention items of the overview: autopilot in ERROR, PAPER_* command pending for more than 3 minutes. */
export function paperAttention(
  input: { accounts: { id: string; name: string; state: string | null; reason: string | null }[]; pending: { id: number; type: string; target: string | null; issuedAt: Date }[] },
  now: number,
): AttentionItem[] {
  const items: AttentionItem[] = [];
  for (const a of input.accounts) {
    if (a.state === "ERROR") {
      items.push({ id: `autopilot:${a.id}`, severity: "critical", title: `Paper-Autopilot «${a.name}» im Zustand Fehler`, detail: a.reason ?? "Kein Grund gespeichert.", href: "/autopilot" });
    }
  }
  for (const c of input.pending) {
    if (now - c.issuedAt.getTime() <= PAPER_COMMAND_MAX_PENDING_MS) continue;
    items.push({
      id: `command:${c.id}`,
      severity: "warning",
      title: `Befehl «${COMMAND_LABEL[c.type] ?? c.type}» wartet seit über 3 Minuten`,
      detail: `Befehl Nr. ${c.id}${c.target ? ` für ${c.target}` : ""}, gesendet ${ago(c.issuedAt, now)}. Die Engine hat ihn noch nicht quittiert.`,
      href: "/operations",
    });
  }
  return items;
}
