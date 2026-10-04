import { AutoRefresh } from "@/components/auto-refresh";
import { SetupHint } from "@/components/setup-hint";
import { FeedList, HeartbeatList, OkBadge } from "@/components/status";
import { Badge, Card, Empty, PageHeader, TableWrap, type Tone } from "@/components/ui";
import { SCHEMA_VERSION } from "@/db/schema";
import { guard } from "@/lib/data/guard";
import { loadOperations } from "@/lib/data/operations";
import { ago, dateTime } from "@/lib/format";
import { PingButton } from "./ping-button";

export const dynamic = "force-dynamic";

const COMMAND_TONE: Record<string, Tone> = { PENDING: "warning", DONE: "good", REJECTED: "critical" };
const COMMAND_LABEL: Record<string, string> = { PENDING: "Wartet auf Engine", DONE: "✓ Quittiert", REJECTED: "✕ Abgelehnt" };

export default async function OperationsPage() {
  const r = await guard(() => loadOperations());

  return (
    <>
      <PageHeader title="Verbindungen & Betrieb" subtitle="Lebenszeichen der Engine, Datenströme, Schema-Version und Befehlskanal Web → Engine." />
      {!r.ok ? (
        <SetupHint state={r} />
      ) : (
        <div className="space-y-4">
          <AutoRefresh seconds={3} />
          <div className="grid gap-4 lg:grid-cols-3">
            <Card title="Engine-Dienste" subtitle="OK, wenn das letzte Lebenszeichen jünger als 3 Minuten ist" className="lg:col-span-2">
              <HeartbeatList rows={r.data.heartbeats} now={r.data.now} detailed />
            </Card>
            <Card title="Schema-Version">
              <dl className="space-y-1.5 text-sm">
                <div className="flex justify-between gap-3">
                  <dt className="text-ink-2">Datenbank (schema_meta)</dt>
                  <dd className="font-medium tabular">{r.data.dbSchemaVersion ?? "—"}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-ink-2">Web-App erwartet</dt>
                  <dd className="font-medium tabular">{SCHEMA_VERSION}</dd>
                </div>
              </dl>
              <div className="mt-3">
                <OkBadge ok={r.data.dbSchemaVersion === SCHEMA_VERSION} okLabel="Stimmt überein" failLabel="Weicht ab" />
              </div>
              <p className="mt-2 text-xs text-ink-2">Die Engine verweigert den Start bei abweichender Version. Migrationen laufen als eigener Schritt (pnpm db:migrate), nicht im Build.</p>
            </Card>
          </div>

          <Card title="Datenströme" subtitle="Zustand je Instrument und Zeitebene">
            <FeedList rows={r.data.feeds} now={r.data.now} detailed />
          </Card>

          <Card title="Befehlskanal" subtitle="Die Web-App ruft die Engine nie direkt auf: ein Befehl ist eine Zeile in der Tabelle command, die Engine quittiert ihn innert etwa einer Minute.">
            <PingButton />
            <h3 className="mb-2 mt-4 text-xs font-medium uppercase tracking-wide text-muted">Letzte 10 Befehle</h3>
            {r.data.commands.length ? (
              <TableWrap>
                <table className="data">
                  <thead>
                    <tr>
                      <th className="num">Nr.</th>
                      <th>Typ</th>
                      <th>Status</th>
                      <th>Ausgelöst</th>
                      <th>Von</th>
                      <th>Quittiert</th>
                      <th>Ergebnis</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.data.commands.map((c) => (
                      <tr key={c.id}>
                        <td className="num">{c.id}</td>
                        <td className="font-medium">{c.type}</td>
                        <td>
                          <Badge tone={COMMAND_TONE[c.status] ?? "neutral"}>{COMMAND_LABEL[c.status] ?? c.status}</Badge>
                        </td>
                        <td className="whitespace-nowrap" title={dateTime(c.issuedAt)}>
                          {ago(c.issuedAt, r.data.now)}
                        </td>
                        <td className="whitespace-nowrap">{c.issuedBy}</td>
                        <td className="whitespace-nowrap tabular">{c.handledAt ? dateTime(c.handledAt) : "—"}</td>
                        <td className="break-all font-mono text-xs">{c.result ? JSON.stringify(c.result) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            ) : (
              <Empty>Noch kein Befehl gesendet.</Empty>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
