import Link from "next/link";
import { AutoRefresh } from "@/components/auto-refresh";
import { LiveSummary } from "@/components/live";
import { loadLiveSummary } from "@/lib/data/live";
import { ModeBadge } from "@/components/mode-badge";
import { AutopilotBadge, paperHref, PositionList, Simulated } from "@/components/paper";
import { SetupHint } from "@/components/setup-hint";
import { Badge, Card, Empty, PageHeader } from "@/components/ui";
import { guard } from "@/lib/data/guard";
import { listPaperPositions } from "@/lib/data/paper";
import { int } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function PortfolioPage() {
  const [r, live] = await Promise.all([guard(() => listPaperPositions()), guard(() => loadLiveSummary())]);

  return (
    <>
      <PageHeader title="Portfolio & Orders" subtitle="Was halte ich wirklich? Bestände je Modus und Konto – Paper und Live werden nie addiert, es gibt keine Gesamtsumme." />

      <section aria-label="LIVE · Echtgeld" className="mb-8">
        <h2 className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold">
          <ModeBadge mode="LIVE" /> Echter Bestand
        </h2>
        {live.ok ? (
          <LiveSummary summary={live.data} compact />
        ) : (
          <div className="rounded-xl border-2 border-mode-live bg-surface p-4">
            <Badge>Status nicht lesbar</Badge>
            <p className="mt-2 text-sm">Der echte Bestand konnte nicht geladen werden.</p>
          </div>
        )}
      </section>

      <section aria-label="PAPER">
        <h2 className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold">
          <ModeBadge mode="PAPER" /> Simulierter Bestand <Simulated />
        </h2>
        {!r.ok ? (
          <SetupHint state={r} />
        ) : r.data.length ? (
          <div className="space-y-4">
            <AutoRefresh seconds={15} />
            {r.data.map(({ account: a, positions }) => (
              <Card
                key={a.id}
                title={
                  <Link href={paperHref(a.id)} className="hover:underline">
                    {a.name}
                  </Link>
                }
                subtitle={
                  <>
                    <span className="break-all font-mono">{a.id}</span>
                    {a.episode && ` · Episode ${a.episode.number}`} · {int(positions.length)} offene {positions.length === 1 ? "Position" : "Positionen"}, {int(a.workingOrders)} offene {a.workingOrders === 1 ? "Order" : "Orders"}
                  </>
                }
                actions={
                  <span className="flex flex-wrap items-center gap-2">
                    <AutopilotBadge state={a.state} />
                    <ModeBadge mode="PAPER" />
                  </span>
                }
              >
                {positions.length ? <PositionList rows={positions} /> : <Empty>Keine offenen Positionen in diesem Konto.</Empty>}
                <p className="mt-3 text-xs text-ink-2">
                  Orders, abgeschlossene Trades und Ausführungen:{" "}
                  <Link href={paperHref(a.id)} className="text-accent">
                    Konto ansehen →
                  </Link>
                </p>
              </Card>
            ))}
            <p className="text-xs text-ink-2">Jedes Konto steht für sich; über Konten hinweg wird nichts summiert. Stops sind Auslöse- und Kontrollregeln, kein garantierter Maximalverlust.</p>
          </div>
        ) : (
          <Empty>
            Kein virtuelles Konto vorhanden.{" "}
            <Link href="/paper" className="text-accent">
              Im Paper-Lab anlegen →
            </Link>
          </Empty>
        )}
      </section>
    </>
  );
}
