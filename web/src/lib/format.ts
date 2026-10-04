const ZURICH = "Europe/Zurich";

export function date(d: string | Date | null | undefined, withTime = false): string {
  if (!d) return "—";
  const x = new Date(d);
  if (Number.isNaN(x.getTime())) return "—";
  return x.toLocaleString("de-CH", { timeZone: ZURICH, day: "2-digit", month: "2-digit", year: "numeric", ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}) });
}

/** Date and time in Europe/Zurich (timestamps are stored in UTC). */
export const dateTime = (d: string | Date | null | undefined) => date(d, true);

/** Relative age ("vor 40 s", "vor 5 min", "vor 3 h", "vor 2 Tagen"). */
export function ago(d: string | Date | null | undefined, now = Date.now()): string {
  if (!d) return "—";
  const ms = now - new Date(d).getTime();
  if (Number.isNaN(ms)) return "—";
  if (ms < 5_000) return "gerade eben";
  if (ms < 60_000) return `vor ${Math.floor(ms / 1000)} s`;
  if (ms < 3_600_000) return `vor ${Math.floor(ms / 60_000)} min`;
  if (ms < 86_400_000) return `vor ${Math.floor(ms / 3_600_000)} h`;
  const days = Math.floor(ms / 86_400_000);
  return days === 1 ? "vor 1 Tag" : `vor ${days} Tagen`;
}

/**
 * Formats a `numeric` column (arrives as string, e.g. "64250.5000000000") for display — pure string work,
 * no float math on prices or money. Trailing zeros are trimmed down to `minDecimals`, thousands are grouped.
 */
export function decimal(value: string | null | undefined, minDecimals = 2): string {
  if (value === null || value === undefined || value === "") return "—";
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!m) return value;
  const [, sign, int, frac = ""] = m;
  let f = frac.replace(/0+$/, "");
  if (f.length < minDecimals) f = f.padEnd(minDecimals, "0");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, "’");
  return `${sign}${grouped}${f ? `.${f}` : ""}`;
}

/** Price with its quote currency, e.g. "64’250.50 USD". */
export function price(value: string | null | undefined, currency?: string | null): string {
  const d = decimal(value);
  return d === "—" || !currency ? d : `${d} ${currency}`;
}

export function int(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("de-CH");
}
