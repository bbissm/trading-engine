import Link from "next/link";
import { notFound } from "next/navigation";
import { ExperimentControls } from "@/app/strategies/lab-controls";
import { EquityChart } from "@/components/equity-chart";
import { cpuText, GATE_NAME, GateBadge, GateCriteria, KIND_LABEL, OutcomeBadge, REASON, reasonText, ResearchBanner } from "@/components/lab";
import { ModeBadge } from "@/components/mode-badge";
import { CommandStatusBadge } from "@/components/paper";
import { SetupHint } from "@/components/setup-hint";
import { Badge, Card, Empty, PageHeader, Stat, TableWrap } from "@/components/ui";
import { guard } from "@/lib/data/guard";
import { loadExperiment } from "@/lib/data/lab";
import { date, dateTime, decimal } from "@/lib/format";
import { rejectionReason } from "@/lib/paper";

export const dynamic = "force-dynamic";

const show = (v: unknown): string => (v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));
const num = (v: string) => (/^-?\d+(\.\d+)?$/.test(v) ? decimal(v, 0) : v);

export default async function ExperimentPage({ params }: { params: Promise<{ experimentId: string }> }) {
  const raw = (await params).experimentId;
  const id = /^\d{1,15}$/.test(raw) ? Number(raw) : null;
  if (id === null) notFound();
  const r = await guard(() => loadExperiment(id));
  if (r.ok && !r.data) notFound();
  if (!r.ok || !r.data) {
    return (
      <>
        <PageHeader title={`Experiment ${id}`} actions={<ModeBadge mode="RESEARCH" />} />
        {!r.ok && <SetupHint state={r} />}
      </>
    );
  }
  const { experiment: e, summary: s, trials, gates, holdout, commands } = r.data;
  const accessIndex = holdout.findIndex((h) => h.experimentId === e.id);
  const metricKeys = [...new Set(trials.flatMap((t) => Object.keys(t.metrics ?? {})))].slice(0, 6);
  const curve = s.equity.map(([iso, v]) => ({ time: Math.floor(Date.parse(iso) / 1000), equity: Number(v) }));

  return (
    <>
      <PageHeader
        title={`Experiment ${e.id} · ${KIND_LABEL[e.kind] ?? e.kind}`}
        subtitle={
          <>
            <span className="font-mono">{e.strategyVersionId ?? e.strategy}</span> · erstellt {dateTime(e.createdAt)} von {e.issuedBy}
          </>
        }
        actions={
          <>
            <OutcomeBadge outcome={null} status={e.status} />
            {e.outcome && <OutcomeBadge outcome={e.outcome} status={e.status} />}
            <ModeBadge mode="RESEARCH" />
          </>
        }
      />
      <ResearchBanner />
      <div className="mb-3 text-sm">
        <Link href="/strategies" className="text-accent">
          ← Strategien & Lernlabor
        </Link>
      </div>

      <div className="space-y-4">
        <Card title={s.headline ?? "Kein Ergebnistext gespeichert"} subtitle={`Hypothese: ${e.hypothesis}`} actions={<Badge>simuliert</Badge>}>
          <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Varianten getestet" value={e.variantsTested} />
            <Stat label="Rechenzeit" value={cpuText(e.cpuSeconds)} />
            <Stat label="Datensatz" value={<span className="font-mono text-sm">{e.datasetHash ? e.datasetHash.slice(0, 12) : "—"}</span>} hint={e.dataFrom ? `${date(e.dataFrom)} – ${date(e.dataTo)}` : undefined} />
            <Stat label="Gestartet" value={<span className="text-sm">{dateTime(e.startedAt)}</span>} />
            <Stat label="Beendet" value={<span className="text-sm">{dateTime(e.finishedAt)}</span>} />
            <Stat label="Holdout-Zugriff" value={accessIndex < 0 ? "nein" : `${accessIndex + 1}. Zugriff`} hint={accessIndex > 0 ? "Holdout verbraucht – nur noch Forward-Evidenz zählt" : accessIndex === 0 ? "erster und einziger zulässiger Zugriff" : undefined} />
          </div>
          {s.metrics.length > 0 && (
            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {s.metrics.map(([name, m]) => (
                <div key={name} className="rounded-lg border border-line p-3">
                  <div className="text-xs font-medium text-muted">{name.replace(/_/g, " ")}</div>
                  <div className="mt-0.5 text-lg font-semibold tabular">
                    {m.value}
                    {m.unit && <span className="ml-1 text-sm font-normal text-ink-2">{m.unit}</span>}
                  </div>
                  {m.ci && (
                    <div className="text-xs text-ink-2">
                      Intervall {m.ci[0]} bis {m.ci[1]}
                    </div>
                  )}
                  {m.note && <div className="text-xs text-ink-2">{m.note}</div>}
                </div>
              ))}
            </div>
          )}
          {(s.reasons.length > 0 || s.warnings.length > 0) && (
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {s.reasons.length > 0 && (
                <div>
                  <div className="text-xs font-medium text-muted">Begründung</div>
                  <ul className="mt-1 space-y-1 text-sm">
                    {s.reasons.map((x, i) => (
                      <li key={i}>{REASON[x] ? <><span className="font-mono text-xs text-ink-2">{x}</span> – {reasonText(x)}</> : x}</li>
                    ))}
                  </ul>
                </div>
              )}
              {s.warnings.length > 0 && (
                <div>
                  <div className="text-xs font-medium text-muted">Warnungen</div>
                  <ul className="mt-1 space-y-1 text-sm">
                    {s.warnings.map((x, i) => (
                      <li key={i}>⚠️ {x}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </Card>

        <Card title="Bedienung" subtitle="Befehle LAB_PAUSE / LAB_RESUME / LAB_ABORT an die Engine">
          <ExperimentControls experimentId={e.id} status={e.status} />
          {commands.length > 0 && (
            <ul className="mt-3 divide-y divide-[var(--grid)] text-sm">
              {commands.map((c) => (
                <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 py-1.5">
                  <span>
                    {c.type} · Nr. {c.id} · {dateTime(c.issuedAt)}
                    {rejectionReason(c.result) && <span className="text-critical"> · {rejectionReason(c.result)}</span>}
                  </span>
                  <CommandStatusBadge status={c.status} />
                </li>
              ))}
            </ul>
          )}
        </Card>

        {gates.length > 0 && (
          <Card title="Gate-Bewertungen aus diesem Lauf">
            <ul className="divide-y divide-[var(--grid)]">
              {gates.map((g) => (
                <li key={g.id} className="py-2 first:pt-0 last:pb-0">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-sm">
                      <span className="font-semibold">{g.gate}</span> <span className="text-ink-2">{GATE_NAME[g.gate]}</span> · <span className="font-mono text-xs">{g.strategyVersionId}</span>
                    </span>
                    <GateBadge gate={g} />
                  </div>
                  <GateCriteria gate={g} />
                </li>
              ))}
            </ul>
          </Card>
        )}

        {(s.baselines.length > 0 || s.sensitivity.length > 0) && (
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Baselines" subtitle="Gleicher Zeitraum, gleiche Kosten">
              {s.baselines.length ? (
                <TableWrap>
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Baseline</th>
                        <th>Kennzahl</th>
                        <th className="num">Wert</th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.baselines.map((b, i) => (
                        <tr key={i}>
                          <td>{b.name}</td>
                          <td className="text-ink-2">{b.metric}</td>
                          <td className="num">{b.value}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              ) : (
                <Empty>Keine Baselines gespeichert.</Empty>
              )}
            </Card>
            <Card title="Sensitivität" subtitle="Kosten × 1.5 / × 2, Slippage +50 %, Einstieg verzögert …">
              {s.sensitivity.length ? (
                <TableWrap>
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Szenario</th>
                        <th className="num">Trades</th>
                        <th className="num">Netto</th>
                        <th className="num">Erwartung (R)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.sensitivity.map((x, i) => (
                        <tr key={i}>
                          <td>{x.scenario}</td>
                          <td className="num">{x.trades ?? "—"}</td>
                          <td className="num">{x.net}</td>
                          <td className="num">{x.expectancyR ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              ) : (
                <Empty>Keine Sensitivitätsläufe gespeichert.</Empty>
              )}
            </Card>
          </div>
        )}

        {curve.length > 1 && (
          <Card title="Eigenkapitalkurve des Tests" subtitle="Simuliert, historisch – kein Gewinnversprechen" actions={<Badge>simuliert</Badge>}>
            <EquityChart points={curve} startCash={curve[0].equity} />
          </Card>
        )}

        {s.folds.length > 0 && (
          <Card title="Walk-forward-Fenster" subtitle="Training auf älterem, Test auf späterem Fenster">
            <TableWrap>
              <table className="data">
                <thead>
                  <tr>
                    <th>Nr.</th>
                    <th>Training</th>
                    <th>Test</th>
                    <th className="num">Trades</th>
                    <th className="num">Netto (Test)</th>
                  </tr>
                </thead>
                <tbody>
                  {s.folds.map((f, i) => (
                    <tr key={i}>
                      <td>{i + 1}</td>
                      <td className="whitespace-nowrap tabular">{f.train ? `${date(f.train[0])} – ${date(f.train[1])}` : "—"}</td>
                      <td className="whitespace-nowrap tabular">{f.test ? `${date(f.test[0])} – ${date(f.test[1])}` : "—"}</td>
                      <td className="num">{f.trades ?? "—"}</td>
                      <td className="num">{f.net}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </Card>
        )}

        <Card title={`Getestete Varianten (${trials.length})`} subtitle="Jede Variante zählt – auch schlechte und abgebrochene (Deflated Sharpe, PBO)">
          {trials.length ? (
            <TableWrap>
              <table className="data">
                <thead>
                  <tr>
                    <th>Nr.</th>
                    <th>Parameter</th>
                    {metricKeys.map((k) => (
                      <th key={k} className="num">
                        {k.replace(/_/g, " ")}
                      </th>
                    ))}
                    <th className="num">Fenster</th>
                  </tr>
                </thead>
                <tbody>
                  {trials.map((t) => (
                    <tr key={t.id}>
                      <td>{t.id}</td>
                      <td className="font-mono text-xs">
                        {Object.entries(t.variant)
                          .map(([k, v]) => `${k}=${show(v)}`)
                          .join(", ") || "—"}
                      </td>
                      {metricKeys.map((k) => (
                        <td key={k} className="num">
                          {num(show(t.metrics?.[k]))}
                        </td>
                      ))}
                      <td className="num">{t.folds?.length ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          ) : (
            <Empty>Keine Varianten gespeichert.</Empty>
          )}
        </Card>

        <Card title="Holdout-Zugriffe der Familie" subtitle={`${e.strategy} – pro Strategiefamilie genau einmal für G2 zulässig`}>
          {holdout.length ? (
            <ul className="divide-y divide-[var(--grid)] text-sm">
              {holdout.map((h, i) => (
                <li key={h.id} className="flex flex-wrap items-center justify-between gap-2 py-1.5">
                  <span>
                    {i + 1}. Zugriff · {dateTime(h.accessedAt)} ·{" "}
                    <Link href={`/lab/${h.experimentId}`} className="text-accent">
                      Experiment {h.experimentId}
                    </Link>
                  </span>
                  {i > 0 ? <Badge tone="warning">Holdout verbraucht</Badge> : <Badge>erster Zugriff</Badge>}
                </li>
              ))}
            </ul>
          ) : (
            <Empty>Der Holdout dieser Familie wurde noch nie geöffnet.</Empty>
          )}
        </Card>

        <details className="rounded-xl border border-line bg-surface p-4 text-sm">
          <summary className="cursor-pointer font-medium">Konfiguration und Fortschritt (Rohdaten)</summary>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs text-ink-2">{JSON.stringify({ config: e.config, progress: e.progress }, null, 2)}</pre>
        </details>
      </div>
    </>
  );
}
