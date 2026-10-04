import type { Metadata } from "next";
import { desc, inArray } from "drizzle-orm";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { Badge, Card, Empty, PageHeader } from "@/components/ui";
import { db, schema } from "@/db/client";
import { LOGIN_KINDS } from "@/lib/auth/audit";
import { authMode } from "@/lib/auth/config";
import { getAuth } from "@/lib/auth/server";
import { dateTime } from "@/lib/format";
import { BackupCodesCard, LogoutEverywhere, PasskeyList, SessionList, StepUpCard } from "./security-client";

export const metadata: Metadata = { title: "Sicherheit · TradingEngine" };
export const dynamic = "force-dynamic";

const METHOD: Record<string, string> = { passkey: "Passkey", totp: "Passwort + TOTP", backup_code: "Passwort + Backup-Code", password: "Passwort" };

function device(ua: string | null | undefined): string {
  if (!ua) return "Unbekanntes Gerät";
  const os = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Android/.test(ua) ? "Android" : /Windows/.test(ua) ? "Windows" : /Mac OS X/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "Gerät";
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  return `${browser} · ${os}`;
}

/** Konto & Sicherheit: Passkeys, TOTP/Backup-Codes, aktive Sitzungen, letzte Anmeldungen, Step-up. */
export default async function SecurityPage() {
  if (authMode().mode !== "enabled") {
    return (
      <>
        <PageHeader title="Sicherheit" />
        <Empty>Anmeldung ist lokal deaktiviert (kein TE_PASSWORD gesetzt). Passkeys, TOTP und Sitzungen gibt es erst mit konfigurierter Anmeldung.</Empty>
      </>
    );
  }
  const h = await headers();
  const auth = getAuth();
  const session = await auth.api.getSession({ headers: h });
  if (!session) redirect("/login?next=/settings/security");

  const [passkeys, sessions, logins] = await Promise.all([
    auth.api.listPasskeys({ headers: h }),
    auth.api.listSessions({ headers: h }),
    db()
      .select({ id: schema.auditEvents.id, ts: schema.auditEvents.ts, kind: schema.auditEvents.kind, data: schema.auditEvents.data })
      .from(schema.auditEvents)
      .where(inArray(schema.auditEvents.kind, LOGIN_KINDS))
      .orderBy(desc(schema.auditEvents.ts))
      .limit(12),
  ]);
  const totpEnabled = !!(session.user as { twoFactorEnabled?: boolean | null }).twoFactorEnabled;

  return (
    <>
      <PageHeader title="Sicherheit" subtitle={`Angemeldet als ${session.user.email}. Anmeldung mit Passkey oder mit Passwort + TOTP; heikle Aktionen verlangen eine frische Bestätigung.`} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Passkeys" subtitle="Anmelden und bestätigen ohne Passwort. Entfernen verlangt eine Bestätigung.">
          <PasskeyList passkeys={passkeys.map((p) => ({ id: p.id, name: p.name ?? null, createdAt: p.createdAt ? new Date(p.createdAt).toISOString() : null, backedUp: !!p.backedUp, deviceType: p.deviceType }))} />
        </Card>

        <Card title="Zwei-Faktor (TOTP)" actions={<Badge tone={totpEnabled ? "good" : "critical"}>{totpEnabled ? "Aktiv" : "Nicht eingerichtet"}</Badge>} subtitle="Zweiter Faktor zum Passwort. Kann nicht abgeschaltet werden.">
          <BackupCodesCard />
        </Card>

        <Card title="Bestätigung (Step-up)" subtitle="Gilt 5 Minuten für diese Sitzung – Voraussetzung für Mandate, Limit-Erhöhungen, Positionen schliessen, Schlüssel und Promotion.">
          <StepUpCard />
        </Card>

        <Card title="Aktive Sitzungen" subtitle="30 Tage gültig, bei Nutzung verlängert." actions={<LogoutEverywhere />}>
          <SessionList
            sessions={sessions
              .slice()
              .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
              .map((s) => ({
                id: s.id,
                current: s.id === session.session.id,
                device: device(s.userAgent),
                ip: s.ipAddress || null,
                createdAt: new Date(s.createdAt).toISOString(),
                lastSeen: new Date(s.updatedAt).toISOString(),
                expiresAt: new Date(s.expiresAt).toISOString(),
              }))}
          />
        </Card>

        <Card title="Letzte Anmeldungen" subtitle="Aus dem Audit-Log, inklusive Fehlversuche." className="lg:col-span-2">
          {logins.length === 0 ? (
            <Empty>Noch keine Einträge.</Empty>
          ) : (
            <ul className="divide-y divide-[var(--grid)] text-sm">
              {logins.map((l) => {
                const d = (l.data ?? {}) as { method?: string; ip?: string; userAgent?: string; reason?: string };
                const ok = l.kind === "auth.login.success";
                return (
                  <li key={l.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 first:pt-0 last:pb-0">
                    <Badge tone={ok ? "good" : "critical"}>{ok ? "Erfolgreich" : "Abgelehnt"}</Badge>
                    <span className="tabular">{dateTime(l.ts)}</span>
                    <span className="text-ink-2">{METHOD[d.method ?? ""] ?? d.method ?? "–"}</span>
                    <span className="min-w-0 basis-full truncate text-xs text-muted sm:ml-auto sm:basis-auto">
                      {device(d.userAgent)}
                      {d.ip ? ` · ${d.ip}` : ""}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
