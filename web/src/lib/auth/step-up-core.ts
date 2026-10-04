/**
 * Reine Regeln für die Step-up-Bestätigung (ohne I/O, getestet in step-up.test.ts).
 * Gespeichert wird nur der Zeitpunkt der letzten Bestätigung je Sitzung (`auth_session.step_up_at`).
 */
export const STEP_UP_DEFAULT_MAX_AGE_SECONDS = 300;

export type StepUpResult = { ok: true; at: Date } | { ok: false; reason: string };

/**
 * Wirksames Höchstalter: der angefragte Wert, optional durch TE_STEP_UP_MAX_AGE_SECONDS weiter verkürzt
 * (nur für Tests/Abnahme). Die Umgebung kann das Fenster nie verlängern.
 */
export function effectiveMaxAge(requested: number, env: Record<string, string | undefined> = process.env): number {
  const req = Number.isFinite(requested) && requested > 0 ? requested : 0;
  const cap = Number(env.TE_STEP_UP_MAX_AGE_SECONDS);
  return Number.isFinite(cap) && cap > 0 ? Math.min(req, cap) : req;
}

export function evaluateStepUp(stepUpAt: Date | string | null | undefined, now: Date, maxAgeSeconds: number): StepUpResult {
  if (!stepUpAt) return { ok: false, reason: "Bestätigung mit Passkey oder TOTP erforderlich." };
  const at = stepUpAt instanceof Date ? stepUpAt : new Date(stepUpAt);
  if (Number.isNaN(at.getTime())) return { ok: false, reason: "Bestätigung mit Passkey oder TOTP erforderlich." };
  const age = (now.getTime() - at.getTime()) / 1000;
  // Zeitstempel aus der Zukunft (Uhrabweichung > 5 s) nicht akzeptieren
  if (age < -5) return { ok: false, reason: "Bestätigung ungültig. Bitte erneut bestätigen." };
  if (maxAgeSeconds <= 0 || age > maxAgeSeconds) return { ok: false, reason: "Bestätigung abgelaufen. Bitte erneut mit Passkey oder TOTP bestätigen." };
  return { ok: true, at };
}
