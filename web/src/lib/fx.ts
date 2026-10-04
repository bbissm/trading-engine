import { add, dec, mul, str, sub } from "./stats";

/**
 * Conversion of trade results into the reporting currency CHF (docs/01: Berichtswährung CHF).
 *
 * Rates come from `fx_rate` (daily fixings, base = trade currency, quote = CHF, rate = CHF per unit). The rate of a
 * timestamp is the latest fixing whose date (Europe/Zurich calendar day) is on or before that day — weekends and
 * holidays therefore use the previous fixing. A fixing older than FX_MAX_AGE_DAYS counts as missing («Kurs fehlt»)
 * rather than silently using an outdated rate.
 *
 * The split into trading result and currency effect mirrors `engine/src/tradingengine/core/pnl.py::to_reporting`:
 *   total     = (exit_value − exit_fees) × fx_exit − (entry_value + entry_fees) × fx_entry
 *   fx_effect = entry_value × (fx_exit − fx_entry)
 *   trading   = total − fx_effect
 */

export const REPORTING_CURRENCY = "CHF";
export const FX_MAX_AGE_DAYS = 7;
export const MISSING_RATE = "Kurs fehlt";

export interface FxRate {
  base: string;
  quote: string;
  /** YYYY-MM-DD */
  date: string;
  rate: string;
  source: string;
}

export interface AppliedRate {
  rate: string;
  /** fixing date actually used (YYYY-MM-DD) */
  date: string;
  source: string;
  /** true when the fixing is from an earlier day than the timestamp (weekend, holiday, gap) */
  previous: boolean;
}

const zurichDay = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Zurich", year: "numeric", month: "2-digit", day: "2-digit" });

/** Calendar day of a timestamp in Europe/Zurich, YYYY-MM-DD. */
export const fixingDay = (t: Date): string => zurichDay.format(t);

const dayNumber = (d: string) => Math.floor(Date.parse(`${d}T00:00:00Z`) / 86_400_000);

/**
 * Rate for one timestamp. `rates` may be unsorted and contain other pairs. Same currency → rate 1.
 * Returns null when no fixing within FX_MAX_AGE_DAYS on or before the day exists.
 */
export function pickRate(rates: FxRate[], currency: string, at: Date, quote = REPORTING_CURRENCY): AppliedRate | null {
  const day = fixingDay(at);
  if (currency === quote) return { rate: "1", date: day, source: "identische Währung", previous: false };
  let best: FxRate | null = null;
  for (const r of rates) {
    if (r.base !== currency || r.quote !== quote || r.date > day) continue;
    if (!best || r.date > best.date) best = r;
  }
  if (!best || dayNumber(day) - dayNumber(best.date) > FX_MAX_AGE_DAYS) return null;
  return { rate: best.rate, date: best.date, source: best.source, previous: best.date !== day };
}

export interface ReportingInput {
  entryValue: string;
  exitValue: string;
  entryFees: string;
  exitFees: string;
  fxEntry: string;
  fxExit: string;
}

export interface ReportingSplit {
  /** total result in reporting currency; total = trading + fxEffect exactly */
  total: string;
  trading: string;
  fxEffect: string;
}

/** Exact decimal mirror of `to_reporting` (engine). Results keep 10 decimals before display rounding. */
export function reportingSplit(i: ReportingInput): ReportingSplit {
  const fxEntry = dec(i.fxEntry);
  const fxExit = dec(i.fxExit);
  const total = sub(mul(sub(dec(i.exitValue), dec(i.exitFees)), fxExit), mul(add(dec(i.entryValue), dec(i.entryFees)), fxEntry));
  const fxEffect = mul(dec(i.entryValue), sub(fxExit, fxEntry));
  return { total: str(total), trading: str(sub(total, fxEffect)), fxEffect: str(fxEffect) };
}

/** Converts a single amount (e.g. a fee) with one rate. */
export const convert = (amount: string, rate: string): string => str(mul(dec(amount), dec(rate)));

export interface TradeChf {
  entry: AppliedRate | null;
  exit: AppliedRate | null;
  /** null when a rate is missing */
  split: ReportingSplit | null;
}

/** CHF view of one closed trade: entry rate at opening, exit rate at closing. */
export function tradeInChf(
  t: { currency: string; openedAt: Date; closedAt: Date | null; entryValue: string; exitValue: string | null; entryFees: string; exitFees: string | null },
  rates: FxRate[],
): TradeChf {
  const entry = pickRate(rates, t.currency, t.openedAt);
  const exit = t.closedAt ? pickRate(rates, t.currency, t.closedAt) : null;
  const split =
    entry && exit && t.exitValue !== null
      ? reportingSplit({ entryValue: t.entryValue, exitValue: t.exitValue, entryFees: t.entryFees, exitFees: t.exitFees ?? "0", fxEntry: entry.rate, fxExit: exit.rate })
      : null;
  return { entry, exit, split };
}
