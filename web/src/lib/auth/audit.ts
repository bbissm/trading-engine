import { db, hasDb, schema } from "@/db/client";
import { authActor } from "./config";

/** Art der Anmeldeereignisse in `audit_event.kind`. */
export type AuthAuditKind =
  | "auth.login.success"
  | "auth.login.failure"
  | "auth.login.password_ok"
  | "auth.logout"
  | "auth.sessions.revoked"
  | "auth.totp.enrolled"
  | "auth.backup_codes.regenerated"
  | "auth.passkey.added"
  | "auth.passkey.removed"
  | "auth.step_up.success"
  | "auth.step_up.failure";

export const LOGIN_KINDS: AuthAuditKind[] = ["auth.login.success", "auth.login.failure"];

/** Kontext aus der Anfrage (keine Geheimnisse, keine Tokens). */
export function requestMeta(headers: Headers | undefined | null): Record<string, unknown> {
  if (!headers) return {};
  const ip = headers.get("x-forwarded-for")?.split(",")[0]?.trim() || headers.get("x-real-ip") || undefined;
  const ua = headers.get("user-agent")?.slice(0, 200) || undefined;
  return { ...(ip ? { ip } : {}), ...(ua ? { userAgent: ua } : {}) };
}

/**
 * Schreibt ein Anmeldeereignis. Fehler beim Schreiben werden geloggt, brechen die Anmeldung aber nicht ab
 * (sonst könnte ein DB-Problem im Audit den einzigen Benutzer aussperren).
 */
export async function writeAuthAudit(kind: AuthAuditKind, data: Record<string, unknown> = {}, object?: string, email?: string): Promise<void> {
  if (!hasDb()) return;
  try {
    await db().insert(schema.auditEvents).values({ actor: authActor(email), kind, object: object ?? null, data });
  } catch (e) {
    console.error("[auth-audit]", kind, e);
  }
}
