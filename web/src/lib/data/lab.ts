import "server-only";
import { asc, count, desc, eq, gte, inArray, sql } from "drizzle-orm";
import { z } from "zod";
import { db } from "@/db/client";
import { approvals, commands, experiments, experimentTrials, gateEvaluations, holdoutAccesses, signals, strategyVersions } from "@/db/schema";

/**
 * Strategies & Lernlabor (docs/02, screen 7; docs/04). Research only: nothing here touches an account.
 * The web app proposes nothing and promotes nothing — it shows what the lab stored and writes LAB_* / APPROVAL_DECIDE
 * commands for the engine. «Für Live freigeben» is not available here (step-up + live mandate, separate package).
 */

export type ExperimentRow = typeof experiments.$inferSelect;
export type TrialRow = typeof experimentTrials.$inferSelect;
export type GateRow = typeof gateEvaluations.$inferSelect;
export type ApprovalRow = typeof approvals.$inferSelect;
export type CommandRow = typeof commands.$inferSelect;

export const GATES = ["G1", "G2", "G3", "G4", "G5"] as const;
export const EXPERIMENT_KINDS = ["BACKTEST", "WALK_FORWARD", "OPTIMIZE", "HOLDOUT", "METALABEL"] as const;
export const STRATEGY_FAMILIES = ["s1-trend-pullback", "s2-volume-breakout", "s3-mean-reversion"] as const;
export const LAB_COMMANDS = ["LAB_RUN", "LAB_PAUSE", "LAB_RESUME", "LAB_ABORT", "APPROVAL_DECIDE"] as const;
/** Experiment kinds that create a new strategy version (they count as «automatisch gelernt»). */
export const LEARNING_KINDS = ["OPTIMIZE", "METALABEL"];

/** Budgets per lab (docs/04, 5.5 — start values). */
export const LAB_BUDGET = { runsPerWeek: 2, variantsPerRun: 50, cpuSecondsPerRun: 7200 };

export interface VersionCard {
  id: string;
  strategy: string;
  version: number;
  params: Record<string, unknown>;
  regimeRuleVersion: string;
  lifecycleStatus: string;
  createdAt: Date;
  decisions: number;
  /** latest evaluation per gate (G1–G5); missing gate = not evaluated */
  gates: Partial<Record<(typeof GATES)[number], GateRow>>;
  /** latest approval proposal for this version */
  approval: ApprovalRow | null;
  /** created by an OPTIMIZE / METALABEL experiment of the lab */
  learned: boolean;
  /** experiment that produced it (when learned) */
  sourceExperimentId: number | null;
}

export interface LabOverview {
  now: number;
  versions: VersionCard[];
  learned: VersionCard[];
  proposed: (VersionCard & { approval: ApprovalRow })[];
  live: VersionCard[];
  experiments: ExperimentRow[];
  commands: CommandRow[];
  budget: { runsThisWeek: number; cpuSecondsThisMonth: string };
}

const weekStart = (now: number) => {
  const d = new Date(now);
  const day = (d.getUTCDay() + 6) % 7; // Monday = 0
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - day));
};

export async function loadLabOverview(now = Date.now()): Promise<LabOverview> {
  const monthStart = new Date(Date.UTC(new Date(now).getUTCFullYear(), new Date(now).getUTCMonth(), 1));
  const [versions, decisionCounts, gateRows, approvalRows, experimentRows, commandRows, [week], [month]] = await Promise.all([
    db().select().from(strategyVersions).orderBy(asc(strategyVersions.strategy), asc(strategyVersions.version)),
    db().select({ id: signals.strategyVersionId, n: count() }).from(signals).groupBy(signals.strategyVersionId),
    db()
      .selectDistinctOn([gateEvaluations.strategyVersionId, gateEvaluations.gate])
      .from(gateEvaluations)
      .orderBy(gateEvaluations.strategyVersionId, gateEvaluations.gate, desc(gateEvaluations.evaluatedAt), desc(gateEvaluations.id)),
    db().selectDistinctOn([approvals.strategyVersionId]).from(approvals).orderBy(approvals.strategyVersionId, desc(approvals.proposedAt), desc(approvals.id)),
    db().select().from(experiments).orderBy(desc(experiments.createdAt), desc(experiments.id)).limit(200),
    db()
      .select()
      .from(commands)
      .where(inArray(commands.type, [...LAB_COMMANDS]))
      .orderBy(desc(commands.id))
      .limit(15),
    db().select({ n: count() }).from(experiments).where(gte(experiments.createdAt, weekStart(now))),
    db().select({ s: sql<string>`coalesce(sum(${experiments.cpuSeconds}), 0)::text` }).from(experiments).where(gte(experiments.createdAt, monthStart)),
  ]);

  const learnedBy = new Map<string, number>();
  for (const e of [...experimentRows].reverse()) if (e.strategyVersionId && LEARNING_KINDS.includes(e.kind) && !learnedBy.has(e.strategyVersionId)) learnedBy.set(e.strategyVersionId, e.id);

  const cards: VersionCard[] = versions.map((v) => ({
    id: v.id,
    strategy: v.strategy,
    version: v.version,
    params: v.params,
    regimeRuleVersion: v.regimeRuleVersion,
    lifecycleStatus: v.lifecycleStatus,
    createdAt: v.createdAt,
    decisions: decisionCounts.find((d) => d.id === v.id)?.n ?? 0,
    gates: Object.fromEntries(gateRows.filter((g) => g.strategyVersionId === v.id).map((g) => [g.gate, g])),
    approval: approvalRows.find((a) => a.strategyVersionId === v.id) ?? null,
    learned: learnedBy.has(v.id),
    sourceExperimentId: learnedBy.get(v.id) ?? null,
  }));
  return {
    now,
    versions: cards,
    learned: cards.filter((c) => c.learned),
    proposed: cards.filter((c): c is VersionCard & { approval: ApprovalRow } => c.approval?.decision === "PENDING"),
    live: cards.filter((c) => c.approval?.decision === "APPROVED_LIVE"),
    experiments: experimentRows,
    commands: commandRows,
    budget: { runsThisWeek: week?.n ?? 0, cpuSecondsThisMonth: month?.s ?? "0" },
  };
}

// ───────────────────────── experiment summary contract ─────────────────────────

export interface SummaryMetric {
  value: string;
  unit?: string;
  ci?: [string, string];
  note?: string;
}
export interface ExperimentSummary {
  headline: string | null;
  metrics: [string, SummaryMetric][];
  baselines: { name: string; metric: string; value: string }[];
  sensitivity: { scenario: string; trades: number | null; net: string; expectancyR: string | null }[];
  equity: [string, string][];
  folds: { train: [string, string] | null; test: [string, string] | null; trades: number | null; net: string }[];
  reasons: string[];
  warnings: string[];
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const s = (v: unknown): string | null => (typeof v === "string" ? v : typeof v === "number" && Number.isFinite(v) ? String(v) : null);
const n = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const pair = (v: unknown): [string, string] | null => (Array.isArray(v) && v.length === 2 && s(v[0]) !== null && s(v[1]) !== null ? [s(v[0])!, s(v[1])!] : null);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/**
 * Parses `experiment.summary` defensively: every field is optional, malformed entries are skipped, unknown fields
 * ignored. Contract: { headline, metrics: {name: {value, unit?, ci?, note?}}, baselines: [{name, metric, value}],
 * sensitivity: [{scenario, trades, net, expectancy_r}], equity: [[iso, value]], folds: [{train, test, trades, net}],
 * reasons: string[], warnings: string[] }.
 */
export function parseSummary(raw: unknown): ExperimentSummary {
  const o = isObj(raw) ? raw : {};
  const metrics: [string, SummaryMetric][] = isObj(o.metrics)
    ? Object.entries(o.metrics).flatMap(([k, m]) => {
        if (!isObj(m) || s(m.value) === null) return [];
        const out: SummaryMetric = { value: s(m.value)! };
        if (typeof m.unit === "string") out.unit = m.unit;
        const ci = pair(m.ci);
        if (ci) out.ci = ci;
        if (typeof m.note === "string") out.note = m.note;
        return [[k, out] as [string, SummaryMetric]];
      })
    : [];
  return {
    headline: typeof o.headline === "string" ? o.headline : null,
    metrics,
    baselines: arr(o.baselines).flatMap((b) => (isObj(b) && s(b.name) && s(b.value) !== null ? [{ name: s(b.name)!, metric: s(b.metric) ?? "", value: s(b.value)! }] : [])),
    sensitivity: arr(o.sensitivity).flatMap((x) =>
      isObj(x) && s(x.scenario) && s(x.net) !== null ? [{ scenario: s(x.scenario)!, trades: n(x.trades), net: s(x.net)!, expectancyR: x.expectancy_r === null ? null : s(x.expectancy_r) }] : [],
    ),
    equity: arr(o.equity).flatMap((p) => {
      const q = pair(p);
      return q && !Number.isNaN(Date.parse(q[0])) ? [q] : [];
    }),
    folds: arr(o.folds).flatMap((x) => (isObj(x) && s(x.net) !== null ? [{ train: pair(x.train), test: pair(x.test), trades: n(x.trades), net: s(x.net)! }] : [])),
    reasons: arr(o.reasons).filter((r): r is string => typeof r === "string"),
    warnings: arr(o.warnings).filter((r): r is string => typeof r === "string"),
  };
}

export interface ExperimentDetail {
  now: number;
  experiment: ExperimentRow;
  summary: ExperimentSummary;
  trials: TrialRow[];
  gates: GateRow[];
  /** all holdout accesses of the experiment's strategy family, oldest first */
  holdout: (typeof holdoutAccesses.$inferSelect)[];
  commands: CommandRow[];
}

export async function loadExperiment(id: number, now = Date.now()): Promise<ExperimentDetail | null> {
  const [experiment] = await db().select().from(experiments).where(eq(experiments.id, id));
  if (!experiment) return null;
  const [trials, gates, holdout, cmds] = await Promise.all([
    db().select().from(experimentTrials).where(eq(experimentTrials.experimentId, id)).orderBy(asc(experimentTrials.id)),
    db().select().from(gateEvaluations).where(eq(gateEvaluations.experimentId, id)).orderBy(asc(gateEvaluations.gate), desc(gateEvaluations.evaluatedAt)),
    db().select().from(holdoutAccesses).where(eq(holdoutAccesses.strategy, experiment.strategy)).orderBy(asc(holdoutAccesses.accessedAt), asc(holdoutAccesses.id)),
    db()
      .select()
      .from(commands)
      .where(sql`${commands.type} in ('LAB_PAUSE', 'LAB_RESUME', 'LAB_ABORT') and ${commands.target} = ${String(id)}`)
      .orderBy(desc(commands.id))
      .limit(10),
  ]);
  return { now, experiment, summary: parseSummary(experiment.summary), trials, gates, holdout, commands: cmds };
}

// ───────────────────────── commands Web → Engine ─────────────────────────

const idString = (what: string) =>
  z
    .string({ error: `${what} fehlt.` })
    .trim()
    .regex(/^\d{1,18}$/, { error: `Unbekannte ${what}.` });

/** Exactly the lab commands the web app may write. APPROVED_LIVE is deliberately not a valid decision here. */
export const labCommandInput = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("LAB_RUN"),
    kind: z.enum(EXPERIMENT_KINDS, { error: "Unbekannte Experimentart." }),
    strategy: z.enum(STRATEGY_FAMILIES, { error: "Unbekannte Strategiefamilie." }),
  }),
  z.object({ type: z.enum(["LAB_PAUSE", "LAB_RESUME", "LAB_ABORT"]), experimentId: idString("Experiment-Nummer") }),
  z.object({
    type: z.literal("APPROVAL_DECIDE"),
    approvalId: idString("Freigabe-Nummer"),
    decision: z.enum(["REJECTED", "SHADOW"], { error: "Erlaubt sind nur «Ablehnen» und «Als Shadow beobachten». Live-Freigabe erfordert Step-up-Anmeldung und ein Live-Mandat." }),
    note: z.string().trim().max(500, { error: "Notiz: höchstens 500 Zeichen." }).optional().default(""),
  }),
]);
export type LabCommandInput = z.infer<typeof labCommandInput>;

export type IssueResult = { ok: true; commandId: number } | { ok: false; error: string };

const rowsOf = (res: unknown): Record<string, unknown>[] => (Array.isArray(res) ? res : ((res as { rows?: Record<string, unknown>[] }).rows ?? []));

/**
 * Validates and writes one lab command plus its audit event in a single statement (both rows or none), like
 * `issuePaperCommand`. Targets are checked in the same statement: pause/resume/abort only for an experiment that is
 * not finished; decisions only for an approval that is still PENDING.
 */
export async function issueLabCommand(user: string, raw: unknown): Promise<IssueResult> {
  const parsed = labCommandInput.safeParse(raw);
  if (!parsed.success) return { ok: false, error: parsed.error.issues[0]?.message ?? "Ungültige Eingabe." };
  const input = parsed.data;
  const actor = `user:${user}`;
  let target: string | null = null;
  let params: Record<string, unknown> = {};
  let check = sql`true`;
  let missing = "Ungültige Eingabe.";
  if (input.type === "LAB_RUN") {
    params = { kind: input.kind, strategy: input.strategy };
  } else if (input.type === "APPROVAL_DECIDE") {
    target = input.approvalId;
    params = { decision: input.decision, note: input.note };
    check = sql`exists (select 1 from "approval" a where a."id" = ${target}::bigint and a."decision" = 'PENDING')`;
    missing = "Unbekannter oder bereits entschiedener Freigabevorschlag. Es wurde kein Befehl gespeichert.";
  } else {
    target = input.experimentId;
    check = sql`exists (select 1 from "experiment" e where e."id" = ${target}::bigint and e."status" not in ('DONE', 'ABORTED'))`;
    missing = "Unbekanntes oder bereits abgeschlossenes Experiment. Es wurde kein Befehl gespeichert.";
  }
  const json = JSON.stringify(params);
  const res = await db().execute(sql`
    with c as (
      insert into "command" ("type", "target", "params", "issued_by")
      select ${input.type}::text, ${target}::text, ${json}::jsonb, ${actor}::text
      where ${check}
      returning "id", "type", "target"
    )
    insert into "audit_event" ("actor", "kind", "object", "data")
    select ${actor}::text, 'COMMAND_ISSUED', 'command:' || c."id",
           jsonb_build_object('type', c."type", 'commandId', c."id", 'target', c."target", 'params', ${json}::jsonb)
    from c
    returning ("data"->>'commandId')::int as "commandId"
  `);
  const id = rowsOf(res)[0]?.commandId;
  if (typeof id !== "number") return { ok: false, error: missing };
  return { ok: true, commandId: id };
}
