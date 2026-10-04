import Link from "next/link";
import { AutoRefresh } from "@/components/auto-refresh";
import { ModeBadge } from "@/components/mode-badge";
import { AccountControls, AutopilotBadge, paperHref } from "@/components/paper";
import { SetupHint } from "@/components/setup-hint";
import { Badge, Card, Empty, PageHeader, Stat } from "@/components/ui";
import { guard } from "@/lib/data/guard";
import { listPaperAccounts, listPaperCommands } from "@/lib/data/paper";
import { dateTime, int, price } from "@/lib/format";
import { autopilotState } from "@/lib/paper";

export const dynamic = "force-dynamic";

async function load() {
  const [accounts, commands] = await Promise.all([listPaperAccounts(), listPaperCommands({ limit: 50 })]);
  return { accounts, commands };
}

export default async function AutopilotPage() {
  const r = await guard(() => load());

  return (
    <>
      <PageHeader title="Autopilot" subtitle="Wer darf was, und wie halte ich ihn an? Paper und Live haben getrennte Zustände und Schalter. Befehle gehen über die Tabelle command an die Engine." />
      {!r.ok ? (
        <SetupHint state={r} />
      ) : (
        <div className="space-y-4">
          <AutoRefresh seconds={5} />

          <h2 className="flex flex-wrap items-center gap-2 text-sm font-semibold">
            Paper-Autopilot <ModeBadge mode="PAPER" />
            <span className="text-xs font-normal text-ink-2">simulierter Handel mit virtuellem Kapital</span>
          </h2>

          {r.data.accounts.length ? (
            r.data.accounts.map((a) => (
              <Card
                key={a.id}
                title={
                  <Link href={paperHref(a.id)} className="hover:underline">
                    {a.name}
                  </Link>
                }
                subtitle={<span className="break-all font-mono">{a.id}</span>}
                actions={<ModeBadge mode="PAPER" />}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <AutopilotBadge state={a.state} />
                  {a.stateAt && <span className="text-xs text-ink-2">seit {dateTime(a.stateAt)}</span>}
                </div>
                <p className="mt-2 text-sm">{autopilotState(a.state).meaning}</p>
                {a.reason && <p className="mt-1 break-words text-xs text-ink-2">Letzte Zustandsänderung: {a.reason}</p>}
                {(a.state === "ENTRIES_PAUSED" || a.state === "WINDING_DOWN") && a.openPositions > 0 && (
                  <p className="mt-1 text-xs text-ink-2">
                    Keine neuen Einstiege – {a.openPositions} {a.openPositions === 1 ? "Position wird" : "Positionen werden"} weiter betreut.
                  </p>
                )}

                {a.episode && (
                  <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
                    <Stat label="Episode" value={a.episode.number} />
                    <Stat label="Offene Positionen" value={int(a.openPositions)} />
                    <Stat label="Offene Orders" value={int(a.workingOrders)} hint={`davon ${int(a.workingEntryOrders)} Einstieg`} />
                    <Stat label="Eigenkapital (simuliert)" value={price(a.equity ?? a.episode.cash, a.currency)} />
                  </div>
                )}

                <div className="mt-4 border-t border-[var(--grid)] pt-4">
                  <AccountControls account={a} commands={r.data.commands} />
                </div>
              </Card>
            ))
          ) : (
            <Empty>
              Kein virtuelles Konto vorhanden.{" "}
              <Link href="/paper" className="text-accent">
                Im Paper-Lab anlegen →
              </Link>
            </Empty>
          )}

          <hr className="!mt-8 border-[var(--grid)]" />
          <section className="rounded-xl border-2 border-mode-live bg-surface p-4" aria-label="Live-Autopilot">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">Live-Autopilot</h2>
              <ModeBadge mode="LIVE" />
            </div>
            <Badge>Nicht eingerichtet</Badge>
            <p className="mt-2 text-sm">Kein Konto verbunden. Echtgeldhandel ist ausgeschaltet. Es gibt keinen Live-Orderweg.</p>
          </section>
        </div>
      )}
    </>
  );
}
