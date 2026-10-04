import Link from "next/link";
import type { ReactNode } from "react";
import { AutoRefresh } from "@/components/auto-refresh";
import { ModeBadge, type Mode } from "@/components/mode-badge";
import { AutopilotBadge, paperHref } from "@/components/paper";
import { SetupHint } from "@/components/setup-hint";
import { FeedList, HeartbeatList } from "@/components/status";
import { AlertList, Badge, Card, PageHeader, Stat } from "@/components/ui";
import { SCHEMA_VERSION } from "@/db/schema";
import { guard } from "@/lib/data/guard";
import { loadOverview } from "@/lib/data/overview";
import { ago, dateTime, int, price } from "@/lib/format";

export const dynamic = "force-dynamic";

/** Status card of one mode. Each mode has its own card — figures of different modes are never added up. */
function StatusCard({ title, mode, state, children, href }: { title: string; mode?: Mode; state: ReactNode; children: ReactNode; href: string }) {
  return (
    <Card
      title={
        <Link href={href} className="hover:underline">
          {title}
        </Link>
      }
      actions={mode && <ModeBadge mode={mode} />}
    >
      <div className="mb-2">{state}</div>
      {children}
    </Card>
  );
}

const NotSetUp = () => <Badge>Nicht eingerichtet</Badge>;

export default async function OverviewPage() {
  const r = await guard(() => loadOverview());

  return (
    <>
      <PageHeader title="Übersicht" subtitle="Was läuft, was offen ist und was Aufmerksamkeit braucht. Kein Echtgeldhandel; der Paper-Autopilot handelt nur simuliert." />
      {!r.ok ? (
        <SetupHint state={r} />
      ) : (
        <>
          <AutoRefresh seconds={15} />
          <Card title="Braucht Aufmerksamkeit" subtitle={r.data.attention.length ? `${r.data.attention.length} offen` : undefined} className="mb-4">
            <AlertList alerts={r.data.attention} />
          </Card>

          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatusCard
              title="Paper-Autopilot"
              mode="PAPER"
              state={r.data.paper.length ? <span className="text-sm font-medium">{r.data.paper.length === 1 ? "1 virtuelles Konto" : `${r.data.paper.length} virtuelle Konten`}</span> : <NotSetUp />}
              href="/autopilot"
            >
              {r.data.paper.length ? (
                <>
                  <ul className="divide-y divide-[var(--grid)]">
                    {r.data.paper.map((a) => (
                      <li key={a.id} className="py-2 first:pt-0 last:pb-0">
                        <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
                          <Link href={paperHref(a.id)} className="min-w-0 break-words text-sm font-medium hover:underline">
                            {a.name}
                          </Link>
                          <AutopilotBadge state={a.state} />
                        </div>
                        {a.episode && (
                          <dl className="mt-1 grid grid-cols-2 gap-x-3 text-xs">
                            <div>
                              <dt className="text-muted">Eigenkapital</dt>
                              <dd className="text-sm font-medium tabular">{price(a.equity ?? a.episode.cash, a.currency)}</dd>
                            </div>
                            <div>
                              <dt className="text-muted">Cash</dt>
                              <dd className="text-sm font-medium tabular">{price(a.episode.cash, a.currency)}</dd>
                            </div>
                          </dl>
                        )}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-2 text-xs text-ink-2">Simuliert, je Konto getrennt – keine Summe über Konten.</p>
                </>
              ) : (
                <p className="text-sm text-ink-2">
                  Kein virtuelles Konto vorhanden.{" "}
                  <Link href="/paper" className="text-accent">
                    Im Paper-Lab anlegen →
                  </Link>
                </p>
              )}
            </StatusCard>
            <StatusCard title="Live-Autopilot" mode="LIVE" state={<NotSetUp />} href="/autopilot">
              <p className="text-sm text-ink-2">Kein Konto verbunden. Echtgeldhandel ist ausgeschaltet. Es gibt keinen Live-Orderweg.</p>
            </StatusCard>
            <StatusCard title="Lernlabor" mode="RESEARCH" state={<NotSetUp />} href="/strategies">
              <p className="text-sm text-ink-2">Noch keine Experimente. Backtests und Gates folgen in Etappe E3.</p>
            </StatusCard>
            <StatusCard title="Signalbetrieb" state={<Badge tone="warning">Signale ungeprüft</Badge>} href="/signals">
              <div className="grid grid-cols-2 gap-3">
                <Stat label="Instrumente im Universum" value={int(r.data.signalOps.universe)} />
                <Stat label="Letztes Signal" value={r.data.signalOps.lastSignalAt ? ago(r.data.signalOps.lastSignalAt, r.data.now) : "—"} hint={r.data.signalOps.lastSignalAt ? dateTime(r.data.signalOps.lastSignalAt) : "Noch keines gespeichert"} />
                <Stat label="BUY (7 Tage)" value={int(r.data.signalOps.buy7d)} />
                <Stat label="NO TRADE (7 Tage)" value={int(r.data.signalOps.noTrade7d)} />
              </div>
              <p className="mt-2 text-xs text-ink-2">Entscheide der Strategie, keine Orders.</p>
            </StatusCard>
          </div>

          <Card
            title="Dienste"
            subtitle={`Lebenszeichen der Engine (OK, wenn jünger als 3 Minuten) und Datenströme · Schema-Version Datenbank ${r.data.dbSchemaVersion ?? "—"}, Web-App ${SCHEMA_VERSION}`}
            className="mt-4"
            actions={
              <Link href="/operations" className="text-xs text-accent">
                Verbindungen & Betrieb →
              </Link>
            }
          >
            <div className="grid gap-x-8 gap-y-4 lg:grid-cols-2">
              <div>
                <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">Engine</h3>
                <HeartbeatList rows={r.data.heartbeats} now={r.data.now} />
              </div>
              <div>
                <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">Datenströme</h3>
                <FeedList rows={r.data.feeds} now={r.data.now} />
              </div>
            </div>
          </Card>
        </>
      )}
    </>
  );
}
