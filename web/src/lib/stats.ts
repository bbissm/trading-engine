/**
 * Exact decimal arithmetic and trade statistics for the journal (docs/02, screen 9).
 *
 * Money and prices arrive from Postgres as `numeric` strings. Where a figure has to be derived in TypeScript
 * (per-trade R multiples, profit factor, bootstrap), it is computed with scaled BigInt integers — never with
 * floats — and returned as a decimal string. Sums over table rows that need no per-row logic stay in SQL.
 */

const SCALE_DIGITS = 20;
const SCALE = 10n ** BigInt(SCALE_DIGITS);

/** Fixed-point decimal with 20 fractional digits. */
export type Dec = bigint;

const DEC_RE = /^(-?)(\d+)(?:\.(\d+))?$/;

/** Parses a decimal string ("-12.5", "64250.5000000000"). Digits beyond 20 decimals are truncated (inputs have ≤ 10). */
export function dec(value: string | number | bigint): Dec {
  if (typeof value === "bigint") return value * SCALE;
  const s = typeof value === "number" ? String(value) : value.trim();
  const m = DEC_RE.exec(s);
  if (!m) throw new Error(`Keine Dezimalzahl: ${s}`);
  const [, sign, int, frac = ""] = m;
  const v = BigInt(int) * SCALE + BigInt(frac.slice(0, SCALE_DIGITS).padEnd(SCALE_DIGITS, "0"));
  return sign ? -v : v;
}

/** Null-safe parse. */
export const decOrNull = (value: string | null | undefined): Dec | null => (value === null || value === undefined || value === "" ? null : dec(value));

/** Integer division rounded half away from zero. */
function divRound(a: bigint, b: bigint): bigint {
  if (b === 0n) throw new Error("Division durch null");
  const neg = a < 0n !== b < 0n;
  const aa = a < 0n ? -a : a;
  const bb = b < 0n ? -b : b;
  let q = aa / bb;
  if ((aa % bb) * 2n >= bb) q += 1n;
  return neg ? -q : q;
}

export const add = (...xs: Dec[]): Dec => xs.reduce((s, x) => s + x, 0n);
export const sub = (a: Dec, b: Dec): Dec => a - b;
export const mul = (a: Dec, b: Dec): Dec => divRound(a * b, SCALE);
/** a ÷ b; null when b is zero. */
export const div = (a: Dec, b: Dec): Dec | null => (b === 0n ? null : divRound(a * SCALE, b));
export const abs = (a: Dec): Dec => (a < 0n ? -a : a);
export const ZERO: Dec = 0n;
export const ONE: Dec = SCALE;

/** Decimal string rounded to `digits` decimals (half away from zero), trailing zeros kept: toFixed(dec("1.005"), 2) = "1.01". */
export function toFixed(value: Dec, digits = 10): string {
  const unit = 10n ** BigInt(SCALE_DIGITS - digits);
  const r = divRound(value, unit);
  const neg = r < 0n;
  const a = neg ? -r : r;
  const p = 10n ** BigInt(digits);
  const int = (a / p).toString();
  const frac = digits ? `.${(a % p).toString().padStart(digits, "0")}` : "";
  return `${neg && r !== 0n ? "-" : ""}${int}${frac}`;
}

/** Decimal string with at most 10 decimals, trailing zeros removed ("12.5", "-3"). */
export function str(value: Dec, digits = 10): string {
  const s = toFixed(value, digits);
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}

export const sum = (xs: Dec[]): Dec => xs.reduce((s, x) => s + x, 0n);
export const mean = (xs: Dec[]): Dec | null => (xs.length ? divRound(sum(xs), BigInt(xs.length)) : null);

// ───────────────────────── trade statistics ─────────────────────────

export interface TradeStatInput {
  /** net result in trade currency, after fees (only closed trades) */
  net: string;
  /** planned risk at entry (stop distance × qty + costs); 0 or missing → no R multiple */
  plannedRisk: string;
}

/** Profit factor = sum of gains ÷ |sum of losses|. Null when there is no losing trade (undefined, not «infinite»). */
export function profitFactor(nets: string[]): string | null {
  const xs = nets.map(dec);
  const gains = sum(xs.filter((x) => x > 0n));
  const losses = abs(sum(xs.filter((x) => x < 0n)));
  const q = div(gains, losses);
  return q === null ? null : str(q, 4);
}

/** R multiple of one trade: net ÷ planned risk. Null when the planned risk is not positive. */
export function rMultiple(t: TradeStatInput): Dec | null {
  const risk = dec(t.plannedRisk);
  return risk > 0n ? div(dec(t.net), risk) : null;
}

/** Expectancy in R = mean of net ÷ planned risk over all trades with a positive planned risk. */
export function expectancyR(trades: TradeStatInput[]): string | null {
  const rs = trades.map(rMultiple).filter((r): r is Dec => r !== null);
  const m = mean(rs);
  return m === null ? null : str(m, 4);
}

/** Minimum number of trades before an uncertainty interval is shown at all. */
export const MIN_TRADES_FOR_CI = 10;
export const TOO_FEW_TRADES = "zu wenig Trades für eine Unsicherheitsangabe";
export const BOOTSTRAP_SEED = 20261004;
export const BOOTSTRAP_RESAMPLES = 2000;

/** Small deterministic PRNG (mulberry32): same seed → same sequence on every machine. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export interface BootstrapResult {
  /** lower and upper bound as decimal strings (4 decimals) */
  ci: [string, string];
  level: number;
  resamples: number;
  seed: number;
  n: number;
}

/**
 * Percentile bootstrap interval of the mean (i.i.d. resampling of trades, fixed seed → reproducible).
 * Returns null below MIN_TRADES_FOR_CI values: the UI then shows TOO_FEW_TRADES instead of a number.
 * The PRNG only picks indices; the means themselves are exact decimals.
 */
export function bootstrapMeanCI(values: Dec[], { seed = BOOTSTRAP_SEED, resamples = BOOTSTRAP_RESAMPLES, level = 0.95 } = {}): BootstrapResult | null {
  const n = values.length;
  if (n < MIN_TRADES_FOR_CI) return null;
  const rand = mulberry32(seed);
  const means: bigint[] = new Array(resamples);
  for (let b = 0; b < resamples; b++) {
    let s = 0n;
    for (let i = 0; i < n; i++) s += values[Math.floor(rand() * n)];
    means[b] = divRound(s, BigInt(n));
  }
  means.sort((x, y) => (x < y ? -1 : x > y ? 1 : 0));
  const alpha = (1 - level) / 2;
  const lo = means[Math.floor(alpha * (resamples - 1))];
  const hi = means[Math.ceil((1 - alpha) * (resamples - 1))];
  return { ci: [toFixed(lo, 4), toFixed(hi, 4)], level, resamples, seed, n };
}

/** Bootstrap interval of the expectancy in R for a set of closed trades (null below the minimum sample). */
export function expectancyRCI(trades: TradeStatInput[], opts?: { seed?: number; resamples?: number; level?: number }): BootstrapResult | null {
  return bootstrapMeanCI(
    trades.map(rMultiple).filter((r): r is Dec => r !== null),
    opts,
  );
}

/** Sample-size caveat shown next to every ratio. */
export function sampleCaveat(n: number): string {
  if (n === 0) return "Keine abgeschlossenen Trades – keine Aussage möglich.";
  if (n < MIN_TRADES_FOR_CI) return `Nur ${n} abgeschlossene Trades – Kennzahlen sind Zufallsschwankungen stark ausgesetzt und belegen keinen Vorteil.`;
  if (n < 100) return `${n} abgeschlossene Trades – noch weit unter den ≥ 100 unabhängigen Fällen, die G1 verlangt. Keine statistische Aussage über einen Vorteil.`;
  return `${n} abgeschlossene Trades. Trades werden als unabhängig behandelt (keine Cluster-Bildung); das Intervall ist eher zu eng.`;
}
