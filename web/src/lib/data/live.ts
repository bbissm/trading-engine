import "server-only";
import { and, asc, desc, eq, inArray, isNotNull, max, sql } from "drizzle-orm";
import { z } from "zod";
import { db } from "@/db/client";
import {
  accounts,
  approvals,
  autopilots,
  channelStatus,
  commands,
  fills,
  fxRates,
  gateEvaluations,
  heartbeats,
  instruments,
  mandates,
  orderApprovals,
  orders,
  reconciliations,
  strategyVersions,
  trades,
} from "@/db/schema";
import { EMERGENCY_POLICIES, LIVE_COMMANDS, STEP_UP_COMMANDS, validateMandate, type LiveFacts, type StrategyFacts } from "@/app/live/model";
import { WORKING_ORDER_STATES } from "@/lib/paper";

/**
 * Live account (real money, Kraken Spot). Everything here is filtered to `account.mode = 'LIVE'`; live figures are
 * never added to paper figures. Kraken credentials are never read or stored by the web app — only the engine's
 * live function has them; the web app shows the stored permission check and the status.
 * Money columns stay strings; nothing is computed with JavaScript floats.
 */

export type LiveCommandRow = typeof commands.$inferSelect;

/** Only the boolean of the flag is read, never any other environment value. */
export const liveFlag = () => process.env.LIVE_TRADING_ENABLED === "true";

const rowsOf = (res: unknown): Record<string, unknown>[] => (Array.isArray(res) ? res : ((res as { rows?: Record<string, unknown>[] }).rows ?? []));

export interface LiveSnapshot {
  balances: Record<string, string>;
  managed: Record<string, string>;
  foreign: Record<string, string>;
  system: string | null;
}

export interface LiveOverview {
  facts: LiveFacts;
  account: { id: string; name: string; createdAt: Date; providerAccountRef: string | null; permissions: Record<string, unknown> | null } | null;
  autopilot: { state: string; reason: string | null; updatedAt: Date } | null;
  mandate: typeof mandates.$inferSelect | null;
  strategies: (StrategyFacts & { lifecycle: string })[];
  instruments: { id: string; name: string }[];
  positions: (typeof trades.$inferSelect & { avgEntry: string | null })[];
  orders: (typeof orders.$inferSelect & { filled: string })[];
  fills: { id: string; orderId: string; instrumentId: string; side: string; role: string; qty: string; price: string; fee: string; feeCurrency: string; time: Date }[];
  reconciliations: (typeof reconciliations.$inferSelect)[];
  snapshot: LiveSnapshot | null;
  pendingApprovals: (typeof orderApprovals.$inferSelect)[];
  commands: LiveCommandRow[];
  /** Einstandswert der verwalteten Positionen und geschätzte Taker-Gebühr 0.80 % (Postgres-numeric, für den Schliessen-Dialog) */
  exposure: { costBasis: string | null; estTakerFee: string | null };
}

async function strategyFacts(): Promise<(StrategyFacts & { lifecycle: string })[]> {
  const versions = await db().select({ id: strategyVersions.id, lifecycle: strategyVersions.lifecycleStatus }).from(strategyVersions).orderBy(asc(strategyVersions.id));
  if (!versions.length) return [];
  const gates = await db()
    .selectDistinctOn([gateEvaluations.strategyVersionId, gateEvaluations.gate], { v: gateEvaluations.strategyVersionId, gate: gateEvaluations.gate, result: gateEvaluations.result })
    .from(gateEvaluations)
    .orderBy(gateEvaluations.strategyVersionId, gateEvaluations.gate, desc(gateEvaluations.evaluatedAt), desc(gateEvaluations.id));
  const decided = await db()
    .selectDistinctOn([approvals.strategyVersionId], { v: approvals.strategyVersionId, decision: approvals.decision })
    .from(approvals)
    .where(isNotNull(approvals.decidedAt))
    .orderBy(approvals.strategyVersionId, desc(approvals.decidedAt), desc(approvals.id));
  return versions.map((v) => ({
    id: v.id,
    lifecycle: v.lifecycle,
    gates: Object.fromEntries(gates.filter((g) => g.v === v.id).map((g) => [g.gate, g.result])),
    approvedLive: decided.find((d) => d.v === v.id)?.decision === "APPROVED_LIVE",
  }));
}

/** Everything the Live-Assistent shows, for the (single) Kraken live account. */
export async function loadLiveOverview(): Promise<LiveOverview> {
  const [acc] = await db().select().from(accounts).where(and(eq(accounts.mode, "LIVE"), eq(accounts.provider, "KRAKEN"))).orderBy(asc(accounts.createdAt)).limit(1);
  const [hb] = await db().select().from(heartbeats).where(eq(heartbeats.service, "live"));
  const [ack] = await db().select({ at: max(channelStatus.lastTestAckAt) }).from(channelStatus);
  const [fx] = await db()
    .select({ d: max(fxRates.date) })
    .from(fxRates)
    .where(sql`(${fxRates.base} = 'USD' and ${fxRates.quote} = 'CHF') or (${fxRates.base} = 'CHF' and ${fxRates.quote} = 'USD')`);
  const strategies = await strategyFacts();
  const universe = await db().select({ id: instruments.id, name: instruments.name }).from(instruments).where(eq(instruments.inUniverse, true)).orderBy(asc(instruments.id));

  const id = acc?.id;
  const [ap] = id ? await db().select().from(autopilots).where(eq(autopilots.accountId, id)) : [];
  const [mandate] = id ? await db().select().from(mandates).where(eq(mandates.accountId, id)).orderBy(desc(mandates.createdAt), desc(mandates.id)).limit(1) : [];
  const recons = id ? await db().select().from(reconciliations).where(eq(reconciliations.accountId, id)).orderBy(desc(reconciliations.at), desc(reconciliations.id)).limit(10) : [];
  const positions = id
    ? await db()
        .select({ t: trades, avgEntry: sql<string | null>`round(${trades.entryValue} / nullif(${trades.qty}, 0), 10)::text` })
        .from(trades)
        .where(and(eq(trades.accountId, id), eq(trades.status, "OPEN")))
        .orderBy(asc(trades.openedAt))
    : [];
  const working = id
    ? await db()
        .select({ o: orders, filled: sql<string>`coalesce((select sum(f.qty) from fill f where f.order_id = ${orders.id}), 0)::text` })
        .from(orders)
        .where(and(eq(orders.accountId, id), eq(orders.mode, "LIVE"), inArray(orders.state, WORKING_ORDER_STATES)))
        .orderBy(desc(orders.createdAt))
    : [];
  const recentFills = id
    ? await db()
        .select({ id: fills.id, orderId: fills.orderId, instrumentId: orders.instrumentId, side: orders.side, role: orders.role, qty: fills.qty, price: fills.price, fee: fills.fee, feeCurrency: fills.feeCurrency, time: fills.time })
        .from(fills)
        .innerJoin(orders, eq(orders.id, fills.orderId))
        .where(and(eq(orders.accountId, id), eq(orders.mode, "LIVE"), eq(fills.simulated, false)))
        .orderBy(desc(fills.time))
        .limit(30)
    : [];
  const pendingApprovals = id ? await db().select().from(orderApprovals).where(and(eq(orderApprovals.accountId, id), eq(orderApprovals.status, "PENDING"))).orderBy(asc(orderApprovals.expiresAt)) : [];
  const [exposure] = id
    ? await db()
        .select({
          costBasis: sql<string | null>`nullif(round(coalesce(sum(${trades.entryValue}), 0), 2), 0)::text`,
          estTakerFee: sql<string | null>`nullif(round(coalesce(sum(${trades.entryValue}), 0) * 0.008, 2), 0)::text`,
        })
        .from(trades)
        .where(and(eq(trades.accountId, id), eq(trades.status, "OPEN")))
    : [{ costBasis: null, estTakerFee: null }];
  const liveCommands = await db().select().from(commands).where(inArray(commands.type, [...LIVE_COMMANDS])).orderBy(desc(commands.id)).limit(20);

  const snap = recons.find((r) => r.diffs.some((d) => d.kind === "SNAPSHOT"))?.diffs.find((d) => d.kind === "SNAPSHOT");
  const snapshot: LiveSnapshot | null = snap
    ? { balances: (snap.balances as Record<string, string>) ?? {}, managed: (snap.managed as Record<string, string>) ?? {}, foreign: (snap.foreign as Record<string, string>) ?? {}, system: typeof snap.system === "string" ? snap.system : null }
    : null;
  const lastRecon = recons[0] ? { status: recons[0].status, at: recons[0].at } : null;
  const unknownOrders = working.filter((w) => w.o.state === "UNKNOWN" || w.o.state === "SUBMITTED").length;

  const facts: LiveFacts = {
    flag: liveFlag(),
    liveSeenAt: hb?.lastSeen ?? null,
    account: acc ? { id: acc.id, permissions: acc.permissions ?? null } : null,
    lastRecon,
    testAckAt: ack?.at ?? null,
    fxDate: fx?.d ?? null,
    strategies,
    mandate: mandate ? { id: mandate.id, status: mandate.status, budget: mandate.budget, autonomyLevel: mandate.autonomyLevel, emergency: String(mandate.policy?.emergency ?? "HOLD_PROTECTED") } : null,
    unknownOrders,
  };
  return {
    facts,
    account: acc ? { id: acc.id, name: acc.name, createdAt: acc.createdAt, providerAccountRef: acc.providerAccountRef, permissions: acc.permissions ?? null } : null,
    autopilot: ap ? { state: ap.state, reason: ap.reason, updatedAt: ap.updatedAt } : null,
    mandate: mandate ?? null,
    strategies,
    instruments: universe,
    positions: positions.map((p) => ({ ...p.t, avgEntry: p.avgEntry })),
    orders: working.map((w) => ({ ...w.o, filled: w.filled })),
    fills: recentFills,
    reconciliations: recons,
    snapshot,
    pendingApprovals,
    commands: liveCommands,
    exposure: exposure ?? { costBasis: null, estTakerFee: null },
  };
}

/** Compact live summary for the autopilot and portfolio pages. */
export async function loadLiveSummary() {
  const o = await loadLiveOverview();
  return { account: o.account, autopilot: o.autopilot, mandate: o.mandate, positions: o.positions, orders: o.orders, snapshot: o.snapshot, lastRecon: o.facts.lastRecon, flag: o.facts.flag };
}

// ───────────────────────── writes (always via `command` or after step-up) ─────────────────────────

export type IssueResult = { ok: true; id: number } | { ok: false; error: string };

const commandInput = z.object({
  type: z.enum(LIVE_COMMANDS),
  approvalId: z.coerce.number().int().positive().optional(),
  decision: z.enum(["APPROVED", "REJECTED"]).optional(),
  withdrawAbsentConfirmed: z.boolean().optional(),
});

/**
 * Writes one live command plus its audit event in one statement. Commands that need step-up carry `step_up_at`,
 * which the engine re-checks (≤ 5 min). Non-register commands are only written for an existing LIVE account.
 */
export async function issueLiveCommand(user: string, raw: unknown, stepUpAt?: Date): Promise<IssueResult> {
  const parsed = commandInput.safeParse(raw);
  if (!parsed.success) return { ok: false, error: parsed.error.issues[0]?.message ?? "Ungültige Eingabe." };
  const input = parsed.data;
  if (STEP_UP_COMMANDS.includes(input.type) && !stepUpAt) return { ok: false, error: "Dieser Befehl verlangt eine Step-up-Anmeldung." };
  if (input.type === "ORDER_APPROVAL_DECIDE" && (!input.approvalId || !input.decision)) return { ok: false, error: "Freigabeanfrage und Entscheid fehlen." };
  if (input.withdrawAbsentConfirmed && !stepUpAt) return { ok: false, error: "Die Bestätigung «kein Auszahlungsrecht» verlangt eine Step-up-Anmeldung." };

  const params: Record<string, unknown> = {};
  if (stepUpAt) params.step_up_at = stepUpAt.toISOString();
  if (input.type === "ORDER_APPROVAL_DECIDE") Object.assign(params, { approval_id: input.approvalId, decision: input.decision });
  if (input.type === "LIVE_ACCOUNT_REGISTER" && input.withdrawAbsentConfirmed) params.withdraw_absent_confirmed = true;
  const target = input.type === "LIVE_ACCOUNT_REGISTER" ? null : "live-kraken";
  const json = JSON.stringify(params);
  const actor = `user:${user}`;
  const res = await db().execute(sql`
    with c as (
      insert into "command" ("type", "target", "params", "issued_by")
      select ${input.type}::text, ${target}::text, ${json}::jsonb, ${actor}::text
      where ${target}::text is null or exists (select 1 from "account" a where a."id" = ${target}::text and a."mode" = 'LIVE')
      returning "id", "type", "target"
    )
    insert into "audit_event" ("actor", "kind", "object", "data")
    select ${actor}::text, 'COMMAND_ISSUED', 'command:' || c."id", jsonb_build_object('type', c."type", 'commandId', c."id", 'target', c."target", 'params', ${json}::jsonb)
    from c
    returning ("data"->>'commandId')::int as "commandId"
  `);
  const id = rowsOf(res)[0]?.commandId;
  if (typeof id !== "number") return { ok: false, error: "Kein Live-Konto verbunden. Es wurde kein Befehl gespeichert." };
  return { ok: true, id };
}

const mandateInput = z.object({
  autonomyLevel: z.coerce.number().int().min(1).max(3),
  strategyIds: z.array(z.string().min(1).max(80)).max(20),
  instrumentIds: z.array(z.string().min(1).max(80)).max(50),
  budget: z.string().trim().max(14),
  budgetConfirm: z.string().trim().max(14),
  emergency: z.enum(Object.keys(EMERGENCY_POLICIES) as [keyof typeof EMERGENCY_POLICIES]),
});

/**
 * Activates a mandate after step-up: re-validates on the server that every chosen version has APPROVED_LIVE and
 * G1–G3 passed, the instruments are in the universe, and the budget was retyped. Writes `mandate` (ACTIVE,
 * `activated_by`, `step_up_at`) plus audit in one statement. The engine re-checks step-up freshness on take-over.
 */
export async function activateMandate(user: string, raw: unknown, stepUpAt: Date): Promise<IssueResult> {
  const parsed = mandateInput.safeParse(raw);
  if (!parsed.success) return { ok: false, error: parsed.error.issues[0]?.message ?? "Ungültige Eingabe." };
  const input = parsed.data;
  const strategies = await strategyFacts();
  const eligible = strategies.filter((s) => s.approvedLive && ["G1", "G2", "G3"].every((g) => s.gates[g] === "PASSED")).map((s) => s.id);
  const error = validateMandate(input, eligible);
  if (error) return { ok: false, error };
  const universe = new Set((await db().select({ id: instruments.id }).from(instruments).where(eq(instruments.inUniverse, true))).map((i) => i.id));
  const unknown = input.instrumentIds.filter((i) => !universe.has(i));
  if (unknown.length) return { ok: false, error: `Nicht im Universum: ${unknown.join(", ")}.` };

  const actor = `user:${user}`;
  const policy = JSON.stringify({ emergency: input.emergency, protection: "STOP_AT_EXCHANGE" });
  const res = await db().execute(sql`
    with m as (
      insert into "mandate" ("account_id", "autonomy_level", "strategy_version_ids", "instrument_ids", "budget", "policy", "status", "activated_at", "activated_by", "step_up_at")
      select a."id", ${input.autonomyLevel}::int, ${JSON.stringify(input.strategyIds)}::jsonb, ${JSON.stringify(input.instrumentIds)}::jsonb, ${input.budget}::numeric,
             ${policy}::jsonb, 'ACTIVE', now(), ${actor}::text, ${stepUpAt.toISOString()}::timestamptz
      from "account" a where a."id" = 'live-kraken' and a."mode" = 'LIVE'
      returning "id", "account_id", "budget"
    )
    insert into "audit_event" ("actor", "kind", "object", "data")
    select ${actor}::text, 'MANDATE_ACTIVATED', 'mandate:' || m."id", jsonb_build_object('mandateId', m."id", 'budget', m."budget", 'level', ${input.autonomyLevel}::int, 'stepUpAt', ${stepUpAt.toISOString()}::text)
    from m
    returning ("data"->>'mandateId')::int as "id"
  `);
  const id = rowsOf(res)[0]?.id;
  if (typeof id !== "number") return { ok: false, error: "Kein Live-Konto verbunden. Es wurde kein Mandat gespeichert." };
  return { ok: true, id };
}

/**
 * Live approval decision for a strategy version (docs/02, 3.3), after step-up. APPROVED_LIVE only for versions whose
 * latest G1–G3 evaluations all PASSED. WITHDRAWN (take back) is risk-reducing and needs no step-up.
 */
export async function decideLiveApproval(user: string, strategyVersionId: string, decision: "APPROVED_LIVE" | "WITHDRAWN", stepUpAt?: Date): Promise<IssueResult> {
  const facts = (await strategyFacts()).find((s) => s.id === strategyVersionId);
  if (!facts) return { ok: false, error: "Unbekannte Strategieversion." };
  if (decision === "APPROVED_LIVE") {
    if (!stepUpAt) return { ok: false, error: "Die Live-Freigabe verlangt eine Step-up-Anmeldung." };
    const missing = ["G1", "G2", "G3"].filter((g) => facts.gates[g] !== "PASSED");
    if (missing.length) return { ok: false, error: `Freigabe nicht anwählbar: ${missing.join(", ")} nicht bestanden.` };
  }
  const actor = `user:${user}`;
  const note = stepUpAt ? `step-up ${stepUpAt.toISOString()}` : null;
  const [row] = await db()
    .insert(approvals)
    .values({ strategyVersionId, proposedBy: actor, decision, decidedAt: new Date(), decidedBy: actor, note })
    .returning({ id: approvals.id });
  await db().execute(sql`insert into "audit_event" ("actor", "kind", "object", "data") values (${actor}, 'APPROVAL_DECIDED', ${`approval:${row.id}`}, ${JSON.stringify({ strategyVersionId, decision, stepUpAt: stepUpAt?.toISOString() ?? null })}::jsonb)`);
  return { ok: true, id: row.id };
}
