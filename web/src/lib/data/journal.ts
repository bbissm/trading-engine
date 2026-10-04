import "server-only";
import { and, asc, desc, eq, gte, inArray, lte, or, sql, type SQL } from "drizzle-orm";
import { db } from "@/db/client";
import { accounts, candles, episodes, fills, fxRates, instruments, orders, signalOutcomes, signals, trades } from "@/db/schema";
import type { ChartCandle, ChartMarker } from "@/lib/data/instrument";
import { tradeInChf, type FxRate, type TradeChf, FX_MAX_AGE_DAYS, REPORTING_CURRENCY } from "@/lib/fx";
import { abs, add, dec, decOrNull, div, expectancyR, expectancyRCI, mean, mul, profitFactor, str, sub, sum, ZERO, ONE, type BootstrapResult, type Dec } from "@/lib/stats";

/**
 * Journal & performance (docs/02, screen 9): every trade with its decision chain, plan vs. execution and costs,
 * key figures with uncertainty and baselines. One selection always covers exactly one account and one episode —
 * figures of different accounts or modes are never added up. Money columns stay strings; derived figures use exact
 * decimals (`lib/stats.ts`), equity-curve figures are computed by Postgres.
 */

export type EpisodeRow = typeof episodes.$inferSelect;

export interface JournalFilter {
  account?: string;
  episode?: number;
  instrument?: string;
  strategy?: string;
  status: "ALL" | "OPEN" | "CLOSED";
  /** YYYY-MM-DD, Europe/Zurich calendar days, inclusive */
  from?: string;
  to?: string;
}

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const text = (v: unknown, max = 200) => (typeof v === "string" && v.trim() && v.length <= max ? v.trim() : undefined);
const day = (v: unknown) => (typeof v === "string" && DAY_RE.test(v) && !Number.isNaN(Date.parse(`${v}T00:00:00Z`)) ? v : undefined);

/** Search params → filter; anything malformed is dropped (no error page for a hand-edited URL). */
export function parseJournalFilter(sp: Record<string, string | string[] | undefined>): JournalFilter {
  const one = (k: string) => (Array.isArray(sp[k]) ? sp[k][0] : sp[k]);
  const ep = Number(one("episode"));
  const status = one("status");
  return {
    account: text(one("account")),
    episode: Number.isInteger(ep) && ep > 0 ? ep : undefined,
    instrument: text(one("instrument")),
    strategy: text(one("strategy")),
    status: status === "OPEN" || status === "CLOSED" ? status : "ALL",
    from: day(one("from")),
    to: day(one("to")),
  };
}

/** Start of a Zurich calendar day as SQL timestamptz. */
const zurichStart = (d: string) => sql`(${d}::date::timestamp at time zone 'Europe/Zurich')`;
const zurichEnd = (d: string) => sql`((${d}::date + 1)::timestamp at time zone 'Europe/Zurich')`;

const rowsOf = (res: unknown): Record<string, unknown>[] => (Array.isArray(res) ? res : ((res as { rows?: Record<string, unknown>[] }).rows ?? []));
const asDate = (v: unknown): Date | null => (v === null || v === undefined ? null : v instanceof Date ? v : new Date(String(v)));
const asText = (v: unknown): string | null => (v === null || v === undefined ? null : String(v));

export interface AccountOption {
  id: string;
  name: string;
  /** PAPER | LIVE */
  mode: string;
  currency: string;
  episodes: EpisodeRow[];
}

export interface JournalTrade {
  id: string;
  instrumentId: string;
  /** trade currency (quote currency of the instrument) */
  currency: string;
  strategyVersionId: string;
  timeframe: string;
  status: string;
  openedAt: Date;
  closedAt: Date | null;
  qty: string;
  entryValue: string;
  entryFees: string;
  exitValue: string | null;
  exitFees: string | null;
  /** entry_fees + exit_fees (Postgres) */
  fees: string;
  net: string | null;
  plannedRisk: string;
  plannedStop: string;
  currentStop: string;
  avgEntry: string | null;
  avgExit: string | null;
  exitReason: string | null;
  /** net ÷ planned risk (exact decimals); null for open trades */
  r: string | null;
  chf: TradeChf;
}

export interface JournalKpis {
  closed: number;
  open: number;
  net: string;
  fees: string;
  gross: string;
  /** sum of CHF totals over trades with both rates; `chfMissing` trades are not included */
  netChf: string | null;
  chfMissing: number;
  avgWin: string | null;
  avgLoss: string | null;
  wins: number;
  losses: number;
  profitFactor: string | null;
  expectancyR: string | null;
  expectancyCI: BootstrapResult | null;
  /** traded volume (entry + exit value) */
  volume: string;
  /** fees ÷ traded volume */
  feeShareOfVolume: string | null;
  /** fees ÷ |gross|; null when gross is 0 */
  feeShareOfGross: string | null;
  /** average holding time in hours (closed trades) */
  avgHoldHours: number | null;
}

export interface EquityKpis {
  snapshots: number;
  firstAt: Date | null;
  lastAt: Date | null;
  /** base of the return: first snapshot in the period, or the episode's start capital without a period */
  baseEquity: string;
  lastEquity: string | null;
  /** simple return = time-weighted return (no deposits/withdrawals exist for paper episodes) */
  return: string | null;
  maxDrawdown: string | null;
  /** share of valuations with invested > 0 */
  exposure: string | null;
  avgEquity: string | null;
  /** volume ÷ average equity */
  turnover: string | null;
  dailyReturns: number;
  /** annualised with √365 (crypto trades every day); null below MIN_DAILY_RETURNS */
  sharpe: string | null;
}

export const MIN_DAILY_RETURNS = 30;

export interface Baselines {
  capital: string;
  start: Date;
  end: Date;
  timeframe: string;
  /** effective fee rate of the selection's fills: Σ fee ÷ Σ qty × price */
  feeRate: string | null;
  /** «Buy-and-Hold» of every traded instrument, capital split equally, same fee rate on buy and sell */
  buyHold: { net: string | null; return: string | null; legs: { instrumentId: string; p0: string | null; p1: string | null; p0At: Date | null; p1At: Date | null; net: string | null }[]; missing: string[] };
  /** net of closed trades of the selection */
  strategyNet: string;
  /** change of equity over the period (incl. open positions); null without valuations */
  strategyEquityChange: string | null;
}

export interface JournalData {
  now: number;
  accounts: AccountOption[];
  account: AccountOption | null;
  episode: EpisodeRow | null;
  filter: JournalFilter;
  instruments: string[];
  strategies: string[];
  trades: JournalTrade[];
  /** more trades exist than shown */
  truncated: boolean;
  kpis: JournalKpis;
  equity: EquityKpis | null;
  /** instrument/strategy filter active: equity figures refer to the whole account, not to the filter */
  equityScopeDiffers: boolean;
  baselines: Baselines | null;
  fxSources: string[];
}

const TRADE_LIMIT = 500;

async function loadAccounts(): Promise<AccountOption[]> {
  const [accs, eps] = await Promise.all([
    db().select({ id: accounts.id, name: accounts.name, mode: accounts.mode, currency: accounts.currency }).from(accounts).orderBy(asc(accounts.mode), asc(accounts.createdAt), asc(accounts.id)),
    db().select().from(episodes).orderBy(asc(episodes.number)),
  ]);
  // PAPER before LIVE in the selector; never mixed in one selection
  return accs.sort((a, b) => (a.mode === b.mode ? 0 : a.mode === "PAPER" ? -1 : 1)).map((a) => ({ ...a, episodes: eps.filter((e) => e.accountId === a.id) }));
}

/** fx_rate rows for the given currencies covering [from − max age, to]. */
export async function loadRates(currencies: string[], from: Date, to: Date): Promise<FxRate[]> {
  const bases = [...new Set(currencies.filter((c) => c !== REPORTING_CURRENCY))];
  if (!bases.length) return [];
  const lo = new Date(from.getTime() - (FX_MAX_AGE_DAYS + 2) * 86_400_000).toISOString().slice(0, 10);
  const hi = new Date(to.getTime() + 2 * 86_400_000).toISOString().slice(0, 10);
  return db()
    .select({ base: fxRates.base, quote: fxRates.quote, date: fxRates.date, rate: fxRates.rate, source: fxRates.source })
    .from(fxRates)
    .where(and(inArray(fxRates.base, bases), eq(fxRates.quote, REPORTING_CURRENCY), gte(fxRates.date, lo), lte(fxRates.date, hi)))
    .orderBy(asc(fxRates.date));
}

const tradeColumns = {
  id: trades.id,
  instrumentId: trades.instrumentId,
  quoteCurrency: instruments.quoteCurrency,
  strategyVersionId: trades.strategyVersionId,
  timeframe: trades.timeframe,
  status: trades.status,
  openedAt: trades.openedAt,
  closedAt: trades.closedAt,
  qty: trades.qty,
  entryValue: trades.entryValue,
  entryFees: trades.entryFees,
  exitValue: trades.exitValue,
  exitFees: trades.exitFees,
  fees: sql<string>`(${trades.entryFees} + coalesce(${trades.exitFees}, 0))::text`,
  net: trades.net,
  plannedRisk: trades.plannedRisk,
  plannedStop: trades.plannedStop,
  currentStop: trades.currentStop,
  avgEntry: sql<string | null>`round(${trades.entryValue} / nullif(${trades.qty}, 0), 10)::text`,
  avgExit: sql<string | null>`round(${trades.exitValue} / nullif(${trades.qty}, 0), 10)::text`,
  exitReason: trades.exitReason,
  signalId: trades.signalId,
  managedThrough: trades.managedThrough,
  barsHeld: trades.barsHeld,
  exitPlan: trades.exitPlan,
  accountId: trades.accountId,
  episodeId: trades.episodeId,
};
const tradeQuery = () => db().select(tradeColumns).from(trades).leftJoin(instruments, eq(instruments.id, trades.instrumentId));
type TradeDbRow = Awaited<ReturnType<typeof tradeQuery>>[number];

function toJournalTrade(t: TradeDbRow, fallbackCurrency: string, rates: FxRate[]): JournalTrade {
  const currency = t.quoteCurrency ?? fallbackCurrency;
  const risk = dec(t.plannedRisk);
  const r = t.net !== null && risk > 0n ? div(dec(t.net), risk) : null;
  return {
    id: t.id,
    instrumentId: t.instrumentId,
    currency,
    strategyVersionId: t.strategyVersionId,
    timeframe: t.timeframe,
    status: t.status,
    openedAt: t.openedAt,
    closedAt: t.closedAt,
    qty: t.qty,
    entryValue: t.entryValue,
    entryFees: t.entryFees,
    exitValue: t.exitValue,
    exitFees: t.exitFees,
    fees: t.fees,
    net: t.net,
    plannedRisk: t.plannedRisk,
    plannedStop: t.plannedStop,
    currentStop: t.currentStop,
    avgEntry: t.avgEntry,
    avgExit: t.avgExit,
    exitReason: t.exitReason,
    r: r === null ? null : str(r, 4),
    chf: t.status === "CLOSED" ? tradeInChf({ ...t, currency }, rates) : { entry: null, exit: null, split: null },
  };
}

/** Trade KPIs of a selection (exact decimals). Hit rate is deliberately not a headline figure. */
export function tradeKpis(rows: JournalTrade[]): JournalKpis {
  const closed = rows.filter((t) => t.status === "CLOSED" && t.net !== null);
  const nets = closed.map((t) => dec(t.net!));
  const fees = sum(closed.map((t) => dec(t.fees)));
  const net = sum(nets);
  const gross = add(net, fees);
  const winsV = nets.filter((x) => x > 0n);
  const lossV = nets.filter((x) => x < 0n);
  const volume = sum(closed.map((t) => add(dec(t.entryValue), dec(t.exitValue ?? "0"))));
  const withChf = closed.filter((t) => t.chf.split);
  const statInput = closed.map((t) => ({ net: t.net!, plannedRisk: t.plannedRisk }));
  const hold = closed.filter((t) => t.closedAt).map((t) => (t.closedAt!.getTime() - t.openedAt.getTime()) / 3_600_000);
  const m = (xs: Dec[]) => {
    const v = mean(xs);
    return v === null ? null : str(v, 2);
  };
  const q = (a: Dec, b: Dec, digits = 6) => {
    const v = div(a, b);
    return v === null ? null : str(v, digits);
  };
  return {
    closed: closed.length,
    open: rows.filter((t) => t.status === "OPEN").length,
    net: str(net),
    fees: str(fees),
    gross: str(gross),
    netChf: withChf.length ? str(sum(withChf.map((t) => dec(t.chf.split!.total)))) : null,
    chfMissing: closed.length - withChf.length,
    avgWin: m(winsV),
    avgLoss: m(lossV),
    wins: winsV.length,
    losses: lossV.length,
    profitFactor: closed.length ? profitFactor(closed.map((t) => t.net!)) : null,
    expectancyR: expectancyR(statInput),
    expectancyCI: expectancyRCI(statInput),
    volume: str(volume),
    feeShareOfVolume: q(fees, volume),
    feeShareOfGross: gross === ZERO ? null : q(fees, abs(gross)),
    avgHoldHours: hold.length ? hold.reduce((s, h) => s + h, 0) / hold.length : null,
  };
}

async function equityKpis(episode: EpisodeRow, filter: JournalFilter, volume: string): Promise<EquityKpis> {
  const from = filter.from ?? null;
  const to = filter.to ?? null;
  const res = await db().execute(sql`
    with s as (
      select "ts", "equity", "invested" from "equity_snapshot"
      where "episode_id" = ${episode.id}
        and (${from}::text is null or "ts" >= (${from}::date::timestamp at time zone 'Europe/Zurich'))
        and (${to}::text is null or "ts" < ((${to}::date + 1)::timestamp at time zone 'Europe/Zurich'))
    ),
    dd as (select "equity", max("equity") over (order by "ts") as peak from s),
    daily as (
      select distinct on (("ts" at time zone 'Europe/Zurich')::date) ("ts" at time zone 'Europe/Zurich')::date as d, "equity"
      from s order by ("ts" at time zone 'Europe/Zurich')::date, "ts" desc
    ),
    r as (select "equity" / nullif(lag("equity") over (order by d), 0) - 1 as ret from daily)
    select
      (select count(*) from s)::int as n,
      (select min("ts") from s) as first_at,
      (select max("ts") from s) as last_at,
      (select "equity"::text from s order by "ts" asc limit 1) as first_equity,
      (select "equity"::text from s order by "ts" desc limit 1) as last_equity,
      (select round(max((peak - "equity") / nullif(peak, 0)), 6)::text from dd) as max_dd,
      (select round(avg("equity"), 10)::text from s) as avg_equity,
      (select round((count(*) filter (where "invested" > 0))::numeric / nullif(count(*), 0), 6)::text from s) as exposure,
      (select count(ret)::int from r) as n_ret,
      (select round(avg(ret) / nullif(stddev_samp(ret), 0) * sqrt(365::numeric), 4)::text from r) as sharpe
  `);
  const row = rowsOf(res)[0] ?? {};
  const n = Number(row.n ?? 0);
  const base = from && row.first_equity ? String(row.first_equity) : episode.startCash;
  const last = asText(row.last_equity);
  const nRet = Number(row.n_ret ?? 0);
  const ret = last ? div(sub(dec(last), dec(base)), dec(base)) : null;
  const avgEquity = asText(row.avg_equity);
  const turnover = avgEquity ? div(dec(volume), dec(avgEquity)) : null;
  return {
    snapshots: n,
    firstAt: asDate(row.first_at),
    lastAt: asDate(row.last_at),
    baseEquity: base,
    lastEquity: last,
    return: ret === null ? null : str(ret, 6),
    maxDrawdown: asText(row.max_dd),
    exposure: asText(row.exposure),
    avgEquity,
    turnover: turnover === null ? null : str(turnover, 4),
    dailyReturns: nRet,
    sharpe: nRet >= MIN_DAILY_RETURNS ? asText(row.sharpe) : null,
  };
}

/** Buy-and-Hold net for one instrument: capital buys at p0 incl. fee, sells at p1 minus fee (exact decimals). */
export function buyHoldNet(capital: string, p0: string, p1: string, feeRate: string): string {
  const f = dec(feeRate);
  const qty = div(dec(capital), mul(dec(p0), add(ONE, f)));
  if (qty === null) return "0";
  return str(sub(mul(mul(qty, dec(p1)), sub(ONE, f)), dec(capital)));
}

async function baselines(episode: EpisodeRow, filter: JournalFilter, rows: JournalTrade[], kpis: JournalKpis, equity: EquityKpis): Promise<Baselines | null> {
  const instrumentIds = [...new Set(rows.map((t) => t.instrumentId))].sort();
  const start = filter.from ? new Date(`${filter.from}T00:00:00Z`) : episode.startedAt;
  const end = filter.to ? new Date(Date.parse(`${filter.to}T00:00:00Z`) + 86_400_000) : (episode.endedAt ?? episode.simThrough);
  const timeframe = rows[0]?.timeframe ?? "4h";
  const tradeIds = rows.map((t) => t.id);
  const [feeRow] = tradeIds.length
    ? await db()
        .select({ rate: sql<string | null>`round(sum(${fills.fee}) / nullif(sum(${fills.qty} * ${fills.price}), 0), 10)::text` })
        .from(fills)
        .innerJoin(orders, eq(orders.id, fills.orderId))
        .where(inArray(orders.tradeId, tradeIds))
    : [{ rate: null }];
  const feeRate = feeRow?.rate ?? null;
  const capital = equity.baseEquity;

  const legs = await Promise.all(
    instrumentIds.map(async (id) => {
      const [first] = await db()
        .select({ close: candles.close, at: candles.closeTime })
        .from(candles)
        .where(and(eq(candles.instrumentId, id), eq(candles.timeframe, timeframe), gte(candles.closeTime, start)))
        .orderBy(asc(candles.closeTime))
        .limit(1);
      const [last] = await db()
        .select({ close: candles.close, at: candles.closeTime })
        .from(candles)
        .where(and(eq(candles.instrumentId, id), eq(candles.timeframe, timeframe), lte(candles.closeTime, end)))
        .orderBy(desc(candles.closeTime))
        .limit(1);
      const ok = first && last && feeRate !== null && first.at < last.at;
      const share = div(dec(capital), dec(BigInt(instrumentIds.length)));
      return {
        instrumentId: id,
        p0: first?.close ?? null,
        p1: last?.close ?? null,
        p0At: first?.at ?? null,
        p1At: last?.at ?? null,
        net: ok && share !== null ? buyHoldNet(str(share), first.close, last.close, feeRate!) : null,
      };
    }),
  );
  const missing = legs.filter((l) => l.net === null).map((l) => l.instrumentId);
  const total = legs.length && !missing.length ? sum(legs.map((l) => dec(l.net!))) : null;
  const ret = total !== null ? div(total, dec(capital)) : null;
  return {
    capital,
    start,
    end,
    timeframe,
    feeRate,
    buyHold: { net: total === null ? null : str(total), return: ret === null ? null : str(ret, 6), legs, missing },
    strategyNet: kpis.net,
    strategyEquityChange: equity.lastEquity ? str(sub(dec(equity.lastEquity), dec(equity.baseEquity))) : null,
  };
}

function selectionConditions(episode: EpisodeRow, f: JournalFilter): SQL[] {
  const c: SQL[] = [eq(trades.episodeId, episode.id), eq(trades.accountId, episode.accountId)];
  if (f.instrument) c.push(eq(trades.instrumentId, f.instrument));
  if (f.strategy) c.push(eq(trades.strategyVersionId, f.strategy));
  if (f.status !== "ALL") c.push(eq(trades.status, f.status));
  if (f.from) c.push(sql`coalesce(${trades.closedAt}, ${trades.openedAt}) >= ${zurichStart(f.from)}`);
  if (f.to) c.push(sql`${trades.openedAt} < ${zurichEnd(f.to)}`);
  return c;
}

const EMPTY_KPIS = tradeKpis([]);

/** Journal of one account/episode (default: first paper account, its current episode). */
export async function loadJournal(filter: JournalFilter, now = Date.now()): Promise<JournalData> {
  const accs = await loadAccounts();
  const account = accs.find((a) => a.id === filter.account) ?? accs[0] ?? null;
  const episode = account ? (account.episodes.find((e) => e.number === filter.episode) ?? account.episodes.find((e) => e.endedAt === null) ?? account.episodes.at(-1) ?? null) : null;
  const base: JournalData = {
    now,
    accounts: accs,
    account,
    episode,
    filter,
    instruments: [],
    strategies: [],
    trades: [],
    truncated: false,
    kpis: EMPTY_KPIS,
    equity: null,
    equityScopeDiffers: !!(filter.instrument || filter.strategy),
    baselines: null,
    fxSources: [],
  };
  if (!account || !episode) return base;

  const [options, raw] = await Promise.all([
    db()
      .selectDistinct({ instrumentId: trades.instrumentId, strategyVersionId: trades.strategyVersionId })
      .from(trades)
      .where(eq(trades.episodeId, episode.id)),
    tradeQuery()
      .where(and(...selectionConditions(episode, filter)))
      .orderBy(desc(sql`coalesce(${trades.closedAt}, ${trades.openedAt})`), asc(trades.id))
      .limit(TRADE_LIMIT + 1),
  ]);
  const rows = raw.slice(0, TRADE_LIMIT);
  const times = rows.flatMap((t) => [t.openedAt, ...(t.closedAt ? [t.closedAt] : [])]);
  const rates = times.length
    ? await loadRates(
        rows.map((t) => t.quoteCurrency ?? account.currency),
        new Date(Math.min(...times.map((t) => t.getTime()))),
        new Date(Math.max(...times.map((t) => t.getTime()))),
      )
    : [];
  const list = rows.map((t) => toJournalTrade(t, account.currency, rates));
  const kpis = tradeKpis(list);
  const equity = await equityKpis(episode, filter, kpis.volume);
  return {
    ...base,
    instruments: [...new Set(options.map((o) => o.instrumentId))].sort(),
    strategies: [...new Set(options.map((o) => o.strategyVersionId))].sort(),
    trades: list,
    truncated: raw.length > TRADE_LIMIT,
    kpis,
    equity,
    baselines: await baselines(episode, filter, list, kpis, equity),
    fxSources: [...new Set(list.flatMap((t) => [t.chf.entry?.source, t.chf.exit?.source]).filter((s): s is string => !!s))],
  };
}

// ───────────────────────── trade detail ─────────────────────────

export interface DetailOrder {
  id: string;
  role: string;
  type: string;
  side: string;
  qty: string;
  limitPrice: string | null;
  stopPrice: string | null;
  state: string;
  reason: string | null;
  createdAt: Date;
  updatedAt: Date;
  validUntil: Date | null;
  filledQty: string;
  avgFill: string | null;
}

export interface DetailFill {
  id: string;
  orderId: string;
  role: string;
  side: string;
  qty: string;
  price: string;
  fee: string;
  feeCurrency: string;
  time: Date;
  simulated: boolean;
}

export interface ExitClassification {
  key: "MANUAL" | "GAP_SLIPPAGE" | "FEES" | "TARGET" | "TIME" | "REGIME" | "FAILED_BREAKOUT" | "STOP_AS_PLANNED" | "OPEN" | "UNCLASSIFIED";
  label: string;
  /** the rule that matched, shown verbatim */
  rule: string;
}

export const SLIPPAGE_TOLERANCE = "0.1"; // 10 % of the planned risk

export interface ExitAnalysisInput {
  status: string;
  exitReason: string | null;
  net: string | null;
  fees: string;
  plannedRisk: string;
  /** stop at the time of the exit (trade.current_stop) */
  currentStop: string;
  qty: string;
  avgExit: string | null;
}

/**
 * Rule-based classification of the main deviation of a trade (no model, no LLM). Rules are checked in this order;
 * the first match wins and its wording is shown on the page.
 */
export function classifyExit(i: ExitAnalysisInput): ExitClassification {
  if (i.status !== "CLOSED") return { key: "OPEN", label: "Trade noch offen", rule: "Status ist nicht CLOSED – noch keine Ausstiegsanalyse." };
  const reason = i.exitReason ?? "";
  if (/^manuell/i.test(reason)) return { key: "MANUAL", label: "Manuell geschlossen", rule: "Ausstiegsgrund beginnt mit «Manuell» (Befehl «Positionen jetzt schliessen»)." };
  const isStop = /^stop/i.test(reason);
  if (isStop && i.avgExit !== null) {
    const shortfall = mul(sub(dec(i.currentStop), dec(i.avgExit)), dec(i.qty));
    const tolerance = mul(dec(i.plannedRisk), dec(SLIPPAGE_TOLERANCE));
    if (shortfall > tolerance) {
      return { key: "GAP_SLIPPAGE", label: "Kurslücke / Slippage", rule: `Stop-Ausstieg, Ausführung unter dem Stop um mehr als ${Number(SLIPPAGE_TOLERANCE) * 100} % des geplanten Risikos ((Stop − Ø Ausstieg) × Menge > 0.1 × geplantes Risiko).` };
    }
  }
  if (i.net !== null) {
    const net = dec(i.net);
    const gross = add(net, dec(i.fees));
    if (gross > 0n && net <= 0n) return { key: "FEES", label: "Gebühren", rule: "Brutto-Ergebnis > 0, Netto-Ergebnis ≤ 0: die Gebühren haben den Kursgewinn aufgezehrt." };
  }
  if (/^ziel/i.test(reason)) return { key: "TARGET", label: "Ziel erreicht", rule: "Ausstiegsgrund «Ziel»: die Ziel-Limitorder wurde ausgeführt." };
  if (/haltedauer|zeitlimit/i.test(reason)) return { key: "TIME", label: "Zeitlimit", rule: "Ausstiegsgrund nennt die maximale Haltedauer." };
  if (/^regimewechsel/i.test(reason)) return { key: "REGIME", label: "Regimewechsel", rule: "Ausstiegsgrund «Regimewechsel»: das Regime gehört nicht mehr zu den erlaubten Regimen des Exit-Plans." };
  if (/^gescheiterter ausbruch/i.test(reason)) return { key: "FAILED_BREAKOUT", label: "Gescheiterter Ausbruch", rule: "Ausstiegsgrund «Gescheiterter Ausbruch»: Schluss zurück unter das Ausbruchsniveau innert der Frist." };
  if (isStop) return { key: "STOP_AS_PLANNED", label: "Stop wie geplant ausgelöst", rule: "Stop-Ausstieg ohne wesentliche Abweichung (Ausführung höchstens 10 % des geplanten Risikos unter dem Stop)." };
  return { key: "UNCLASSIFIED", label: "Nicht eindeutig zuordenbar", rule: `Keine Regel trifft auf den Ausstiegsgrund «${reason || "—"}» zu.` };
}

export interface PlanVsActual {
  plannedStop: string;
  currentStop: string;
  /** current stop ≥ planned stop (stops are only ever tightened) */
  stopNeverWidened: boolean;
  avgEntry: string | null;
  entryLimit: string | null;
  /** fill price ≤ limit for a buy limit */
  entryWithinLimit: boolean | null;
  avgExit: string | null;
  /** (avg exit − planned stop) × qty; negative = worse than the planned stop */
  vsPlannedStop: string | null;
  /** (avg exit − current stop) × qty */
  vsCurrentStop: string | null;
  plannedRisk: string;
  /** net; for a loss compare with −planned risk */
  realized: string | null;
  /** realised loss ÷ planned risk (in R); null without net */
  realizedR: string | null;
  fees: string;
  feesOfRisk: string | null;
  feesOfGross: string | null;
}

export function planVsActual(t: JournalTrade, entryLimit: string | null): PlanVsActual {
  const qty = dec(t.qty);
  const exit = decOrNull(t.avgExit);
  const fees = dec(t.fees);
  const net = decOrNull(t.net);
  const gross = net === null ? null : add(net, fees);
  const q = (a: Dec, b: Dec | null, digits = 4) => {
    const v = b === null ? null : div(a, b);
    return v === null ? null : str(v, digits);
  };
  return {
    plannedStop: t.plannedStop,
    currentStop: t.currentStop,
    stopNeverWidened: dec(t.currentStop) >= dec(t.plannedStop),
    avgEntry: t.avgEntry,
    entryLimit,
    entryWithinLimit: entryLimit && t.avgEntry ? dec(t.avgEntry) <= dec(entryLimit) : null,
    avgExit: t.avgExit,
    vsPlannedStop: exit === null ? null : str(mul(sub(exit, dec(t.plannedStop)), qty)),
    vsCurrentStop: exit === null ? null : str(mul(sub(exit, dec(t.currentStop)), qty)),
    plannedRisk: t.plannedRisk,
    realized: t.net,
    realizedR: t.r,
    fees: t.fees,
    feesOfRisk: q(fees, dec(t.plannedRisk) > 0n ? dec(t.plannedRisk) : null),
    feesOfGross: gross === null || gross === 0n ? null : q(fees, abs(gross)),
  };
}

export interface TradeDetail {
  now: number;
  account: { id: string; name: string; mode: string; currency: string };
  episodeNumber: number | null;
  trade: JournalTrade;
  exitPlan: Record<string, unknown>;
  barsHeld: number;
  managedThrough: Date;
  signal: (typeof signals.$inferSelect & { quoteCurrency: string | null }) | null;
  outcome: { status: string; reasons: string[]; values: Record<string, string>; createdAt: Date } | null;
  orders: DetailOrder[];
  fills: DetailFill[];
  candles: ChartCandle[];
  markers: ChartMarker[];
  classification: ExitClassification;
  plan: PlanVsActual;
}

const CHART_BARS_BEFORE = 30;
const CHART_BARS_AFTER = 10;
const TF_MS: Record<string, number> = { "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000 };

export async function loadTradeDetail(id: string, now = Date.now()): Promise<TradeDetail | null> {
  const [row] = await db()
    .select({ ...tradeColumns, accountName: accounts.name, mode: accounts.mode, accountCurrency: accounts.currency, episodeNumber: episodes.number })
    .from(trades)
    .innerJoin(accounts, eq(accounts.id, trades.accountId))
    .leftJoin(instruments, eq(instruments.id, trades.instrumentId))
    .leftJoin(episodes, eq(episodes.id, trades.episodeId))
    .where(eq(trades.id, id));
  if (!row) return null;
  const t = row;

  const [signalRows, outcomeRows, orderRows, fillRows] = await Promise.all([
    t.signalId
      ? db()
          .select({ s: signals, quoteCurrency: instruments.quoteCurrency })
          .from(signals)
          .leftJoin(instruments, eq(instruments.id, signals.instrumentId))
          .where(eq(signals.id, t.signalId))
      : [],
    t.signalId
      ? db()
          .select({ status: signalOutcomes.status, reasons: signalOutcomes.reasons, values: signalOutcomes.values, createdAt: signalOutcomes.createdAt })
          .from(signalOutcomes)
          .where(and(eq(signalOutcomes.signalId, t.signalId), eq(signalOutcomes.accountId, t.accountId), eq(signalOutcomes.episodeId, t.episodeId)))
      : [],
    db()
      .select({
        id: orders.id,
        role: orders.role,
        type: orders.type,
        side: orders.side,
        qty: orders.qty,
        limitPrice: orders.limitPrice,
        stopPrice: orders.stopPrice,
        state: orders.state,
        reason: orders.reason,
        createdAt: orders.createdAt,
        updatedAt: orders.updatedAt,
        validUntil: orders.validUntil,
        filledQty: sql<string>`(select coalesce(sum(f.qty), 0) from fill f where f.order_id = "trade_order"."id")::text`,
        avgFill: sql<string | null>`(select round(sum(f.qty * f.price) / nullif(sum(f.qty), 0), 10) from fill f where f.order_id = "trade_order"."id")::text`,
      })
      .from(orders)
      .where(and(eq(orders.accountId, t.accountId), or(eq(orders.tradeId, t.id), t.signalId ? and(eq(orders.signalId, t.signalId), eq(orders.episodeId, t.episodeId), eq(orders.role, "ENTRY")) : undefined)))
      .orderBy(asc(orders.createdAt), asc(orders.id)),
    db()
      .select({ id: fills.id, orderId: fills.orderId, role: orders.role, side: orders.side, qty: fills.qty, price: fills.price, fee: fills.fee, feeCurrency: fills.feeCurrency, time: fills.time, simulated: fills.simulated })
      .from(fills)
      .innerJoin(orders, eq(orders.id, fills.orderId))
      .where(and(eq(orders.accountId, t.accountId), eq(orders.tradeId, t.id)))
      .orderBy(asc(fills.time), asc(fills.id)),
  ]);

  const currency = t.quoteCurrency ?? t.accountCurrency;
  const rates = await loadRates([currency], t.openedAt, t.closedAt ?? t.openedAt);
  const trade = toJournalTrade(t, t.accountCurrency, rates);

  const tf = TF_MS[t.timeframe] ?? TF_MS["4h"];
  const chartFrom = new Date(t.openedAt.getTime() - CHART_BARS_BEFORE * tf);
  const chartTo = new Date((t.closedAt ?? new Date(now)).getTime() + CHART_BARS_AFTER * tf);
  const candleRows = await db()
    .select({ openTime: candles.openTime, closeTime: candles.closeTime, open: candles.open, high: candles.high, low: candles.low, close: candles.close })
    .from(candles)
    .where(and(eq(candles.instrumentId, t.instrumentId), eq(candles.timeframe, t.timeframe), gte(candles.closeTime, chartFrom), lte(candles.openTime, chartTo)))
    .orderBy(asc(candles.openTime))
    .limit(400);
  // a fill at time x belongs to the candle with open ≤ x < close (simulated fills carry the candle's close or open time)
  const barOf = (x: Date) => {
    const c = candleRows.find((r) => r.openTime <= x && x < r.closeTime) ?? candleRows.findLast((r) => r.closeTime <= x);
    return c ? Math.floor(c.openTime.getTime() / 1000) : null;
  };
  const markers: ChartMarker[] = [];
  const entryFill = fillRows.find((f) => f.role === "ENTRY");
  const exitFill = fillRows.findLast((f) => f.role !== "ENTRY");
  const e = entryFill ? barOf(entryFill.time) : null;
  const x = exitFill ? barOf(exitFill.time) : null;
  if (e !== null) markers.push({ time: e, text: "Einstieg" });
  if (x !== null) markers.push({ time: x, text: "Ausstieg" });

  const entryOrder = orderRows.find((o) => o.role === "ENTRY");
  const sig = signalRows[0];
  return {
    now,
    account: { id: t.accountId, name: t.accountName, mode: t.mode, currency: t.accountCurrency },
    episodeNumber: t.episodeNumber,
    trade,
    exitPlan: t.exitPlan ?? {},
    barsHeld: t.barsHeld,
    managedThrough: t.managedThrough,
    signal: sig ? { ...sig.s, quoteCurrency: sig.quoteCurrency } : null,
    outcome: outcomeRows[0] ?? null,
    orders: orderRows,
    fills: fillRows,
    candles: candleRows.map((r) => ({ time: Math.floor(r.openTime.getTime() / 1000), open: Number(r.open), high: Number(r.high), low: Number(r.low), close: Number(r.close) })),
    markers: markers.sort((a, b) => a.time - b.time),
    classification: classifyExit({ status: trade.status, exitReason: trade.exitReason, net: trade.net, fees: trade.fees, plannedRisk: trade.plannedRisk, currentStop: trade.currentStop, qty: trade.qty, avgExit: trade.avgExit }),
    plan: planVsActual(trade, entryOrder?.limitPrice ?? null),
  };
}
