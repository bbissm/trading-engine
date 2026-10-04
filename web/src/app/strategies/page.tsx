import Link from "next/link";
import type { ReactNode } from "react";
import { cpuText, ExperimentList, lifecycle, ResearchBanner, VersionStatusCard } from "@/components/lab";
import { ModeBadge } from "@/components/mode-badge";
import { CommandStatusBadge } from "@/components/paper";
import { SetupHint } from "@/components/setup-hint";
import { Badge, Card, Empty, PageHeader, Stat } from "@/components/ui";
import { guard } from "@/lib/data/guard";
import { EXPERIMENT_KINDS, LAB_BUDGET, loadLabOverview, STRATEGY_FAMILIES, type CommandRow, type VersionCard } from "@/lib/data/lab";
import { ago, dateTime, int } from "@/lib/format";
import { rejectionReason } from "@/lib/paper";
import { ApprovalDecisionForm, StartExperimentForm } from "./lab-controls";

export const dynamic = "force-dynamic";

const COMMAND_LABEL: Record<string, string> = { LAB_RUN: "Experiment starten", LAB_PAUSE: "Experiment pausieren", LAB_RESUME: "Experiment fortsetzen", LAB_ABORT: "Experiment abbrechen", APPROVAL_DECIDE: "Freigabe-Entscheid" };

function Column({ title, subtitle, items, empty, render }: { title: string; subtitle: string; items: VersionCard[]; empty: string; render?: (v: VersionCard) => ReactNode }) {
  return (
    <section className="min-w-0 rounded-xl border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold">
        {title} <span className="font-normal text-ink-2">({items.length})</span>
      </h2>
      <p className="mt-0.5 text-xs text-muted">{subtitle}</p>
      {items.length ? (
        <ul className="mt-3 space-y-2">
          {items.map((v) => (
            <li key={v.id} className="rounded-lg border border-line p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="break-all font-mono text-xs font-semibold">{v.id}</span>
                <Badge tone={lifecycle(v.lifecycleStatus).tone}>{lifecycle(v.lifecycleStatus).label}</Badge>
              </div>
              {render?.(v)}
            </li>
          ))}
        </ul>
      ) : (
        <div className="mt-3">
          <Empty>{empty}</Empty>
        </div>
      )}
    </section>
  );
}

function CommandList({ rows, now }: { rows: CommandRow[]; now: number }) {
  if (!rows.length) return <Empty>Noch kein Befehl an das Lernlabor gesendet.</Empty>;
  return (
    <ul className="divide-y divide-[var(--grid)]">
      {rows.map((c) => {
        const reason = rejectionReason(c.result);
        const p = c.params ?? {};
        const detail = c.type === "LAB_RUN" ? `${String(p.kind ?? "")} · ${String(p.strategy ?? "")}` : c.type === "APPROVAL_DECIDE" ? `Vorschlag ${c.target} → ${String(p.decision ?? "")}` : `Experiment ${c.target}`;
        return (
          <li key={c.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 first:pt-0 last:pb-0">
            <div className="min-w-0 flex-1 basis-48">
              <div className="text-sm font-medium">
                {COMMAND_LABEL[c.type] ?? c.type} <span className="font-normal text-ink-2">· {detail}</span>
              </div>
              <div className="text-xs text-ink-2">
                Nr. {c.id} · <span title={dateTime(c.issuedAt)}>{ago(c.issuedAt, now)}</span> · {c.issuedBy}
              </div>
              {reason && <div className="break-words text-xs text-critical">Grund: {reason}</div>}
            </div>
            <CommandStatusBadge status={c.status} />
          </li>
        );
      })}
    </ul>
  );
}

export default async function StrategiesPage() {
  const r = await guard(() => loadLabOverview());
  return (
    <>
      <PageHeader title="Strategien & Lernlabor" subtitle="Was wurde gelernt, vorgeschlagen, freigegeben? Kein Gate einer Ebene ersetzt ein Gate einer anderen." actions={<ModeBadge mode="RESEARCH" />} />
      <ResearchBanner />
      {!r.ok ? (
        <SetupHint state={r} />
      ) : (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-3">
            <Card title="Experiment starten" subtitle="Auftrag an das Lernlabor (Befehl LAB_RUN)" className="lg:col-span-2" actions={<ModeBadge mode="RESEARCH" />}>
              <StartExperimentForm kinds={EXPERIMENT_KINDS} strategies={STRATEGY_FAMILIES} />
            </Card>
            <Card title="Budgets" subtitle="Startwerte docs/04, 5.5 – die Engine setzt sie durch">
              <div className="grid grid-cols-2 gap-3">
                <Stat label="Läufe diese Woche" value={`${int(r.data.budget.runsThisWeek)} / ${LAB_BUDGET.runsPerWeek}`} tone={r.data.budget.runsThisWeek >= LAB_BUDGET.runsPerWeek ? "critical" : undefined} />
                <Stat label="Rechenzeit diesen Monat" value={cpuText(r.data.budget.cpuSecondsThisMonth)} />
                <Stat label="Varianten je Lauf" value={`≤ ${LAB_BUDGET.variantsPerRun}`} />
                <Stat label="Rechenzeit je Lauf" value="≤ 2 h" />
              </div>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Column title="Automatisch gelernt" subtitle="Versionen, die ein Optimierungs- oder Meta-Labeling-Lauf erzeugt hat" items={r.data.learned} empty="Das Lernlabor hat noch keine eigene Version erzeugt." />
            <Column
              title="Zur Freigabe vorgeschlagen"
              subtitle="Das System schlägt vor; entscheiden kannst nur du"
              items={r.data.proposed}
              empty="Kein offener Freigabevorschlag."
              render={(v) => (
                <div className="mt-2 space-y-2">
                  <p className="text-xs text-ink-2">
                    Vorgeschlagen {dateTime(v.approval?.proposedAt)} von {v.approval?.proposedBy}
                    {v.approval?.replacesVersionId && <> · ersetzt {v.approval.replacesVersionId}</>}
                  </p>
                  {v.approval && <ApprovalDecisionForm approvalId={v.approval.id} />}
                </div>
              )}
            />
            <Column title="Live freigegeben" subtitle="Nur mit Step-up-Anmeldung und aktivem Live-Mandat" items={r.data.live} empty="Keine Version ist live freigegeben. Live-Freigabe erfordert Step-up-Anmeldung und ein Live-Mandat (noch nicht eingerichtet)." />
          </div>

          <section>
            <h2 className="mb-2 text-sm font-semibold">Versionen und Gates</h2>
            {r.data.versions.length ? (
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {r.data.versions.map((v) => (
                  <VersionStatusCard key={v.id} v={v} />
                ))}
              </div>
            ) : (
              <Empty>Noch keine Strategieversion registriert. Die Engine trägt ihre Versionen beim ersten Lauf ein.</Empty>
            )}
          </section>

          <Card title="Experimentprotokoll" subtitle="Alle Läufe, auch gescheiterte und abgebrochene – jede getestete Variante zählt für die Mehrfachtest-Korrektur" actions={<ModeBadge mode="RESEARCH" />}>
            <ExperimentList rows={r.data.experiments} />
          </Card>

          <Card title="Befehle an das Lernlabor" subtitle="Neueste zuerst">
            <CommandList rows={r.data.commands} now={r.data.now} />
          </Card>

          <p className="text-xs text-ink-2">
            Gates und Schwellen: docs/04, Abschnitt 4.3. Ergebnisse aus dem Paper-Betrieb stehen im{" "}
            <Link href="/journal" className="text-accent">
              Journal
            </Link>
            .
          </p>
        </div>
      )}
    </>
  );
}
