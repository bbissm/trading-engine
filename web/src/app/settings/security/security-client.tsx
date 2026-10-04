"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { BackupCodes } from "@/components/backup-codes";
import { fetchStepUpStatus, useStepUp, type StepUpStatus } from "@/components/step-up";
import { Badge, Empty } from "@/components/ui";
import { authClient, authErrorText, submitWith } from "@/lib/auth/client";
import { dateTime } from "@/lib/format";
import { revokeAllSessionsAction, revokeSessionAction } from "./actions";

type Passkey = { id: string; name: string | null; createdAt: string | null; backedUp: boolean; deviceType: string };

export function PasskeyList({ passkeys }: { passkeys: Passkey[] }) {
  const router = useRouter();
  const { ensureStepUp, dialog } = useStepUp();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState<string>();
  const [name, setName] = useState("");

  async function add() {
    setError(undefined);
    setBusy("add");
    try {
      // direkt nach der Anmeldung ohne, sonst mit Bestätigung (serverseitig erzwungen)
      const opts = { name: name.trim() || undefined };
      let r = await authClient.passkey.addPasskey(opts);
      if (r?.error && "code" in r.error && r.error.code === "STEP_UP_REQUIRED") {
        if (!(await ensureStepUp())) return;
        r = await authClient.passkey.addPasskey(opts);
      }
      if (r?.error) return setError(authErrorText(r.error));
      setName("");
      router.refresh();
    } finally {
      setBusy(undefined);
    }
  }

  async function remove(p: Passkey) {
    setError(undefined);
    if (!window.confirm(`Passkey «${p.name ?? "ohne Namen"}» entfernen?`)) return;
    setBusy(p.id);
    try {
      if (!(await ensureStepUp())) return;
      const r = await authClient.passkey.deletePasskey({ id: p.id });
      if (r?.error) return setError(authErrorText(r.error));
      router.refresh();
    } finally {
      setBusy(undefined);
    }
  }

  return (
    <div className="space-y-3">
      {dialog}
      {passkeys.length === 0 ? (
        <Empty>Noch kein Passkey. Empfohlen: einen auf dem Telefon und einen auf dem Computer hinzufügen.</Empty>
      ) : (
        <ul className="divide-y divide-[var(--grid)]">
          {passkeys.map((p) => (
            <li key={p.id} className="flex items-center gap-3 py-2 first:pt-0 last:pb-0">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{p.name ?? "Passkey"}</div>
                <div className="text-xs text-muted">
                  {p.createdAt ? `hinzugefügt ${dateTime(p.createdAt)}` : "–"} · {p.backedUp ? "synchronisiert" : "nur dieses Gerät"}
                </div>
              </div>
              <button type="button" className="btn-ghost shrink-0 !text-critical disabled:opacity-60" disabled={!!busy} onClick={() => remove(p)}>
                Entfernen
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p role="alert" className="text-sm text-critical">
          {error}
        </p>
      )}
      <div className="flex flex-col gap-2 sm:flex-row">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name, z. B. «iPhone»" aria-label="Name des neuen Passkeys" maxLength={60} className="min-w-0 flex-1" />
        <button type="button" className="btn shrink-0 justify-center disabled:opacity-60" disabled={!!busy} onClick={add}>
          {busy === "add" ? "Passkey wird erstellt …" : "Passkey hinzufügen"}
        </button>
      </div>
    </div>
  );
}

export function BackupCodesCard() {
  const { ensureStepUp, dialog } = useStepUp();
  const [codes, setCodes] = useState<string[]>();
  const [error, setError] = useState<string>();
  const [asking, setAsking] = useState(false);
  const [pending, setPending] = useState(false);

  async function regenerate(form: FormData) {
    setError(undefined);
    setPending(true);
    try {
      if (!(await ensureStepUp())) return;
      const r = await authClient.twoFactor.generateBackupCodes({ password: String(form.get("password") ?? "") });
      if (r.error || !r.data) return setError(authErrorText(r.error));
      setCodes(r.data.backupCodes);
      setAsking(false);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-3">
      {dialog}
      <p className="text-sm text-ink-2">Neue Backup-Codes machen alle bisherigen ungültig. Verlangt Passwort und Bestätigung.</p>
      {codes && (
        <>
          <p className="text-sm font-medium">Neue Backup-Codes (nur jetzt sichtbar):</p>
          <BackupCodes codes={codes} />
        </>
      )}
      {asking ? (
        <form onSubmit={submitWith(regenerate)} className="flex flex-col gap-2 sm:flex-row">
          <input name="password" type="password" autoComplete="current-password" placeholder="Passwort" aria-label="Passwort" className="min-w-0 flex-1" required autoFocus />
          <button type="submit" className="btn shrink-0 justify-center disabled:opacity-60" disabled={pending}>
            Neu erzeugen
          </button>
        </form>
      ) : (
        <button type="button" className="btn-ghost" onClick={() => setAsking(true)}>
          Backup-Codes neu erzeugen
        </button>
      )}
      {error && (
        <p role="alert" className="text-sm text-critical">
          {error}
        </p>
      )}
    </div>
  );
}

export function StepUpCard() {
  const { ensureStepUp, dialog } = useStepUp();
  const [status, setStatus] = useState<StepUpStatus>();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    void fetchStepUpStatus().then(setStatus);
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const active = status?.ok && new Date(status.expiresAt).getTime() > now;
  const remaining = status?.ok ? Math.max(0, Math.round((new Date(status.expiresAt).getTime() - now) / 1000)) : 0;

  return (
    <div className="space-y-3">
      {dialog}
      <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="step-up-status">
        {active && status?.ok ? (
          <>
            <Badge tone="good">Bestätigt</Badge>
            <span className="text-ink-2">
              noch {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, "0")} min (seit {dateTime(status.at)})
            </span>
          </>
        ) : (
          <>
            <Badge>Nicht bestätigt</Badge>
            <span className="text-ink-2">Wird bei Bedarf automatisch abgefragt.</span>
          </>
        )}
      </div>
      <button
        type="button"
        className="btn-ghost"
        onClick={async () => {
          if (await ensureStepUp()) setStatus(await fetchStepUpStatus());
        }}
      >
        Jetzt bestätigen
      </button>
    </div>
  );
}

type SessionRow = { id: string; current: boolean; device: string; ip: string | null; createdAt: string; lastSeen: string; expiresAt: string };

export function SessionList({ sessions }: { sessions: SessionRow[] }) {
  const [pending, start] = useTransition();
  const [error, setError] = useState<string>();
  if (!sessions.length) return <Empty>Keine aktiven Sitzungen.</Empty>;
  return (
    <>
      <ul className="divide-y divide-[var(--grid)]">
        {sessions.map((s) => (
          <li key={s.id} className="flex items-center gap-3 py-2 first:pt-0 last:pb-0">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
                <span className="truncate">{s.device}</span>
                {s.current && <Badge tone="accent">Diese Sitzung</Badge>}
              </div>
              <div className="text-xs text-muted">
                angemeldet {dateTime(s.createdAt)} · zuletzt {dateTime(s.lastSeen)}
                {s.ip ? ` · ${s.ip}` : ""}
              </div>
            </div>
            <button
              type="button"
              className="btn-ghost shrink-0 disabled:opacity-60"
              disabled={pending}
              onClick={() =>
                start(async () => {
                  const r = await revokeSessionAction(s.id);
                  if (r?.error) setError(r.error);
                })
              }
            >
              Beenden
            </button>
          </li>
        ))}
      </ul>
      {error && (
        <p role="alert" className="mt-2 text-sm text-critical">
          {error}
        </p>
      )}
    </>
  );
}

export function LogoutEverywhere() {
  const [pending, start] = useTransition();
  return (
    <button
      type="button"
      className="btn-ghost !text-critical disabled:opacity-60"
      disabled={pending}
      onClick={() => {
        if (window.confirm("Auf allen Geräten abmelden (auch hier)?")) start(() => revokeAllSessionsAction());
      }}
    >
      Überall abmelden
    </button>
  );
}
