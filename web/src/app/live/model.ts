/**
 * Live-Assistent: pure helpers (no database, no secrets) shared by the page, the client controls and tests.
 * The engine (engine/src/tradingengine/live) remains the authority: every lock is re-checked there before an order.
 */

type Tone = "neutral" | "accent" | "good" | "warning" | "critical";

/** Command types the web app may write for the live account (engine: live/commands.py). */
export const LIVE_COMMANDS = ["LIVE_ACCOUNT_REGISTER", "LIVE_PAUSE", "LIVE_RESUME", "LIVE_STOP", "LIVE_CLOSE_ALL", "LIVE_EMERGENCY", "ORDER_APPROVAL_DECIDE", "MANDATE_SUSPEND"] as const;
export type LiveCommandType = (typeof LIVE_COMMANDS)[number];
/** These need a fresh step-up (≤ 5 min); the engine re-checks `params.step_up_at`. */
export const STEP_UP_COMMANDS: LiveCommandType[] = ["LIVE_CLOSE_ALL", "LIVE_RESUME"];

export const LIVE_COMMAND_LABEL: Record<string, string> = {
  LIVE_ACCOUNT_REGISTER: "Kraken-Konto verbinden",
  LIVE_PAUSE: "Einstiege pausieren",
  LIVE_RESUME: "Fortsetzen",
  LIVE_STOP: "Geordnet stoppen",
  LIVE_CLOSE_ALL: "Positionen jetzt schliessen",
  LIVE_EMERGENCY: "Notfall",
  ORDER_APPROVAL_DECIDE: "Order-Freigabe",
  MANDATE_SUSPEND: "Mandat aussetzen",
};

export const EMERGENCY_POLICIES = { HOLD_PROTECTED: "halten mit Schutz", CLOSE: "schliessen" } as const;
export type EmergencyPolicy = keyof typeof EMERGENCY_POLICIES;

/** Live autopilot states (docs/01, 3.1) incl. the live-only ones. */
export const LIVE_STATE: Record<string, { label: string; tone: Tone; icon: string; meaning: string }> = {
  SETUP: { label: "Eingerichtet", tone: "neutral", icon: "○", meaning: "Konfiguration unvollständig – keine Einstiege. Die fehlenden Punkte stehen in der Checkliste." },
  READY: { label: "Bereit", tone: "neutral", icon: "○", meaning: "Alle Vorprüfungen grün; wartet auf die Aktivierung." },
  ACTIVE: { label: "Aktiv", tone: "critical", icon: "▶", meaning: "Echtgeld: neue Einstiege im Rahmen des Mandats; Positionen und Stop-Loss bei Kraken werden betreut." },
  ENTRIES_PAUSED: { label: "Einstiege pausiert", tone: "warning", icon: "⏸", meaning: "Keine neuen Einstiege. Bestehende Positionen und ihre Stop-Loss-Orders bei Kraken werden weiter betreut." },
  WINDING_DOWN: { label: "Positionen abwickeln", tone: "warning", icon: "↘", meaning: "Keine neuen Einstiege. Positionen laufen bis zum Exit; ohne Bestand folgt «Gestoppt»." },
  STOPPED: { label: "Gestoppt", tone: "neutral", icon: "■", meaning: "Bestand 0 bestätigt, keine offenen Orders." },
  RECOVERY: { label: "Wiederherstellung", tone: "warning", icon: "↻", meaning: "Erst Abgleich und Schutz, dann Rückkehr in den vorherigen Zustand. Verpasste Signale werden nicht nachgeholt." },
  ERROR: { label: "Fehler", tone: "critical", icon: "✕", meaning: "Ungeklärte Abweichung beim Abgleich. Keine Einstiege; die Stop-Loss-Orders bei Kraken bleiben bestehen." },
  EMERGENCY: { label: "Notfall", tone: "critical", icon: "⛔", meaning: "Keine Einstiege; Schutz bzw. Exits nach Notfallpolicy. Rückkehr nur manuell." },
};
export const liveState = (state: string | null | undefined) =>
  (state && LIVE_STATE[state]) || { label: "Nicht eingerichtet", tone: "neutral" as Tone, icon: "·", meaning: "Kein Live-Konto verbunden. Es gibt keinen echten Bestand und keine echten Orders." };

export type CheckStatus = "ok" | "missing" | "warning";
export interface Check {
  key: string;
  label: string;
  status: CheckStatus;
  detail: string;
  /** false: does not block «Mandat aktivieren» (filled in the form itself). */
  blocking: boolean;
}

export interface StrategyFacts {
  id: string;
  /** latest result per gate, e.g. { G1: "PASSED" } */
  gates: Record<string, string>;
  approvedLive: boolean;
}

export interface LiveFacts {
  /** LIVE_TRADING_ENABLED == "true" in this (web) deployment's environment — only the boolean is ever read. */
  flag: boolean;
  /** heartbeat of the live function (it only writes one when it is enabled on the worker project) */
  liveSeenAt: Date | null;
  account: { id: string; permissions: Record<string, unknown> | null } | null;
  lastRecon: { status: string; at: Date } | null;
  testAckAt: Date | null;
  fxDate: string | null;
  strategies: StrategyFacts[];
  mandate: { id: number; status: string; budget: string; autonomyLevel: number; emergency: string } | null;
  unknownOrders: number;
}

const MIN = 60_000;
const DAY = 86_400_000;
export const gatesPassed = (s: StrategyFacts) => ["G1", "G2", "G3"].every((g) => s.gates[g] === "PASSED");
export const eligibleStrategies = (facts: LiveFacts) => facts.strategies.filter((s) => gatesPassed(s) && s.approvedLive);

/** Precondition checklist of docs/02, 3.2 with the concrete current status of each point. */
export function preconditions(f: LiveFacts, now: number): Check[] {
  const p = f.account?.permissions ?? null;
  const checks: Check[] = [];
  const add = (key: string, label: string, status: CheckStatus, detail: string, blocking = true) => checks.push({ key, label, status, detail, blocking });

  add("flag", "Live-Schalter LIVE_TRADING_ENABLED", f.flag ? "ok" : "missing",
    f.flag ? "true – vom Nutzer gesetzt." : "false – ohne diesen Schalter läuft nichts Live (Standard). Setzen nur durch dich auf dem Worker-Projekt.");
  add("worker", "Live-Funktion läuft", f.liveSeenAt && now - f.liveSeenAt.getTime() <= 3 * MIN ? "ok" : "missing",
    f.liveSeenAt ? `Letzter Durchlauf ${new Date(f.liveSeenAt).toISOString().slice(0, 16).replace("T", " ")} UTC.` : "Noch nie gelaufen (api/live.py meldet ohne Schalter nur «disabled»).",
    false);
  add("account", "Anbieterkonto verbunden", f.account ? "ok" : "missing", f.account ? `Kraken Spot (${f.account.id}).` : "Kein Kraken-Konto registriert.");
  const keyOk = !!p && typeof p.checked_at === "string" && p.withdraw === false && p.trade === true;
  add("key", "Schlüsselrechte geprüft (Handel ja, Auszahlung nein)", keyOk ? "ok" : "missing",
    keyOk
      ? `Handel ja, Auszahlung nein (${p?.withdraw_check === "USER_CONFIRMED" ? "von dir bestätigt – Kraken bietet keine Rechteabfrage" : "Kraken verweigert die Auszahlungsabfrage"}), geprüft ${String(p?.checked_at).slice(0, 16).replace("T", " ")} UTC.`
      : "Noch nicht geprüft oder Auszahlungsrecht nicht ausgeschlossen.");
  add("protection", "Schutzpolicy vom Anbieter unterstützt", "ok",
    "Stop-Loss liegt bei Kraken auf der gefüllten Menge. Gewinnziel und Trailing führt TradingEngine (kein OCO per API).");
  const reconFresh = f.lastRecon && f.lastRecon.status === "OK" && now - f.lastRecon.at.getTime() <= 5 * MIN;
  add("reconciliation", "Abgleich fehlerfrei (≤ 5 min)", reconFresh ? "ok" : "missing",
    f.lastRecon ? `Letzter Abgleich ${f.lastRecon.status}, ${Math.round((now - f.lastRecon.at.getTime()) / MIN)} min alt.` : "Noch kein Abgleich.");
  add("unknown", "Keine Order mit unbekanntem Status", f.unknownOrders === 0 ? "ok" : "missing",
    f.unknownOrders === 0 ? "Keine." : `${f.unknownOrders} Order(s) werden über die Client-Order-ID geklärt.`);
  const ackOk = !!f.testAckAt && now - f.testAckAt.getTime() <= 7 * DAY;
  add("notification", "Benachrichtigung + Fallback getestet (≤ 7 Tage)", ackOk ? "ok" : "missing",
    f.testAckAt ? `Testalarm zuletzt bestätigt vor ${Math.floor((now - f.testAckAt.getTime()) / DAY)} Tag(en).` : "Kein bestätigter Testalarm.");
  const fxOk = !!f.fxDate && (now - Date.parse(`${f.fxDate}T00:00:00Z`)) / DAY <= 5;
  add("fx", "FX-Kurs USD/CHF für Limits", fxOk ? "ok" : "missing", f.fxDate ? `Fixing vom ${f.fxDate}.` : "Kein Kurs gespeichert.");
  add("risk", "Risikopolicy gesetzt", "ok", "Startwerte risk@1 (Annahme A-RISK): 0.5 % Risiko je Trade, Tagesverlust 1.5 %, Drawdown 8 %.");
  const gated = f.strategies.filter(gatesPassed);
  add("gates", "Strategieversion hat G1–G3 bestanden", gated.length ? "ok" : "missing",
    gated.length ? gated.map((s) => s.id).join(", ") : "Keine Version mit G1, G2 und G3 bestanden – Freigabe nicht anwählbar.");
  const approved = eligibleStrategies(f);
  add("approval", "Freigabe für Live (APPROVED_LIVE)", approved.length ? "ok" : "missing",
    approved.length ? approved.map((s) => s.id).join(", ") : "Noch keine Version für Live freigegeben.");
  const active = f.mandate?.status === "ACTIVE";
  add("budget", "Budget festgelegt", active ? "ok" : "missing", active ? `Mandat ${f.mandate?.id}: ${f.mandate?.budget} USD.` : "Im Mandat unten festlegen.", false);
  add("emergency", "Notfallpolicy gewählt", active ? "ok" : "missing",
    active ? (EMERGENCY_POLICIES[f.mandate?.emergency as EmergencyPolicy] ?? "halten mit Schutz") : "Im Mandat unten wählen (Standard: halten mit Schutz).", false);
  return checks;
}

export const canActivate = (checks: Check[]) => checks.every((c) => !c.blocking || c.status === "ok");

/** Concrete effect texts of the live control dialogs (docs/01, 3.2). */
export function controlEffects(input: { positions: number; entryOrders: number; notional: string | null; estCost: string | null; emergency: string }) {
  const pos = `${input.positions} ${input.positions === 1 ? "Position" : "Positionen"}`;
  const entries = `${input.entryOrders} offene ${input.entryOrders === 1 ? "Einstiegsorder wird" : "Einstiegsorders werden"} bei Kraken storniert`;
  const gap = "Kurslücken, Handelsunterbrüche (Wartung) und fehlende Liquidität können die Ausführung verschlechtern oder verhindern.";
  return {
    LIVE_PAUSE: `Keine neuen Einstiege. ${entries}; Reserviertes wird erst nach Krakens Bestätigung frei. ${pos} und ihre Stop-Loss-Orders bei Kraken bleiben und werden weiter betreut.`,
    LIVE_STOP: `Keine neuen Einstiege. ${entries}. ${pos} laufen bis zu ihren Exits nach Strategie-Regeln (Stop, Ziel, Zeitlimit); erst ohne Bestand folgt «Gestoppt».`,
    LIVE_CLOSE_ALL: `Echtgeld: für ${pos} wird der Stop-Loss bei Kraken storniert und nach Bestätigung eine Market-Order zum Verkauf gesendet${input.notional ? ` (Einstandswert ca. ${input.notional} USD` : ""}${input.estCost ? `, geschätzte Kosten ca. ${input.estCost} USD Taker-Gebühr 0.80 % zuzüglich Slippage)` : input.notional ? ")" : ""}. ${entries}. Zwischen Storno und Verkauf ist die Position kurz ohne Stop. ${gap}`,
    LIVE_EMERGENCY: `Keine neuen Einstiege, ${entries}. Notfallpolicy «${EMERGENCY_POLICIES[input.emergency as EmergencyPolicy] ?? "halten mit Schutz"}»: ${input.emergency === "CLOSE" ? `${pos} werden marktnah verkauft.` : `${pos} bleiben mit Stop-Loss bei Kraken; fehlende Stops werden ersetzt.`} Rückkehr nur manuell. ${gap}`,
    LIVE_RESUME: "Der Live-Autopilot wird wieder aktiv: neue Signale freigegebener Versionen können im Rahmen des Mandats zu echten Orders führen. Verpasste Signale werden nicht nachgeholt.",
  };
}

/** Validates the mandate form; returns German error text or null. Budget must be retyped to confirm (docs/02, 3.2). */
export function validateMandate(input: { budget: string; budgetConfirm: string; autonomyLevel: number; strategyIds: string[]; instrumentIds: string[]; emergency: string }, eligible: string[]): string | null {
  if (!/^\d{1,8}(\.\d{1,2})?$/.test(input.budget) || Number(input.budget) <= 0) return "Budget in USD > 0 mit höchstens zwei Nachkommastellen eingeben.";
  if (input.budget !== input.budgetConfirm) return "Zur Bestätigung das Budget genau gleich ein zweites Mal eingeben.";
  if (![1, 2, 3].includes(input.autonomyLevel)) return "Autonomiestufe 1, 2 oder 3 wählen.";
  if (!input.strategyIds.length) return "Mindestens eine freigegebene Strategieversion wählen.";
  const bad = input.strategyIds.filter((s) => !eligible.includes(s));
  if (bad.length) return `Nicht für Live freigegeben oder G1–G3 nicht bestanden: ${bad.join(", ")}.`;
  if (!input.instrumentIds.length) return "Mindestens ein Instrument wählen.";
  if (!(input.emergency in EMERGENCY_POLICIES)) return "Notfallpolicy wählen.";
  return null;
}
