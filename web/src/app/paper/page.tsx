import Link from "next/link";
import { AutoRefresh } from "@/components/auto-refresh";
import { ModeBadge } from "@/components/mode-badge";
import { AutopilotBadge, PaperCommandList, paperHref, Simulated } from "@/components/paper";
import { SetupHint } from "@/components/setup-hint";
import { Badge, Card, Empty, PageHeader, Stat, type Tone } from "@/components/ui";
import { guard } from "@/lib/data/guard";
import { listPaperAccounts, listPaperCommands } from "@/lib/data/paper";
import { dateTime, int, price } from "@/lib/format";
import { CreateAccountForm } from "./create-form";

export const dynamic = "force-dynamic";

type Realism = "simuliert" | "konservativ" | "fehlt";
const REALISM_BADGE: Record<Realism, { tone: Tone; icon: string }> = {
  simuliert: { tone: "accent", icon: "◐" },
  konservativ: { tone: "neutral", icon: "▣" },
  fehlt: { tone: "warning", icon: "✕" },
};

/** Realism card: what the simulator models and what is missing. Static and honest — same list as the engine's simulator. */
const REALISM: { label: string; status: Realism; note?: string }[] = [
  { label: "Gebühren beider Seiten", status: "simuliert", note: "Kostenmodell des Handelsplatzes" },
  { label: "Limit nur bei Durchhandeln", status: "simuliert" },
  { label: "Kurslücken bei Stops", status: "simuliert", note: "schlechterer aus Stop und Eröffnung" },
  { label: "Slippage bei Stop/Market", status: "simuliert", note: "fester Abschlag – Annahme" },
  { label: "Teilfüllungen", status: "simuliert", note: "Anteil am Kerzenvolumen" },
  { label: "Reihenfolge Stop/Ziel in einer Kerze", status: "konservativ", note: "Stop zuerst" },
  { label: "Bid/Ask-Spread", status: "fehlt", note: "Kerzen statt Quotes" },
  { label: "Latenz und Warteschlangenposition", status: "fehlt" },
  { label: "Marktwirkung eigener Orders", status: "fehlt" },
  { label: "Wartungsfenster und Handelsunterbrüche", status: "fehlt" },
];

async function load(now = Date.now()) {
  const [accounts, commands] = await Promise.all([listPaperAccounts(), listPaperCommands({ limit: 8 })]);
  return { now, accounts, commands };
}

export default async function PaperPage() {
  const r = await guard(() => load());

  return (
    <>
      <PageHeader title="Paper-Lab" subtitle="Virtuelle Konten mit virtuellem Kapital. Kein Echtgeld, kein Handelskonto: alle Orders gehen an den Simulator, alle Zahlen sind simuliert." actions={<ModeBadge mode="PAPER" />} />
      {!r.ok ? (
        <SetupHint state={r} />
      ) : (
        <div className="space-y-4">
          <AutoRefresh seconds={10} />

          {r.data.accounts.length ? (
            <div className="grid gap-4 lg:grid-cols-2">
              {r.data.accounts.map((a) => {
                const e = a.episode;
                return (
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
                    <div className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1">
                      <AutopilotBadge state={a.state} />
                      {e && <Badge>Episode {e.number}</Badge>}
                      {a.reason && <span className="min-w-0 break-words text-xs text-ink-2">{a.reason}</span>}
                    </div>
                    {e ? (
                      <>
                        <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
                          <Stat label="Startkapital" value={price(e.startCash, a.currency)} />
                          <Stat label="Eigenkapital" value={price(a.equity ?? e.cash, a.currency)} hint={a.equity ? `Stand ${dateTime(a.equityAt)}` : "Noch keine Bewertung – entspricht Cash"} />
                          <Stat label="Cash" value={price(e.cash, a.currency)} />
                          <Stat label="Realisiertes Ergebnis" value={price(e.realized, a.currency)} hint="nach Gebühren" />
                          <Stat label="Gebühren bezahlt" value={price(e.feesPaid, a.currency)} />
                          <Stat label="Offene Positionen" value={int(a.openPositions)} />
                        </div>
                        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-ink-2">
                          <span className="inline-flex flex-wrap items-center gap-1.5">
                            <Simulated /> Letzte simulierte Kerze: <span className="tabular">{dateTime(e.simThrough)}</span>
                          </span>
                          <Link href={paperHref(a.id)} className="text-accent">
                            Konto ansehen →
                          </Link>
                        </div>
                      </>
                    ) : (
                      <Empty>Für dieses Konto ist keine Episode gespeichert.</Empty>
                    )}
                  </Card>
                );
              })}
            </div>
          ) : (
            <Empty>Kein virtuelles Konto vorhanden. Unten lässt sich eines anlegen; es entsteht, sobald die Engine den Befehl quittiert hat.</Empty>
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Virtuelles Konto anlegen" subtitle="Der Befehl geht über die Tabelle command an die Engine; sie legt Konto und Episode 1 an (Zustand «Bereit»)." actions={<ModeBadge mode="PAPER" />}>
              <CreateAccountForm />
              <h3 className="mb-2 mt-5 text-xs font-medium uppercase tracking-wide text-muted">Letzte Befehle</h3>
              <PaperCommandList rows={r.data.commands} now={r.data.now} />
            </Card>

            <Card title="Realismus-Karte" subtitle="Was der Simulator nachbildet – und was nicht. Paper-Ergebnisse sind deshalb kein Nachweis für Live-Ergebnisse.">
              <ul className="divide-y divide-[var(--grid)]">
                {REALISM.map((x) => (
                  <li key={x.label} className="flex items-start justify-between gap-3 py-2 first:pt-0 last:pb-0">
                    <div className="min-w-0">
                      <div className="text-sm">{x.label}</div>
                      {x.note && <div className="text-xs text-ink-2">{x.note}</div>}
                    </div>
                    <Badge tone={REALISM_BADGE[x.status].tone}>
                      <span aria-hidden>{REALISM_BADGE[x.status].icon}</span>
                      {x.status}
                    </Badge>
                  </li>
                ))}
              </ul>
              <p className="mt-3 rounded-lg border border-line bg-surface-2 p-3 text-xs text-ink-2">
                <span className="font-medium text-ink">Auswertung nur bei Schluss jeder 4h-Kerze:</span> Stops lösen im Modell beim Kerzenschluss aus, nicht im Moment des Kurses.
              </p>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
