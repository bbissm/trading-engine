"use client";

import { startAuthentication, WebAuthnError } from "@simplewebauthn/browser";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Step-up im Client: «Bestätigen mit Passkey oder TOTP». Erneuert den serverseitig je Sitzung gespeicherten
 * Zeitpunkt (`auth_session.step_up_at`), den `requireStepUp()` prüft (src/lib/auth/step-up.ts).
 *
 *   const { ensureStepUp, dialog } = useStepUp();
 *   async function onClick() {
 *     if (!(await ensureStepUp())) return;      // Dialog nur, wenn die letzte Bestätigung älter ist
 *     const r = await riskyAction();             // Server Action ruft requireStepUp()
 *   }
 *   return <>{dialog}<button onClick={onClick}>…</button></>;
 */
export type StepUpStatus = { ok: true; at: string; expiresAt: string } | { ok: false; reason: string };

export async function fetchStepUpStatus(): Promise<StepUpStatus> {
  try {
    const r = await fetch("/api/step-up", { cache: "no-store", credentials: "same-origin" });
    if (!r.ok) return { ok: false, reason: "Nicht angemeldet." };
    return (await r.json()) as StepUpStatus;
  } catch {
    return { ok: false, reason: "Keine Verbindung." };
  }
}

async function postJson(url: string, body: unknown): Promise<{ ok: boolean; message?: string }> {
  const r = await fetch(url, { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (r.ok) return { ok: true };
  if (r.status === 429) return { ok: false, message: "Zu viele Versuche. Bitte kurz warten." };
  const data = (await r.json().catch(() => ({}))) as { message?: string };
  return { ok: false, message: data.message || "Bestätigung fehlgeschlagen." };
}

/** Passkey-Bestätigung (Benutzerverifikation Pflicht). */
export async function stepUpWithPasskey(): Promise<{ ok: boolean; message?: string }> {
  const r = await fetch("/api/auth/step-up/passkey/options", { credentials: "same-origin", cache: "no-store" });
  if (!r.ok) {
    const data = (await r.json().catch(() => ({}))) as { message?: string };
    return { ok: false, message: r.status === 429 ? "Zu viele Versuche. Bitte kurz warten." : data.message || "Passkey nicht verfügbar." };
  }
  let response: unknown;
  try {
    response = await startAuthentication({ optionsJSON: await r.json() });
  } catch (e) {
    return { ok: false, message: e instanceof WebAuthnError && e.code === "ERROR_CEREMONY_ABORTED" ? "Abgebrochen." : "Passkey-Vorgang abgebrochen." };
  }
  return postJson("/api/auth/step-up/passkey/verify", { response });
}

export const stepUpWithTotp = (code: string) => postJson("/api/auth/step-up/totp", { code: code.replace(/\s+/g, "") });

export function StepUpDialog({ open, onConfirmed, onCancel, title = "Bestätigen mit Passkey oder TOTP", children }: { open: boolean; onConfirmed: () => void; onCancel: () => void; title?: string; children?: ReactNode }) {
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState(false);
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  async function run(fn: () => Promise<{ ok: boolean; message?: string }>) {
    setPending(true);
    setError(undefined);
    const r = await fn().catch(() => ({ ok: false, message: "Bestätigung fehlgeschlagen." }));
    setPending(false);
    if (r.ok) onConfirmed();
    else setError(r.message);
  }

  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        onCancel();
      }}
      aria-labelledby="step-up-title"
      className="m-auto w-[min(26rem,calc(100vw-2rem))] rounded-2xl border border-line bg-surface p-5 text-ink shadow-xl backdrop:bg-black/40"
    >
      <h2 id="step-up-title" className="text-base font-semibold">
        {title}
      </h2>
      <p className="mt-1 text-sm text-ink-2">{children ?? "Diese Aktion verlangt eine frische Bestätigung (gilt 5 Minuten)."}</p>
      <div className="mt-4 space-y-3">
        <button type="button" className="btn w-full justify-center !py-2.5 disabled:opacity-60" disabled={pending} onClick={() => run(stepUpWithPasskey)}>
          Mit Passkey bestätigen
        </button>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const code = String(new FormData(e.currentTarget).get("code") ?? "");
            void run(() => stepUpWithTotp(code));
          }}
        >
          <input name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" placeholder="TOTP-Code" aria-label="TOTP-Code" className="min-w-0 flex-1 text-center tracking-widest" required />
          <button type="submit" className="btn-ghost shrink-0 disabled:opacity-60" disabled={pending}>
            Bestätigen
          </button>
        </form>
        {error && (
          <p role="alert" className="text-sm text-critical">
            {error}
          </p>
        )}
        <button type="button" className="w-full py-1 text-sm text-muted hover:text-ink" onClick={onCancel}>
          Abbrechen
        </button>
      </div>
    </dialog>
  );
}

/** Hook: `ensureStepUp()` gibt true zurück, wenn die Sitzung (noch oder neu) bestätigt ist. */
export function useStepUp(): { ensureStepUp: () => Promise<boolean>; dialog: ReactNode } {
  const [open, setOpen] = useState(false);
  const resolver = useRef<((ok: boolean) => void) | null>(null);

  const finish = useCallback((ok: boolean) => {
    setOpen(false);
    resolver.current?.(ok);
    resolver.current = null;
  }, []);

  const ensureStepUp = useCallback(async () => {
    const s = await fetchStepUpStatus();
    // knapp vor Ablauf lieber neu bestätigen, damit die folgende Aktion nicht mitten im Ablauf scheitert
    if (s.ok && new Date(s.expiresAt).getTime() - Date.now() > 15_000) return true;
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
      setOpen(true);
    });
  }, []);

  return { ensureStepUp, dialog: <StepUpDialog open={open} onConfirmed={() => finish(true)} onCancel={() => finish(false)} /> };
}
