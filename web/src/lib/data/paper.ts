import "server-only";
import { and, asc, count, desc, eq, inArray, sql } from "drizzle-orm";
import { z } from "zod";
import { db } from "@/db/client";
import { accounts, autopilots, commands, episodes, equitySnapshots, fills, instruments, orders, reservations, signalOutcomes, trades } from "@/db/schema";
import { parseStartCash } from "@/lib/amount";
import { PAPER_COMMANDS, WORKING_ORDER_STATES } from "@/lib/paper";

/**
 * Paper accounts (simulated trading with virtual capital). Everything here is filtered to `account.mode = 'PAPER'`;
 * figures of different accounts are never added up, and nothing is ever combined with LIVE.
 * Money columns stay strings; sums and quotients are computed by Postgres (`numeric`), never in JavaScript floats.
 */

export type CommandRow = typeof commands.$inferSelect;
export type EpisodeRow = typeof episodes.$inferSelect;

export interface PaperAccountSummary {
  id: string;
  name: string;
  currency: string;
  createdAt: Date;
  /** Autopilot state; null when the engine has not stored one. */
  state: string | null;
  reason: string | null;
  stateAt: Date | null;
  /** Current episode (not ended); falls back to the newest one. */
  episode: EpisodeRow | null;
  /** Equity of the latest `equity_snapshot` of the episode; null when none exists yet (show cash instead). */
  equity: string | null;
  equityAt: Date | null;
  openPositions: number;
  workingOrders: number;
  workingEntryOrders: number;
}

const currentEpisode = (rows: EpisodeRow[]): EpisodeRow | null => rows.find((e) => e.endedAt === null) ?? rows.at(-1) ?? null;

async function latestSnapshots(episodeIds: number[]) {
  if (!episodeIds.length) return [];
  return db()
    .selectDistinctOn([equitySnapshots.episodeId], { episodeId: equitySnapshots.episodeId, ts: equitySnapshots.ts, equity: equitySnapshots.equity, cash: equitySnapshots.cash, invested: equitySnapshots.invested })
    .from(equitySnapshots)
    .where(inArray(equitySnapshots.episodeId, episodeIds))
    .orderBy(equitySnapshots.episodeId, desc(equitySnapshots.ts));
}

/** All paper accounts with autopilot state and the key figures of their current episode. */
export async function listPaperAccounts(): Promise<PaperAccountSummary[]> {
  const rows = await db()
    .select({ id: accounts.id, name: accounts.name, currency: accounts.currency, createdAt: accounts.createdAt, state: autopilots.state, reason: autopilots.reason, stateAt: autopilots.updatedAt })
    .from(accounts)
    .leftJoin(autopilots, eq(autopilots.accountId, accounts.id))
    .where(eq(accounts.mode, "PAPER"))
    .orderBy(asc(accounts.createdAt), asc(accounts.id));
  if (!rows.length) return [];

  const ids = rows.map((r) => r.id);
  const allEpisodes = await db().select().from(episodes).where(inArray(episodes.accountId, ids)).orderBy(asc(episodes.number));
  const current = new Map(ids.map((id) => [id, currentEpisode(allEpisodes.filter((e) => e.accountId === id))]));
  const episodeIds = [...current.values()].flatMap((e) => (e ? [e.id] : []));

  const [snaps, open, working] = await Promise.all([
    latestSnapshots(episodeIds),
    episodeIds.length
      ? db().select({ episodeId: trades.episodeId, n: count() }).from(trades).where(and(inArray(trades.episodeId, episodeIds), eq(trades.status, "OPEN"))).groupBy(trades.episodeId)
      : [],
    episodeIds.length
      ? db()
          .select({ episodeId: orders.episodeId, role: orders.role, n: count() })
          .from(orders)
          .where(and(inArray(orders.episodeId, episodeIds), inArray(orders.state, WORKING_ORDER_STATES)))
          .groupBy(orders.episodeId, orders.role)
      : [],
  ]);

  return rows.map((r) => {
    const episode = current.get(r.id) ?? null;
    const snap = episode ? snaps.find((s) => s.episodeId === episode.id) : undefined;
    const mine = working.filter((w) => w.episodeId === episode?.id);
    return {
      ...r,
      episode,
      equity: snap?.equity ?? null,
      equityAt: snap?.ts ?? null,
      openPositions: open.find((o) => o.episodeId === episode?.id)?.n ?? 0,
      workingOrders: mine.reduce((n, w) => n + w.n, 0),
      workingEntryOrders: mine.filter((w) => w.role === "ENTRY").reduce((n, w) => n + w.n, 0),
    };
  });
}

export interface OpenPosition {
  id: string;
  accountId: string;
  instrumentId: string;
  quoteCurrency: string | null;
  strategyVersionId: string;
  timeframe: string;
  qty: string;
  entryValue: string;
  /** entry_value / qty, computed by Postgres; null when qty is 0. */
  avgEntry: string | null;
  currentStop: string;
  plannedRisk: string;
  barsHeld: number;
  openedAt: Date;
}

async function openPositions(episodeIds: number[]): Promise<(OpenPosition & { episodeId: number })[]> {
  if (!episodeIds.length) return [];
  return db()
    .select({
      id: trades.id,
      episodeId: trades.episodeId,
      accountId: trades.accountId,
      instrumentId: trades.instrumentId,
      quoteCurrency: instruments.quoteCurrency,
      strategyVersionId: trades.strategyVersionId,
      timeframe: trades.timeframe,
      qty: trades.qty,
      entryValue: trades.entryValue,
      avgEntry: sql<string | null>`round(${trades.entryValue} / nullif(${trades.qty}, 0), 10)::text`,
      currentStop: trades.currentStop,
      plannedRisk: trades.plannedRisk,
      barsHeld: trades.barsHeld,
      openedAt: trades.openedAt,
    })
    .from(trades)
    .leftJoin(instruments, eq(instruments.id, trades.instrumentId))
    .where(and(inArray(trades.episodeId, episodeIds), eq(trades.status, "OPEN")))
    .orderBy(asc(trades.openedAt), asc(trades.id));
}

/** Open positions of the current episode of every paper account, grouped per account (no totals across accounts). */
export async function listPaperPositions(): Promise<{ account: PaperAccountSummary; positions: OpenPosition[] }[]> {
  const accs = await listPaperAccounts();
  const positions = await openPositions(accs.flatMap((a) => (a.episode ? [a.episode.id] : [])));
  return accs.map((account) => ({ account, positions: positions.filter((p) => p.episodeId === account.episode?.id) }));
}

export interface WorkingOrder {
  id: string;
  instrumentId: string;
  quoteCurrency: string | null;
  role: string;
  type: string;
  side: string;
  qty: string;
  /** Sum of the fills of this order (the filled quantity is never a stored field). */
  filledQty: string;
  limitPrice: string | null;
  stopPrice: string | null;
  state: string;
  createdAt: Date;
  validUntil: Date | null;
}

export interface ClosedTrade {
  id: string;
  instrumentId: string;
  strategyVersionId: string;
  openedAt: Date;
  closedAt: Date | null;
  qty: string;
  entryValue: string;
  exitValue: string | null;
  /** entry_fees + exit_fees */
  fees: string;
  net: string | null;
  exitReason: string | null;
}

export interface FillRow {
  id: string;
  time: Date;
  instrumentId: string;
  quoteCurrency: string | null;
  role: string;
  side: string;
  qty: string;
  price: string;
  fee: string;
  feeCurrency: string;
  simulated: boolean;
}

export interface EquityPoint {
  /** UTC seconds */
  time: number;
  /** number ONLY for drawing the curve — no calculations on it */
  equity: number;
}

export interface PaperAccountDetail {
  account: PaperAccountSummary;
  episodes: EpisodeRow[];
  /** The selected episode (search param or current). */
  episode: EpisodeRow;
  /** True when the selected episode is the running one (controls and "open" data refer to it). */
  isCurrent: boolean;
  kpi: {
    startCash: string;
    cash: string;
    /** latest snapshot; null → no snapshot yet */
    equity: string | null;
    equityAt: Date | null;
    /** latest snapshot; fallback: sum of entry values of open trades */
    invested: string;
    /** sum of reservation.cash */
    reserved: string;
    /** sum of planned_risk of OPEN trades */
    openRisk: string;
    realized: string;
    feesPaid: string;
    closedTrades: number;
    /** closed trades with net > 0 */
    closedWithGain: number;
  };
  equityCurve: EquityPoint[];
  positions: OpenPosition[];
  orders: WorkingOrder[];
  closed: ClosedTrade[];
  fills: FillRow[];
}

const CURVE_LIMIT = 3000;

/** One paper account with one episode (default: the current one). Null when the account does not exist or is not PAPER. */
export async function loadPaperAccount(id: string, episodeNumber?: number): Promise<PaperAccountDetail | null> {
  const account = (await listPaperAccounts()).find((a) => a.id === id);
  if (!account || !account.episode) return null;
  const all = await db().select().from(episodes).where(eq(episodes.accountId, id)).orderBy(asc(episodes.number));
  const episode = all.find((e) => e.number === episodeNumber) ?? account.episode;
  const ep = episode.id;

  const [[snap], [reserved], [open], [closedAgg], curve, positions, working, closed, fillRows] = await Promise.all([
    latestSnapshots([ep]),
    db().select({ cash: sql<string>`coalesce(sum(${reservations.cash}), 0)::text` }).from(reservations).where(eq(reservations.episodeId, ep)),
    db()
      .select({ risk: sql<string>`coalesce(sum(${trades.plannedRisk}), 0)::text`, entryValue: sql<string>`coalesce(sum(${trades.entryValue}), 0)::text` })
      .from(trades)
      .where(and(eq(trades.episodeId, ep), eq(trades.status, "OPEN"))),
    db()
      .select({ n: count(), gains: sql<number>`(count(*) filter (where ${trades.net} > 0))::int` })
      .from(trades)
      .where(and(eq(trades.episodeId, ep), eq(trades.status, "CLOSED"))),
    db().select({ ts: equitySnapshots.ts, equity: equitySnapshots.equity }).from(equitySnapshots).where(eq(equitySnapshots.episodeId, ep)).orderBy(desc(equitySnapshots.ts)).limit(CURVE_LIMIT),
    openPositions([ep]),
    db()
      .select({
        id: orders.id,
        instrumentId: orders.instrumentId,
        quoteCurrency: instruments.quoteCurrency,
        role: orders.role,
        type: orders.type,
        side: orders.side,
        qty: orders.qty,
        filledQty: sql<string>`(select coalesce(sum(f.qty), 0) from fill f where f.order_id = ${orders.id})::text`,
        limitPrice: orders.limitPrice,
        stopPrice: orders.stopPrice,
        state: orders.state,
        createdAt: orders.createdAt,
        validUntil: orders.validUntil,
      })
      .from(orders)
      .leftJoin(instruments, eq(instruments.id, orders.instrumentId))
      .where(and(eq(orders.episodeId, ep), inArray(orders.state, WORKING_ORDER_STATES)))
      .orderBy(desc(orders.createdAt), asc(orders.id)),
    db()
      .select({
        id: trades.id,
        instrumentId: trades.instrumentId,
        strategyVersionId: trades.strategyVersionId,
        openedAt: trades.openedAt,
        closedAt: trades.closedAt,
        qty: trades.qty,
        entryValue: trades.entryValue,
        exitValue: trades.exitValue,
        fees: sql<string>`(${trades.entryFees} + coalesce(${trades.exitFees}, 0))::text`,
        net: trades.net,
        exitReason: trades.exitReason,
      })
      .from(trades)
      .where(and(eq(trades.episodeId, ep), eq(trades.status, "CLOSED")))
      .orderBy(desc(trades.closedAt), asc(trades.id))
      .limit(100),
    db()
      .select({
        id: fills.id,
        time: fills.time,
        instrumentId: orders.instrumentId,
        quoteCurrency: instruments.quoteCurrency,
        role: orders.role,
        side: orders.side,
        qty: fills.qty,
        price: fills.price,
        fee: fills.fee,
        feeCurrency: fills.feeCurrency,
        simulated: fills.simulated,
      })
      .from(fills)
      .innerJoin(orders, eq(orders.id, fills.orderId))
      .leftJoin(instruments, eq(instruments.id, orders.instrumentId))
      .where(eq(orders.episodeId, ep))
      .orderBy(desc(fills.time), asc(fills.id))
      .limit(50),
  ]);

  return {
    account,
    episodes: all,
    episode,
    isCurrent: episode.id === account.episode.id && episode.endedAt === null,
    kpi: {
      startCash: episode.startCash,
      cash: episode.cash,
      equity: snap?.equity ?? null,
      equityAt: snap?.ts ?? null,
      invested: snap?.invested ?? open?.entryValue ?? "0",
      reserved: reserved?.cash ?? "0",
      openRisk: open?.risk ?? "0",
      realized: episode.realized,
      feesPaid: episode.feesPaid,
      closedTrades: closedAgg?.n ?? 0,
      closedWithGain: closedAgg?.gains ?? 0,
    },
    equityCurve: curve.reverse().map((p) => ({ time: Math.floor(p.ts.getTime() / 1000), equity: Number(p.equity) })),
    positions,
    orders: working,
    closed,
    fills: fillRows,
  };
}

/** Latest PAPER_* commands (newest first), optionally only the pending ones. */
export async function listPaperCommands({ limit = 10, pendingOnly = false }: { limit?: number; pendingOnly?: boolean } = {}): Promise<CommandRow[]> {
  return db()
    .select()
    .from(commands)
    .where(and(inArray(commands.type, [...PAPER_COMMANDS]), pendingOnly ? eq(commands.status, "PENDING") : undefined))
    .orderBy(desc(commands.id))
    .limit(limit);
}

export interface SignalOutcomeRow {
  signalId: string;
  accountId: string;
  accountName: string;
  episodeNumber: number | null;
  /** ORDERED | BLOCKED */
  status: string;
  reasons: string[];
  createdAt: Date;
}

/** What became of the given signals per paper account (`signal_outcome`), keyed by signal id. */
export async function loadSignalOutcomes(signalIds: string[]): Promise<Record<string, SignalOutcomeRow[]>> {
  if (!signalIds.length) return {};
  const rows = await db()
    .select({
      signalId: signalOutcomes.signalId,
      accountId: signalOutcomes.accountId,
      accountName: accounts.name,
      episodeNumber: episodes.number,
      status: signalOutcomes.status,
      reasons: signalOutcomes.reasons,
      createdAt: signalOutcomes.createdAt,
    })
    .from(signalOutcomes)
    .innerJoin(accounts, and(eq(accounts.id, signalOutcomes.accountId), eq(accounts.mode, "PAPER")))
    .leftJoin(episodes, eq(episodes.id, signalOutcomes.episodeId))
    .where(inArray(signalOutcomes.signalId, signalIds))
    .orderBy(asc(accounts.name), asc(signalOutcomes.accountId), asc(signalOutcomes.episodeId));
  const out: Record<string, SignalOutcomeRow[]> = {};
  for (const r of rows) (out[r.signalId] ??= []).push(r);
  return out;
}

// ───────────────────────── Commands Web → Engine ─────────────────────────

const startCash = z
  .string({ error: "Startkapital fehlt." })
  .refine((v) => parseStartCash(v) !== null, { error: "Startkapital muss grösser als 0 und höchstens 100’000’000 USD sein (höchstens zwei Nachkommastellen)." })
  .transform((v) => parseStartCash(v)!);
const accountId = z.string({ error: "Konto fehlt." }).min(1, { error: "Konto fehlt." }).max(200, { error: "Unbekanntes Konto." });

/** Exactly the PAPER_* commands the engine understands — nothing else can be written through this module. */
export const paperCommandInput = z.discriminatedUnion("type", [
  z.object({ type: z.literal("PAPER_CREATE"), name: z.string({ error: "Name fehlt." }).trim().min(1, { error: "Name fehlt." }).max(60, { error: "Name: höchstens 60 Zeichen." }), startCash }),
  z.object({ type: z.enum(["PAPER_START", "PAPER_PAUSE", "PAPER_STOP", "PAPER_CLOSE_ALL"]), accountId }),
  z.object({ type: z.literal("PAPER_RESET"), accountId, startCash }),
]);
export type PaperCommandInput = z.infer<typeof paperCommandInput>;

export type IssueResult = { ok: true; commandId: number } | { ok: false; error: string };

const rowsOf = (res: unknown): Record<string, unknown>[] => (Array.isArray(res) ? res : ((res as { rows?: Record<string, unknown>[] }).rows ?? []));

/**
 * Validates the input and writes one PAPER_* command plus its audit event in a single statement (both rows or none;
 * the Neon HTTP driver has no interactive transactions). Commands for an account are only written when that account
 * exists with mode PAPER — checked inside the same statement. The engine picks the row up within about a minute.
 */
export async function issuePaperCommand(user: string, raw: unknown): Promise<IssueResult> {
  const parsed = paperCommandInput.safeParse(raw);
  if (!parsed.success) return { ok: false, error: parsed.error.issues[0]?.message ?? "Ungültige Eingabe." };
  const input = parsed.data;
  const actor = `user:${user}`;
  const target = input.type === "PAPER_CREATE" ? null : input.accountId;
  const params = input.type === "PAPER_CREATE" ? { name: input.name, start_cash: input.startCash } : input.type === "PAPER_RESET" ? { start_cash: input.startCash } : {};
  const json = JSON.stringify(params);

  const res = await db().execute(sql`
    with c as (
      insert into "command" ("type", "target", "params", "issued_by")
      select ${input.type}::text, ${target}::text, ${json}::jsonb, ${actor}::text
      where ${target}::text is null or exists (select 1 from "account" a where a."id" = ${target}::text and a."mode" = 'PAPER')
      returning "id", "type", "target"
    )
    insert into "audit_event" ("actor", "kind", "object", "data")
    select ${actor}::text, 'COMMAND_ISSUED', 'command:' || c."id",
           jsonb_build_object('type', c."type", 'commandId', c."id", 'target', c."target", 'params', ${json}::jsonb)
    from c
    returning ("data"->>'commandId')::int as "commandId"
  `);
  const id = rowsOf(res)[0]?.commandId;
  if (typeof id !== "number") return { ok: false, error: "Unbekanntes Konto oder kein Paper-Konto. Es wurde kein Befehl gespeichert." };
  return { ok: true, commandId: id };
}
