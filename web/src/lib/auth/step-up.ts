import "server-only";
import { headers } from "next/headers";
import { authMode } from "./config";
import { effectiveMaxAge, evaluateStepUp, STEP_UP_DEFAULT_MAX_AGE_SECONDS, type StepUpResult } from "./step-up-core";

/**
 * Prüft serverseitig (Server Action, Route Handler, Server Component), ob die aktuelle Sitzung in den letzten
 * `maxAgeSeconds` mit Passkey oder TOTP bestätigt wurde. Vor jeder risikoerhöhenden Aktion aufrufen
 * (Mandat aktivieren, Limits erhöhen, Positionen schliessen, Schlüssel ändern, Promotion):
 *
 *   const s = await requireStepUp();
 *   if (!s.ok) return { error: s.reason, stepUp: true };
 *
 * Im Client die Bestätigung mit `useStepUp()` / `<StepUpDialog>` (src/components/step-up.tsx) einholen.
 *
 * - Der Zeitpunkt liegt serverseitig je Sitzung (`auth_session.step_up_at`), nie im Client.
 * - Anfragen mit `Authorization`-Header (API-Token, Skripte) bestehen nie: sie haben keine Sitzung.
 * - Ohne Anmeldung (lokal ohne TE_PASSWORD) gibt es keinen Step-up → immer `ok: false`.
 * - TE_STEP_UP_MAX_AGE_SECONDS kann das Fenster nur verkürzen (Tests), nie verlängern.
 */
export async function requireStepUp(maxAgeSeconds = STEP_UP_DEFAULT_MAX_AGE_SECONDS): Promise<{ ok: true; at: Date } | { ok: false; reason: string }> {
  if (authMode().mode !== "enabled") return { ok: false, reason: "Anmeldung ist nicht eingerichtet – Step-up nicht möglich." };
  const h = await headers();
  if (h.get("authorization")) return { ok: false, reason: "Mit API-Token ist keine Bestätigung möglich." };
  const { getAuth } = await import("./server");
  let session: Awaited<ReturnType<ReturnType<typeof getAuth>["api"]["getSession"]>>;
  try {
    session = await getAuth().api.getSession({ headers: h, query: { disableCookieCache: true } });
  } catch (e) {
    console.error("[step-up]", e);
    return { ok: false, reason: "Sitzung konnte nicht geprüft werden." };
  }
  if (!session) return { ok: false, reason: "Nicht angemeldet." };
  if (!(session.user as { twoFactorEnabled?: boolean | null }).twoFactorEnabled) return { ok: false, reason: "Zuerst TOTP einrichten." };
  const stepUpAt = (session.session as { stepUpAt?: Date | string | null }).stepUpAt;
  return evaluateStepUp(stepUpAt, new Date(), effectiveMaxAge(maxAgeSeconds)) satisfies StepUpResult;
}
