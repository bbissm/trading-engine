import Link from "next/link";
import type { ExperimentRow, GateRow, VersionCard } from "@/lib/data/lab";
import { GATES } from "@/lib/data/lab";
import { dateTime, decimal } from "@/lib/format";
import { Badge, Banner, Empty, TableWrap, type Tone } from "./ui";

/** Required on every research page. */
export function ResearchBanner() {
  return (
    <Banner>
      <strong>Forschungsergebnisse sind kein Gewinnversprechen.</strong> Historische Tests können überangepasst sein; massgeblich sind die Gates.
    </Banner>
  );
}

/** Lifecycle of a strategy version (docs/01, 3.5); stored without umlauts. */
export const LIFECYCLE: Record<string, { label: string; tone: Tone }> = {
  IDEE: { label: "Idee – kein Gate bestanden", tone: "neutral" },
  HISTORISCH_GEPRUEFT: { label: "Historisch geprüft (G1)", tone: "accent" },
  VALIDIERT: { label: "Validiert (G2)", tone: "accent" },
  FORWARD_PAPER: { label: "Forward-Paper (G3 läuft)", tone: "accent" },
  SHADOW: { label: "Shadow (G3 läuft)", tone: "accent" },
  FREIGABE_VORGESCHLAGEN: { label: "Zur Freigabe vorgeschlagen", tone: "warning" },
  LIVE_FREIGEGEBEN: { label: "Live freigegeben (G4)", tone: "good" },
  LIVE_PILOT: { label: "Live-Pilot (G5)", tone: "good" },
  LIVE_AKTIV: { label: "Live aktiv", tone: "good" },
  ABGELEHNT: { label: "Abgelehnt", tone: "critical" },
  ZU_WENIG_EVIDENZ: { label: "Zu wenig Evidenz", tone: "warning" },
  ZURUECKGENOMMEN: { label: "Zurückgenommen", tone: "neutral" },
  STILLGELEGT: { label: "Stillgelegt", tone: "neutral" },
};
export const lifecycle = (s: string) => LIFECYCLE[s] ?? LIFECYCLE[s.replace("Ü", "UE")] ?? { label: s, tone: "neutral" as Tone };

export const GATE_NAME: Record<string, string> = {
  G1: "Historische Prüfung (Walk-forward)",
  G2: "Unabhängige Validierung (Holdout)",
  G3: "Forward-Paper / Shadow",
  G4: "Echtgeld-Freigabe (durch dich)",
  G5: "Live-Pilot",
};

const GATE_RESULT: Record<string, { label: string; tone: Tone }> = {
  PASSED: { label: "✓ bestanden", tone: "good" },
  FAILED: { label: "✕ nicht bestanden", tone: "critical" },
  INSUFFICIENT: { label: "… zu wenig Evidenz", tone: "warning" },
};

/** Machine-readable rejection reasons (docs/04, 4.3) in plain German. */
export const REASON: Record<string, string> = {
  NETTO_NEGATIV: "Netto-Erwartungswert je Trade nach Kosten nicht über 0.",
  KOSTENSENSITIV: "Der Vorteil verschwindet bei Kosten × 1.5 – er hängt an der Kostenannahme.",
  ZU_WENIG_FAELLE: "Zu wenige unabhängige Fälle (Cluster) für eine belastbare Aussage.",
  REGIME_NICHT_ABGEDECKT: "Die Testfenster enthalten keine Stress- oder Abwärtsphase des Leitmarkts – nur Schönwetter-Evidenz.",
  UEBERANPASSUNG_DSR: "Deflated Sharpe Ratio unter der Schwelle: nach Korrektur für die Anzahl Versuche kein belastbarer Vorteil.",
  UEBERANPASSUNG_PBO: "Wahrscheinlichkeit der Überanpassung (PBO) zu hoch: die Auswahl der besten Variante ist überwiegend Zufall.",
  KEIN_PLATEAU: "Kein Parameter-Plateau: Nachbarwerte ±20 % brechen ein (Nadelspitzen-Parameter).",
  DRAWDOWN_ZU_HOCH: "Maximaler Drawdown im Test über 1.5 × dem konfigurierten Limit.",
  SCHLECHTER_ALS_BASELINE: "Nicht besser als Cash, exposure-gleiches Buy-and-Hold oder Zufallseinstiege.",
  HOLDOUT_EINBRUCH: "Holdout-Ergebnis ausserhalb des 80 %-Intervalls der Walk-forward-Erwartung (Einbruch).",
  FORWARD_INKONSISTENT: "Forward-Paper-Ergebnis unter dem 10. Perzentil der historischen Erwartung.",
  BETRIEB_INSTABIL: "Ungelöste Abgleichs- oder Datenstörung im Forward-Betrieb.",
  ZU_WENIG_EVIDENZ: "Zu wenig Evidenz: kein Durchfallen, aber auch kein Bestehen.",
  HOLDOUT_VERBRAUCHT: "Holdout für diese Familie bereits verwendet – nur noch Forward-Evidenz zählt.",
};
export const reasonText = (code: string) => REASON[code] ?? code;

export const OUTCOME: Record<string, { label: string; tone: Tone }> = {
  KANDIDAT: { label: "Kandidat", tone: "accent" },
  KEIN_BELASTBARER_FORTSCHRITT: { label: "Kein belastbarer Fortschritt", tone: "neutral" },
  ZU_WENIG_DATEN: { label: "Zu wenig Daten", tone: "warning" },
  ABGELEHNT: { label: "Abgelehnt", tone: "critical" },
  ABGEBROCHEN: { label: "Abgebrochen", tone: "warning" },
};
export const EXPERIMENT_STATUS: Record<string, { label: string; tone: Tone }> = {
  PLANNED: { label: "geplant", tone: "neutral" },
  RUNNING: { label: "läuft", tone: "accent" },
  PAUSED: { label: "pausiert", tone: "warning" },
  DONE: { label: "abgeschlossen", tone: "neutral" },
  ABORTED: { label: "abgebrochen", tone: "warning" },
};
export const KIND_LABEL: Record<string, string> = { BACKTEST: "Backtest", WALK_FORWARD: "Walk-forward", OPTIMIZE: "Optimierung", HOLDOUT: "Holdout", METALABEL: "Meta-Labeling" };

export const labHref = (id: number) => `/lab/${id}`;

export function OutcomeBadge({ outcome, status }: { outcome: string | null; status: string }) {
  if (!outcome) {
    const s = EXPERIMENT_STATUS[status] ?? { label: status, tone: "neutral" as Tone };
    return <Badge tone={s.tone}>{s.label}</Badge>;
  }
  const o = OUTCOME[outcome] ?? { label: outcome, tone: "neutral" as Tone };
  return <Badge tone={o.tone}>{o.label}</Badge>;
}

export function GateBadge({ gate }: { gate: GateRow | undefined }) {
  if (!gate) return <Badge>nicht bewertet</Badge>;
  const r = GATE_RESULT[gate.result] ?? { label: gate.result, tone: "neutral" as Tone };
  return <Badge tone={r.tone}>{r.label}</Badge>;
}

/** Criteria table of one gate evaluation: actual vs required with a mark per row. */
export function GateCriteria({ gate }: { gate: GateRow }) {
  return (
    <div className="mt-2 space-y-2">
      {gate.criteria.length > 0 && (
        <TableWrap>
          <table className="data text-xs">
            <thead>
              <tr>
                <th>Kriterium</th>
                <th className="num">Ist</th>
                <th className="num">Soll</th>
                <th>Erfüllt</th>
              </tr>
            </thead>
            <tbody>
              {gate.criteria.map((c, i) => (
                <tr key={i}>
                  <td>{c.name}</td>
                  <td className="num">{c.actual}</td>
                  <td className="num">{c.required}</td>
                  <td>{c.passed === null ? "– offen" : c.passed ? "✓ ja" : "✕ nein"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
      {gate.reasons.length > 0 && (
        <ul className="space-y-1 text-xs">
          {gate.reasons.map((r) => (
            <li key={r}>
              <span className="font-mono text-ink-2">{r}</span> – {reasonText(r)}
            </li>
          ))}
        </ul>
      )}
      <p className="text-[11px] text-ink-2">
        Bewertet {dateTime(gate.evaluatedAt)} · Gate-Konfiguration {gate.gateConfigVersion}
        {gate.experimentId !== null && (
          <>
            {" "}
            ·{" "}
            <Link href={labHref(gate.experimentId)} className="text-accent">
              Experiment {gate.experimentId}
            </Link>
          </>
        )}
      </p>
    </div>
  );
}

/** Status card of one strategy version with G1–G5. */
export function VersionStatusCard({ v }: { v: VersionCard }) {
  const l = lifecycle(v.lifecycleStatus);
  return (
    <article className="min-w-0 rounded-xl border border-line bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="break-all font-mono text-sm font-semibold">{v.id}</h3>
          <p className="text-xs text-muted">
            Regimeregel {v.regimeRuleVersion} · {decimal(String(v.decisions), 0)} gespeicherte Entscheide
            {v.learned && v.sourceExperimentId !== null && (
              <>
                {" "}
                · gelernt in{" "}
                <Link href={labHref(v.sourceExperimentId)} className="text-accent">
                  Experiment {v.sourceExperimentId}
                </Link>
              </>
            )}
          </p>
        </div>
        <Badge tone={l.tone}>{l.label}</Badge>
      </div>
      <ul className="mt-3 divide-y divide-[var(--grid)]">
        {GATES.map((g) => {
          const e = v.gates[g];
          return (
            <li key={g} className="py-2 first:pt-0 last:pb-0">
              <details>
                <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2">
                  <span className="text-sm">
                    <span className="font-semibold">{g}</span> <span className="text-ink-2">{GATE_NAME[g]}</span>
                  </span>
                  <GateBadge gate={e} />
                </summary>
                {e ? <GateCriteria gate={e} /> : <p className="mt-1 text-xs text-ink-2">{g === "G4" ? "G4 ist deine Aktivierung eines Live-Mandats – kein automatischer Übergang." : "Noch keine Bewertung gespeichert."}</p>}
              </details>
            </li>
          );
        })}
      </ul>
      {v.gates.G3 && <p className="mt-2 text-[11px] text-ink-2">G3 ist kein statistischer Vorteilsbeweis: es prüft nur, ob die Forward-Realität der historischen Erwartung nicht widerspricht und der Betrieb stimmt.</p>}
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-ink-2">Parameter (unveränderlich für diese Version)</summary>
        {Object.keys(v.params).length ? (
          <dl className="mt-2 grid grid-cols-1 gap-x-3 gap-y-1 font-mono text-xs sm:grid-cols-2">
            {Object.entries(v.params).map(([k, val]) => (
              <div key={k} className="flex justify-between gap-2 border-b border-[var(--grid)] py-0.5">
                <dt className="text-ink-2">{k}</dt>
                <dd className="break-all">{typeof val === "object" ? JSON.stringify(val) : String(val)}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="mt-1 text-xs text-ink-2">Keine Parameter gespeichert.</p>
        )}
      </details>
    </article>
  );
}

const cpu = (s: string) => {
  const n = Number(s);
  return n < 120 ? `${decimal(s, 0)} s` : n < 7200 ? `${Math.round(n / 60)} min` : `${(n / 3600).toFixed(1)} h`;
};
export const cpuText = cpu;

/** Experiment protocol incl. failed and aborted runs (cards on phones, table on desktop). */
export function ExperimentList({ rows }: { rows: ExperimentRow[] }) {
  if (!rows.length) return <Empty>Noch kein Experiment gelaufen. Mit «Experiment starten» oben beauftragst du das Lernlabor.</Empty>;
  return (
    <>
      <ul className="space-y-2 md:hidden">
        {rows.map((e) => (
          <li key={e.id} className="rounded-lg border border-line p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Link href={labHref(e.id)} className="text-sm font-medium hover:underline">
                Nr. {e.id} · {KIND_LABEL[e.kind] ?? e.kind}
              </Link>
              <OutcomeBadge outcome={e.outcome} status={e.status} />
            </div>
            <div className="mt-1 break-all font-mono text-xs text-ink-2">{e.strategyVersionId ?? e.strategy}</div>
            <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
              <dt className="text-muted">Varianten</dt>
              <dd className="tabular">{e.variantsTested}</dd>
              <dt className="text-muted">Rechenzeit</dt>
              <dd className="tabular">{cpu(e.cpuSeconds)}</dd>
              <dt className="text-muted">Datensatz</dt>
              <dd className="font-mono">{e.datasetHash ? e.datasetHash.slice(0, 10) : "—"}</dd>
              <dt className="text-muted">Erstellt</dt>
              <dd className="tabular">{dateTime(e.createdAt)}</dd>
              <dt className="text-muted">Beendet</dt>
              <dd className="tabular">{e.finishedAt ? dateTime(e.finishedAt) : "—"}</dd>
            </dl>
          </li>
        ))}
      </ul>
      <div className="hidden md:block">
        <TableWrap>
          <table className="data">
            <thead>
              <tr>
                <th>Nr.</th>
                <th>Art</th>
                <th>Strategie</th>
                <th>Status / Ergebnis</th>
                <th className="num">Varianten</th>
                <th className="num">Rechenzeit</th>
                <th>Datensatz</th>
                <th>Erstellt</th>
                <th>Beendet</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id}>
                  <td>
                    <Link href={labHref(e.id)} className="font-medium text-accent">
                      {e.id}
                    </Link>
                  </td>
                  <td>{KIND_LABEL[e.kind] ?? e.kind}</td>
                  <td className="break-all font-mono text-xs">{e.strategyVersionId ?? e.strategy}</td>
                  <td>
                    <span className="inline-flex flex-wrap gap-1">
                      {!(e.status === "ABORTED" && e.outcome === "ABGEBROCHEN") && <OutcomeBadge outcome={null} status={e.status} />}
                      {e.outcome && <OutcomeBadge outcome={e.outcome} status={e.status} />}
                    </span>
                  </td>
                  <td className="num">{e.variantsTested}</td>
                  <td className="num">{cpu(e.cpuSeconds)}</td>
                  <td className="font-mono text-xs" title={e.datasetHash ?? undefined}>
                    {e.datasetHash ? e.datasetHash.slice(0, 10) : "—"}
                  </td>
                  <td className="whitespace-nowrap tabular">{dateTime(e.createdAt)}</td>
                  <td className="whitespace-nowrap tabular">{e.finishedAt ? dateTime(e.finishedAt) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </div>
    </>
  );
}
