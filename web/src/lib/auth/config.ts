/**
 * Anmelde-Konfiguration aus der Umgebung (rein, ohne I/O – in Proxy, Seiten und Tests nutzbar).
 *
 * - `open`: lokale Entwicklung ohne TE_PASSWORD → keine Anmeldung (wie bisher). Nie auf Vercel.
 * - `unconfigured`: auf Vercel ohne TE_PASSWORD, oder TE_PASSWORD gesetzt, aber andere Pflichtwerte fehlen → 503.
 * - `enabled`: Better Auth mit genau einem Benutzer (TE_ADMIN_EMAIL), Passwort TE_PASSWORD, TOTP Pflicht, Passkeys.
 */
export type AuthMode = { mode: "open" } | { mode: "unconfigured"; missing: string[] } | { mode: "enabled" };

const REQUIRED_WITH_PASSWORD = ["TE_ADMIN_EMAIL", "BETTER_AUTH_SECRET", "BETTER_AUTH_URL"] as const;

export function authMode(env: Record<string, string | undefined> = process.env): AuthMode {
  const onVercel = env.VERCEL_ENV === "production" || env.VERCEL_ENV === "preview";
  if (!env.TE_PASSWORD) return onVercel ? { mode: "unconfigured", missing: ["TE_PASSWORD", ...REQUIRED_WITH_PASSWORD.filter((k) => !env[k])] } : { mode: "open" };
  const missing: string[] = REQUIRED_WITH_PASSWORD.filter((k) => !env[k]);
  if (!(env.DATABASE_URL ?? env.POSTGRES_URL)) missing.push("DATABASE_URL");
  return missing.length ? { mode: "unconfigured", missing } : { mode: "enabled" };
}

/** Die einzige zugelassene E-Mail-Adresse (klein geschrieben). */
export const adminEmail = (env: Record<string, string | undefined> = process.env) => (env.TE_ADMIN_EMAIL ?? "").trim().toLowerCase();

/** Akteur in `audit_event.actor` für Anmeldeereignisse. */
export const authActor = (email = adminEmail()) => `user:${email || "unknown"}`;

/** Sitzungen: 30 Tage, gleitend (Verlängerung bei Nutzung, höchstens einmal pro Tag geschrieben). */
export const SESSION_EXPIRES_SECONDS = 30 * 86_400;
export const SESSION_UPDATE_AGE_SECONDS = 86_400;

/** Pfade, die der Proxy nie schützt: Better Auth selbst, Telegram-Webhook und Crons (authentisieren sich selbst). */
export function isSelfAuthenticatedPath(pathname: string): boolean {
  return pathname.startsWith("/api/auth/") || pathname === "/api/telegram" || pathname.startsWith("/api/telegram/") || pathname.startsWith("/api/cron/");
}

/** Nur interne Pfade als Ziel nach der Anmeldung (kein Open Redirect). */
export const safeNext = (next: string | null | undefined) =>
  next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\") && !next.startsWith("/login") && !next.startsWith("/setup") ? next : "/";
