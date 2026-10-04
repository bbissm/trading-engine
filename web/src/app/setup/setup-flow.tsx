"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { BackupCodes } from "@/components/backup-codes";
import { QrCode } from "@/components/qr-code";
import { authClient, authErrorText, submitWith } from "@/lib/auth/client";

const input = "w-full !rounded-xl !px-3 !py-3 !text-base";
const primary = "btn w-full justify-center !rounded-xl !py-3 !text-base disabled:opacity-60";
const secondary = "btn-ghost w-full justify-center !rounded-xl !py-3 !text-base disabled:opacity-60";

type Enrolment = { totpURI: string; backupCodes: string[] };

const secretOf = (uri: string) => {
  try {
    return new URL(uri).searchParams.get("secret") ?? "";
  } catch {
    return "";
  }
};
const grouped = (s: string) => s.replace(/(.{4})/g, "$1 ").trim();

/**
 * 1. Passwort bestätigen → TOTP-Geheimnis + Backup-Codes (einmalig sichtbar)
 * 2. Code aus der App bestätigen → TOTP aktiv (neue Sitzung)
 * 3. Passkey hinzufügen (empfohlen) oder später
 */
export function SetupFlow({ email, totpEnabled }: { email: string; totpEnabled: boolean }) {
  const router = useRouter();
  const [step, setStep] = useState<"password" | "scan" | "passkey">(totpEnabled ? "passkey" : "password");
  const [enrolment, setEnrolment] = useState<Enrolment>();
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState(false);

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

  if (step === "password") {
    return (
      <form
        className="space-y-3"
        onSubmit={submitWith((form) =>
          run(async () => {
            const r = await authClient.twoFactor.enable({ password: String(form.get("password") ?? "") });
            if (r.error || !r.data) return setError(authErrorText(r.error));
            const data = r.data as Partial<Enrolment>;
            if (!data.totpURI || !data.backupCodes) return setError("TOTP konnte nicht eingerichtet werden.");
            setEnrolment({ totpURI: data.totpURI, backupCodes: data.backupCodes });
            setStep("scan");
          }))
        }
      >
        <p className="text-sm text-ink-2">
          Bevor die App nutzbar ist, wird ein zweiter Faktor eingerichtet: ein zeitbasierter Code (TOTP) aus einer Authenticator-App. Bitte das Passwort für <span className="font-medium text-ink">{email}</span> bestätigen.
        </p>
        <input name="password" type="password" autoComplete="current-password" placeholder="Passwort" aria-label="Passwort" className={input} required autoFocus />
        {error && <p role="alert" className="text-sm text-critical">{error}</p>}
        <button type="submit" disabled={pending} className={primary}>
          {pending ? "Einrichten …" : "TOTP einrichten"}
        </button>
      </form>
    );
  }

  if (step === "scan" && enrolment) {
    const secret = secretOf(enrolment.totpURI);
    return (
      <form
        className="space-y-4"
        onSubmit={submitWith((form) =>
          run(async () => {
            const r = await authClient.twoFactor.verifyTotp({ code: String(form.get("code") ?? "").replace(/\s+/g, "") });
            if (r.error) return setError(authErrorText(r.error));
            setStep("passkey");
          }))
        }
      >
        <section className="space-y-3">
          <h2 className="text-sm font-semibold">1 · In der Authenticator-App hinzufügen</h2>
          <div className="flex justify-center">
            <QrCode value={enrolment.totpURI} label="QR-Code für die Authenticator-App" />
          </div>
          <p className="text-xs text-ink-2">Oder Schlüssel von Hand eingeben:</p>
          <p className="break-words rounded-lg bg-surface-2 px-3 py-2 text-center font-mono text-sm select-all" data-testid="totp-secret">
            {grouped(secret)}
          </p>
        </section>
        <section className="space-y-2">
          <h2 className="text-sm font-semibold">2 · Backup-Codes sichern</h2>
          <p className="text-xs text-ink-2">Werden nur jetzt angezeigt. Jeder Code ersetzt einmal den TOTP-Code, z. B. wenn das Telefon fehlt.</p>
          <BackupCodes codes={enrolment.backupCodes} />
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} className="mt-0.5 !min-h-0 !w-auto" />
            Ich habe die Backup-Codes sicher abgelegt.
          </label>
        </section>
        <section className="space-y-2">
          <h2 className="text-sm font-semibold">3 · Code bestätigen</h2>
          <input name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" placeholder="123456" aria-label="TOTP-Code" className={`${input} text-center tracking-widest`} required />
        </section>
        {error && <p role="alert" className="text-sm text-critical">{error}</p>}
        <button type="submit" disabled={pending || !saved} className={primary}>
          {pending ? "Prüfen …" : "Bestätigen und aktivieren"}
        </button>
      </form>
    );
  }

  return (
    <div className="space-y-4">
      {!totpEnabled && <p className="rounded-xl bg-surface-2 px-3 py-2 text-sm">TOTP ist eingerichtet.</p>}
      <p className="text-sm text-ink-2">
        <span className="font-medium text-ink">Empfohlen:</span> einen Passkey hinzufügen (Face ID, Touch ID, Windows Hello oder Sicherheitsschlüssel). Danach genügt der Passkey zum Anmelden und für Bestätigungen; Passwort + TOTP bleiben als Rückfallweg.
      </p>
      <form
        className="space-y-3"
        onSubmit={submitWith((form) =>
          run(async () => {
            const name = String(form.get("name") ?? "").trim() || undefined;
            const r = await authClient.passkey.addPasskey({ name });
            if (r?.error) return setError(authErrorText(r.error));
            router.replace("/settings/security");
          }))
        }
      >
        <input name="name" placeholder="Name, z. B. «iPhone»" aria-label="Name des Passkeys" className={input} maxLength={60} />
        {error && <p role="alert" className="text-sm text-critical">{error}</p>}
        <button type="submit" disabled={pending} className={primary}>
          {pending ? "Passkey wird erstellt …" : "Passkey hinzufügen"}
        </button>
      </form>
      <button type="button" className={secondary} onClick={() => router.replace("/")}>
        Später
      </button>
    </div>
  );
}
