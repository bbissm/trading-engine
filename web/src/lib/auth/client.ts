"use client";

import { passkeyClient } from "@better-auth/passkey/client";
import { twoFactorClient } from "better-auth/client/plugins";
import { createAuthClient } from "better-auth/react";

/** Better-Auth-Client (gleiche Origin, /api/auth). Weiterleitungen steuern die Seiten selbst. */
export const authClient = createAuthClient({
  basePath: "/api/auth",
  plugins: [twoFactorClient(), passkeyClient()],
});

/**
 * Formular-Handler ohne React-«action»: React 19 setzt Formulare nach einer action zurück, hier sollen die
 * Eingaben (z. B. die E-Mail nach falschem Passwort) stehen bleiben.
 */
export const submitWith =
  (fn: (form: FormData) => unknown) =>
  (e: React.FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    void fn(new FormData(e.currentTarget));
  };

/** Fehlermeldungen von Better Auth auf Deutsch (Codes aus der API, Rest generisch). */
export function authErrorText(error: { code?: string; message?: string; status?: number } | null | undefined): string {
  if (!error) return "Unbekannter Fehler.";
  if (error.status === 429) return "Zu viele Versuche. Bitte kurz warten.";
  switch (error.code) {
    case "INVALID_EMAIL_OR_PASSWORD":
      return "E-Mail oder Passwort falsch.";
    case "INVALID_CODE":
    case "STEP_UP_INVALID_CODE":
      return "Code ungültig.";
    case "INVALID_BACKUP_CODE":
      return "Backup-Code ungültig.";
    case "INVALID_TWO_FACTOR_COOKIE":
      return "Anmeldung abgelaufen. Bitte neu anmelden.";
    case "TOO_MANY_ATTEMPTS_REQUEST_NEW_CODE":
    case "ACCOUNT_TEMPORARILY_LOCKED":
      return "Zu viele Fehlversuche. Bitte später erneut anmelden.";
    case "INVALID_PASSWORD":
      return "Passwort falsch.";
    case "STEP_UP_REQUIRED":
      return "Bitte zuerst mit Passkey oder TOTP bestätigen.";
    case "AUTH_CANCELLED":
    case "ERROR_CEREMONY_ABORTED":
      return "Passkey-Vorgang abgebrochen.";
    case "ERROR_AUTHENTICATOR_PREVIOUSLY_REGISTERED":
      return "Dieser Passkey ist bereits registriert.";
    default:
      return error.message || "Vorgang fehlgeschlagen.";
  }
}
