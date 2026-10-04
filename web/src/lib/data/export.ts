import "server-only";
import { and, asc, eq, sql, type AnyColumn, type SQL } from "drizzle-orm";
import { db } from "@/db/client";
import { accounts, episodes, fills, instruments, orders, trades } from "@/db/schema";
import { convert, pickRate, tradeInChf, MISSING_RATE } from "@/lib/fx";
import { dec, str, sum } from "@/lib/stats";
import { loadRates } from "./journal";

/**
 * CSV exports for the own evaluation (docs/02, screen 9): orders, fills, trades, fees, fx, year.
 * Format: `;` separator, UTF-8 with BOM (Excel), CRLF, decimal point `.`, timestamps ISO 8601 UTC plus a
 * Europe/Zurich column. Every row carries the mode (PAPER/LIVE), so a file never mixes modes without saying so.
 */

export const EXPORT_KINDS = ["orders", "fills", "trades", "fees", "fx", "year"] as const;
export type ExportKind = (typeof EXPORT_KINDS)[number];
export const isExportKind = (k: string): k is ExportKind => (EXPORT_KINDS as readonly string[]).includes(k);

export const TAX_NOTE = "Grundlage für die eigene Auswertung – keine Steuerberatung; Steuerbarkeit hängt von persönlichen Umständen ab";

export interface ExportFilter {
  account?: string;
  /** episode number of the account (only together with account) */
  episode?: number;
  /** calendar year in Europe/Zurich */
  year?: number;
}

export function parseExportFilter(sp: URLSearchParams): ExportFilter {
  const account = sp.get("account")?.trim() || undefined;
  const ep = Number(sp.get("episode"));
  const year = Number(sp.get("year"));
  return {
    account: account && account.length <= 200 ? account : undefined,
    episode: account && Number.isInteger(ep) && ep > 0 ? ep : undefined,
    year: Number.isInteger(year) && year >= 2000 && year <= 2100 ? year : undefined,
  };
}

type Cell = string | number | boolean | null | undefined | Date;

const NUMERIC = /^-?\d+(\.\d+)?$/;

/** One CSV field: ISO dates, numbers as is; text quoted when needed; formula-like text neutralised for spreadsheets. */
export function csvCell(v: Cell): string {
  if (v === null || v === undefined) return "";
  if (v instanceof Date) return v.toISOString();
  if (typeof v === "boolean") return v ? "true" : "false";
  let s = String(v);
  if (typeof v === "string" && !NUMERIC.test(s) && /^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[;"\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export const BOM = "﻿";

/** Complete file: BOM, optional note line, header, rows; CRLF line ends. */
export function toCsv(header: string[], rows: Cell[][], note?: string): string {
  const lines = [...(note ? [csvCell(note)] : []), header.map(csvCell).join(";"), ...rows.map((r) => r.map(csvCell).join(";"))];
  return `${BOM}${lines.join("\r\n")}\r\n`;
}

const zurich = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Zurich", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
/** "2026-10-04 14:00:00" in Europe/Zurich. */
export const zurichTime = (d: Date | null | undefined): string => (d ? zurich.format(d) : "");
const both = (d: Date | null | undefined): Cell[] => [d ?? null, zurichTime(d)];

/** Conditions of a filter against a table with account_id / episode_id and a time column. */
async function scope(f: ExportFilter, accountCol: AnyColumn, episodeCol: AnyColumn, timeCol: AnyColumn | SQL): Promise<SQL[] | null> {
  const c: SQL[] = [];
  if (f.account) {
    c.push(sql`${accountCol} = ${f.account}`);
    if (f.episode) {
      const [ep] = await db().select({ id: episodes.id }).from(episodes).where(and(eq(episodes.accountId, f.account), eq(episodes.number, f.episode)));
      if (!ep) return null;
      c.push(sql`${episodeCol} = ${ep.id}`);
    }
  }
  if (f.year) c.push(sql`extract(year from (${timeCol} at time zone 'Europe/Zurich')) = ${f.year}`);
  return c;
}

export interface ExportFile {
  filename: string;
  csv: string;
  rows: number;
}

function filename(kind: ExportKind, f: ExportFilter): string {
  const parts = ["tradingengine", kind, f.account?.replace(/[^A-Za-z0-9_-]+/g, "_"), f.episode ? `ep${f.episode}` : undefined, f.year ? String(f.year) : undefined].filter(Boolean);
  return `${parts.join("-")}.csv`;
}

async function exportOrders(f: ExportFilter): Promise<[string[], Cell[][]]> {
  const c = await scope(f, orders.accountId, orders.episodeId, orders.createdAt);
  const header = ["mode", "account_id", "episode", "order_id", "trade_id", "instrument_id", "strategy_version_id", "role", "type", "side", "qty", "limit_price", "stop_price", "state", "reason", "created_utc", "created_zurich", "valid_until_utc", "updated_utc"];
  if (!c) return [header, []];
  const rows = await db()
    .select({ o: orders, episode: episodes.number })
    .from(orders)
    .leftJoin(episodes, eq(episodes.id, orders.episodeId))
    .where(and(...c))
    .orderBy(asc(orders.createdAt), asc(orders.id));
  return [header, rows.map(({ o, episode }) => [o.mode, o.accountId, episode, o.id, o.tradeId, o.instrumentId, o.strategyVersionId, o.role, o.type, o.side, o.qty, o.limitPrice, o.stopPrice, o.state, o.reason, ...both(o.createdAt), o.validUntil, o.updatedAt])];
}

async function fillRows(f: ExportFilter) {
  const c = await scope(f, orders.accountId, orders.episodeId, fills.time);
  if (!c) return [];
  return db()
    .select({
      mode: orders.mode,
      accountId: orders.accountId,
      episode: episodes.number,
      id: fills.id,
      orderId: fills.orderId,
      tradeId: orders.tradeId,
      instrumentId: orders.instrumentId,
      role: orders.role,
      side: orders.side,
      qty: fills.qty,
      price: fills.price,
      value: sql<string>`round(${fills.qty} * ${fills.price}, 10)::text`,
      fee: fills.fee,
      feeCurrency: fills.feeCurrency,
      time: fills.time,
      simulated: fills.simulated,
    })
    .from(fills)
    .innerJoin(orders, eq(orders.id, fills.orderId))
    .leftJoin(episodes, eq(episodes.id, orders.episodeId))
    .where(and(...c))
    .orderBy(asc(fills.time), asc(fills.id));
}

async function exportFills(f: ExportFilter): Promise<[string[], Cell[][]]> {
  const rows = await fillRows(f);
  return [
    ["mode", "account_id", "episode", "fill_id", "order_id", "trade_id", "instrument_id", "role", "side", "qty", "price", "value", "fee", "fee_currency", "time_utc", "time_zurich", "simulated"],
    rows.map((r) => [r.mode, r.accountId, r.episode, r.id, r.orderId, r.tradeId, r.instrumentId, r.role, r.side, r.qty, r.price, r.value, r.fee, r.feeCurrency, ...both(r.time), r.simulated]),
  ];
}

async function exportFees(f: ExportFilter): Promise<[string[], Cell[][]]> {
  const rows = await fillRows(f);
  const times = rows.map((r) => r.time.getTime());
  const rates = rows.length ? await loadRates([...new Set(rows.map((r) => r.feeCurrency))], new Date(Math.min(...times)), new Date(Math.max(...times))) : [];
  return [
    ["mode", "account_id", "episode", "fill_id", "trade_id", "instrument_id", "role", "time_utc", "time_zurich", "fee", "fee_currency", "fx_rate_chf", "fx_date", "fx_source", "fee_chf", "simulated"],
    rows.map((r) => {
      const rate = pickRate(rates, r.feeCurrency, r.time);
      return [r.mode, r.accountId, r.episode, r.id, r.tradeId, r.instrumentId, r.role, ...both(r.time), r.fee, r.feeCurrency, rate?.rate, rate?.date, rate?.source ?? MISSING_RATE, rate ? convert(r.fee, rate.rate) : null, r.simulated];
    }),
  ];
}

async function closedTrades(f: ExportFilter, onlyClosed: boolean) {
  const c = await scope(f, trades.accountId, trades.episodeId, sql`coalesce(${trades.closedAt}, ${trades.openedAt})`);
  if (!c) return { rows: [], rates: [] };
  if (onlyClosed) c.push(eq(trades.status, "CLOSED"));
  const rows = await db()
    .select({
      mode: accounts.mode,
      accountId: trades.accountId,
      accountName: accounts.name,
      accountCurrency: accounts.currency,
      episode: episodes.number,
      id: trades.id,
      instrumentId: trades.instrumentId,
      currency: sql<string>`coalesce(${instruments.quoteCurrency}, ${accounts.currency})`,
      strategyVersionId: trades.strategyVersionId,
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
      exitReason: trades.exitReason,
    })
    .from(trades)
    .innerJoin(accounts, eq(accounts.id, trades.accountId))
    .leftJoin(episodes, eq(episodes.id, trades.episodeId))
    .leftJoin(instruments, eq(instruments.id, trades.instrumentId))
    .where(and(...c))
    .orderBy(asc(accounts.mode), asc(trades.accountId), asc(trades.openedAt), asc(trades.id));
  const times = rows.flatMap((r) => [r.openedAt.getTime(), ...(r.closedAt ? [r.closedAt.getTime()] : [])]);
  const rates = rows.length ? await loadRates([...new Set(rows.map((r) => r.currency))], new Date(Math.min(...times)), new Date(Math.max(...times))) : [];
  return { rows, rates };
}

async function exportTrades(f: ExportFilter): Promise<[string[], Cell[][]]> {
  const { rows, rates } = await closedTrades(f, false);
  return [
    [
      "mode", "account_id", "episode", "trade_id", "instrument_id", "currency", "strategy_version_id", "status", "opened_utc", "opened_zurich", "closed_utc", "closed_zurich",
      "qty", "entry_value", "entry_fees", "exit_value", "exit_fees", "fees", "net", "planned_risk", "exit_reason",
      "fx_entry_chf", "fx_entry_date", "fx_exit_chf", "fx_exit_date", "fx_source", "net_chf", "trading_chf", "fx_effect_chf",
    ],
    rows.map((r) => {
      const chf = r.status === "CLOSED" ? tradeInChf(r, rates) : null;
      const source = chf ? (chf.split ? [...new Set([chf.entry?.source, chf.exit?.source])].join(" / ") : MISSING_RATE) : null;
      return [
        r.mode, r.accountId, r.episode, r.id, r.instrumentId, r.currency, r.strategyVersionId, r.status, ...both(r.openedAt), ...both(r.closedAt),
        r.qty, r.entryValue, r.entryFees, r.exitValue, r.exitFees, r.fees, r.net, r.plannedRisk, r.exitReason,
        chf?.entry?.rate, chf?.entry?.date, chf?.exit?.rate, chf?.exit?.date, source, chf?.split?.total, chf?.split?.trading, chf?.split?.fxEffect,
      ];
    }),
  ];
}

async function exportFx(f: ExportFilter): Promise<[string[], Cell[][]]> {
  const { rows, rates } = await closedTrades(f, true);
  const out: Cell[][] = [];
  for (const r of rows) {
    for (const [leg, at] of [["ENTRY", r.openedAt], ["EXIT", r.closedAt]] as const) {
      if (!at) continue;
      const rate = pickRate(rates, r.currency, at);
      out.push([r.mode, r.accountId, r.episode, r.id, leg, ...both(at), r.currency, "CHF", rate?.rate, rate?.date, rate?.source ?? MISSING_RATE, rate ? rate.previous : null]);
    }
  }
  return [["mode", "account_id", "episode", "trade_id", "leg", "time_utc", "time_zurich", "base", "quote", "rate", "fixing_date", "source", "previous_fixing"], out];
}

async function exportYear(f: ExportFilter): Promise<[string[], Cell[][]]> {
  const { rows, rates } = await closedTrades(f, true);
  const groups = new Map<string, typeof rows>();
  for (const r of rows) {
    const year = zurichTime(r.closedAt).slice(0, 4);
    const key = `${r.mode}|${r.accountId}|${r.currency}|${year}`;
    groups.set(key, [...(groups.get(key) ?? []), r]);
  }
  const out: Cell[][] = [];
  for (const [key, list] of groups) {
    const [mode, accountId, currency, year] = key.split("|");
    const chf = list.map((r) => tradeInChf(r, rates).split);
    const missing = chf.filter((x) => !x).length;
    out.push([
      mode, accountId, list[0].accountName, year, currency, list.length,
      str(sum(list.map((r) => dec(r.net ?? "0")))), str(sum(list.map((r) => dec(r.fees)))),
      missing ? null : str(sum(chf.map((x) => dec(x!.total)))), missing,
    ]);
  }
  return [["mode", "account_id", "account_name", "year", "currency", "closed_trades", "net_realized", "fees", "net_realized_chf", "trades_without_fx_rate"], out];
}

/** Builds one export file. The year overview carries the tax note as its first line. */
export async function buildExport(kind: ExportKind, f: ExportFilter): Promise<ExportFile> {
  const [header, rows] =
    kind === "orders" ? await exportOrders(f) : kind === "fills" ? await exportFills(f) : kind === "fees" ? await exportFees(f) : kind === "trades" ? await exportTrades(f) : kind === "fx" ? await exportFx(f) : await exportYear(f);
  return { filename: filename(kind, f), csv: toCsv(header, rows, kind === "year" ? TAX_NOTE : undefined), rows: rows.length };
}
