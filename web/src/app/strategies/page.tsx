import { ModeBadge } from "@/components/mode-badge";
import { SetupHint } from "@/components/setup-hint";
import { Badge, Banner, Card, Empty, PageHeader } from "@/components/ui";
import { guard } from "@/lib/data/guard";
import { loadStrategies } from "@/lib/data/strategies";
import { ago, int } from "@/lib/format";

export const dynamic = "force-dynamic";

/** Lifecycle from docs/01, 3.5. Only IDEE exists until the Lernlabor (Etappe E3) evaluates gates. */
const LIFECYCLE: Record<string, string> = {
  IDEE: "Idee – kein Gate geprüft",
};

const PLANNED = [
  { stage: "E3", text: "Statuskarte je Version mit den Gates G1–G5 (bestanden / nicht bestanden / zu wenig Evidenz, jeweils mit Grund)." },
  { stage: "E3", text: "Experimentprotokoll inklusive gescheiterter Varianten, Budgets für Rechenzeit und Anzahl Experimente." },
  { stage: "E3", text: "Drei getrennte Spalten: «automatisch gelernt» · «zur Freigabe vorgeschlagen» · «live freigegeben»." },
];

export default async function StrategiesPage() {
  const r = await guard(loadStrategies);
  return (
    <>
      <PageHeader title="Strategien & Lernlabor" subtitle="Was wurde gelernt, vorgeschlagen, freigegeben? Kein Gate einer Ebene ersetzt ein Gate einer anderen." />
      <Banner>
        <strong>Forschungskandidaten ohne Qualitätsnachweis.</strong> Die Zähler unten sind reine Entscheidzahlen, keine Ergebnisse. Für keine Version liegt ein Backtest, ein Gate oder eine Freigabe vor.
      </Banner>

      {!r.ok ? (
        <SetupHint state={r} />
      ) : !r.data.versions.length ? (
        <Empty>Noch keine Strategieversion registriert. Die Engine trägt ihre Versionen beim ersten Lauf ein.</Empty>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {r.data.versions.map((v) => (
            <Card key={v.id} title={<span className="font-mono">{v.id}</span>} subtitle={`Regimeregel ${v.regimeRuleVersion}`} actions={<ModeBadge mode="RESEARCH" />}>
              <div className="flex flex-wrap gap-1">
                <Badge>{LIFECYCLE[v.lifecycleStatus] ?? v.lifecycleStatus}</Badge>
                <Badge>Nicht für Live freigegeben</Badge>
              </div>
              <dl className="mt-3 grid grid-cols-3 gap-2 text-sm">
                <div>
                  <dt className="text-xs text-muted">Entscheide</dt>
                  <dd className="tabular font-semibold">{int(v.decisions)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted">davon BUY</dt>
                  <dd className="tabular font-semibold">{int(v.buys)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted">Letzter Entscheid</dt>
                  <dd>{v.lastDecisionAt ? ago(v.lastDecisionAt, r.data.now) : "—"}</dd>
                </div>
              </dl>
              <details className="mt-3 text-sm">
                <summary className="cursor-pointer text-ink-2">Parameter (unveränderlich für diese Version)</summary>
                <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-xs">
                  {Object.entries(v.params).map(([k, val]) => (
                    <div key={k} className="flex justify-between gap-2 border-b border-[var(--grid)] py-0.5">
                      <dt className="text-ink-2">{k}</dt>
                      <dd>{String(val)}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            </Card>
          ))}
        </div>
      )}

      <Card title="Noch nicht gebaut" subtitle="Lernlabor und Freigabeprozess folgen in Etappe E3." className="mt-4">
        <ul className="divide-y divide-[var(--grid)]">
          {PLANNED.map((i, n) => (
            <li key={n} className="flex items-start gap-3 py-2 first:pt-0 last:pb-0">
              <Badge title={`Etappe ${i.stage}`}>Etappe {i.stage}</Badge>
              <span className="min-w-0 text-sm">{i.text}</span>
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}
