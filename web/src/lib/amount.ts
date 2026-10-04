/**
 * Money input as a decimal string — never a float. Used for the start capital of paper accounts:
 * the value travels as a string into `command.params` and the engine parses it with Decimal.
 */
export const MAX_START_CASH = "100000000";

const SCALE = 10;

/** "10’000.50" / "10'000,5" / " 10000 " → "10000.50"; null when it is not a plain non-negative decimal number. */
export function normalizeAmount(input: string): string | null {
  const s = input.trim().replace(/[’'\s]/g, "").replace(",", ".");
  const m = /^(\d{1,12})(?:\.(\d{1,10}))?$/.exec(s);
  if (!m) return null;
  const int = m[1].replace(/^0+(?=\d)/, "");
  return m[2] ? `${int}.${m[2]}` : int;
}

/** Decimal string → integer scaled by 10^10 (the scale of the numeric columns). Throws on anything else. */
export function toScaled(value: string): bigint {
  const m = /^(-?)(\d+)(?:\.(\d{1,10}))?$/.exec(value.trim());
  if (!m) throw new Error(`not a decimal: ${value}`);
  const n = BigInt(m[2]) * 10n ** BigInt(SCALE) + BigInt((m[3] ?? "").padEnd(SCALE, "0"));
  return m[1] ? -n : n;
}

/** -1 | 0 | 1 without float conversion. */
export function compareDecimal(a: string, b: string): number {
  const x = toScaled(a);
  const y = toScaled(b);
  return x < y ? -1 : x > y ? 1 : 0;
}

/** Start capital: > 0 and ≤ 100 000 000, at most two decimals. Returns the normalised string or null. */
export function parseStartCash(input: string): string | null {
  const n = normalizeAmount(input);
  if (n === null || !/^\d+(\.\d{1,2})?$/.test(n)) return null;
  if (compareDecimal(n, "0") <= 0 || compareDecimal(n, MAX_START_CASH) > 0) return null;
  return n;
}
