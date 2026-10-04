"use client";

import { useState } from "react";
import { authClient, authErrorText, submitWith } from "@/lib/auth/client";

const input = "w-full !rounded-xl !px-3 !py-3 !text-base";
const primary = "btn w-full justify-center !rounded-xl !py-3 !text-base disabled:opacity-60";
const secondary = "btn-ghost w-full justify-center !rounded-xl !py-3 !text-base disabled:opacity-60";

type Step = "credentials" | "totp" | "backup";

/**
 * Anmeldung: Passkey (allein genügend) oder E-Mail + Passwort, danach TOTP bzw. Backup-Code.
 * Beim allerersten Login gibt es noch kein TOTP → Weiterleitung zur Einrichtung (/setup).
 */
export function LoginForm({ next }: { next: string }) {
  const [step, setStep] = useState<Step>("credentials");
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState(false);

  const go = (href: string) => window.location.assign(href);

  async function run(fn: () => Promise<void>) {
    setPending(true);
    setError(undefined);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Vorgang fehlgeschlagen.");
    } finally {
      setPending(false);
    }
  }

  const withPasskey = () =>
    run(async () => {
      const r = await authClient.signIn.passkey();
      if (r?.error) return setError(authErrorText(r.error));
      go(next);
    });

  const withPassword = (form: FormData) =>
    run(async () => {
      const r = await authClient.signIn.email({ email: String(form.get("email") ?? ""), password: String(form.get("password") ?? "") });
      if (r.error) return setError(authErrorText(r.error));
      if ((r.data as { twoFactorRedirect?: boolean } | null)?.twoFactorRedirect) return setStep("totp");
      go("/setup"); // erster Login: TOTP noch nicht eingerichtet
    });

  const withCode = (form: FormData) =>
    run(async () => {
      const code = String(form.get("code") ?? "").replace(/\s+/g, "");
      const r = step === "totp" ? await authClient.twoFactor.verifyTotp({ code }) : await authClient.twoFactor.verifyBackupCode({ code });
      if (r.error) {
        if (r.error.code === "INVALID_TWO_FACTOR_COOKIE" || r.error.code === "TOO_MANY_ATTEMPTS_REQUEST_NEW_CODE") setStep("credentials");
        return setError(authErrorText(r.error));
      }
      go(next);
    });

  if (step !== "credentials") {
    return (
      <form onSubmit={submitWith(withCode)} className="space-y-3" key={step}>
        <p className="text-center text-sm text-ink-2">{step === "totp" ? "Code aus der Authenticator-App eingeben." : "Einen der Backup-Codes eingeben. Jeder Code gilt nur einmal."}</p>
        <input
          name="code"
          inputMode={step === "totp" ? "numeric" : "text"}
          autoComplete="one-time-code"
          pattern={step === "totp" ? "[0-9 ]{6,7}" : undefined}
          placeholder={step === "totp" ? "123456" : "XXXXX-XXXXX"}
          aria-label={step === "totp" ? "TOTP-Code" : "Backup-Code"}
          className={`${input} text-center tracking-widest`}
          autoCapitalize="none"
          autoCorrect="off"
          required
          autoFocus
        />
        {error && <p role="alert" className="text-sm text-critical">{error}</p>}
        <button type="submit" disabled={pending} className={primary}>
          {pending ? "Prüfen …" : "Bestätigen"}
        </button>
        <button type="button" className={secondary} onClick={() => setStep(step === "totp" ? "backup" : "totp")}>
          {step === "totp" ? "Backup-Code verwenden" : "TOTP-Code verwenden"}
        </button>
      </form>
    );
  }

  return (
    <div className="space-y-5">
      <button type="button" onClick={withPasskey} disabled={pending} className={primary}>
        Mit Passkey anmelden
      </button>
      <div className="flex items-center gap-3 text-xs text-muted" aria-hidden>
        <span className="h-px flex-1 bg-[var(--grid)]" />
        oder mit Passwort und TOTP
        <span className="h-px flex-1 bg-[var(--grid)]" />
      </div>
      <form onSubmit={submitWith(withPassword)} className="space-y-3">
        <input name="email" type="email" autoComplete="username" placeholder="E-Mail" aria-label="E-Mail" className={input} autoCapitalize="none" autoCorrect="off" required />
        <input name="password" type="password" autoComplete="current-password" placeholder="Passwort" aria-label="Passwort" className={input} required />
        {error && <p role="alert" className="text-sm text-critical">{error}</p>}
        <button type="submit" disabled={pending} className={secondary}>
          {pending ? "Anmelden …" : "Weiter"}
        </button>
      </form>
    </div>
  );
}
