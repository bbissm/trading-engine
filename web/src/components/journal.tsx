import Link from "next/link";
import type { ReactNode } from "react";
import type { AppliedRate, TradeChf } from "@/lib/fx";
import { MISSING_RATE } from "@/lib/fx";
import { decimal, price } from "@/lib/format";
import { dec, mul, str } from "@/lib/stats";
import { ModeBadge, type Mode } from "./mode-badge";
import { Simulated } from "./paper";
import { Badge, type Tone } from "./ui";

export const journalHref = (q: Record<string, string | number | undefined | null>) => {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  const s = sp.toString();
  return `/journal${s ? `?${s}` : ""}`;
};
export const tradeHref = (id: string) => `/journal/${encodeURIComponent(id)}`;
export const exportHref = (kind: string, q: Record<string, string | number | undefined | null>) => {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  const s = sp.toString();
  return `/api/export/${kind}${s ? `?${s}` : ""}`;
};

export const asMode = (m: string): Mode => (m === "LIVE" ? "LIVE" : "PAPER");

/** Mode badge plus «simuliert» for paper figures. */
export function ModeTag({ mode }: { mode: string }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <ModeBadge mode={asMode(mode)} />
      {mode !== "LIVE" && <Simulated />}
    </span>
  );
}

/** Ratio string ("0.006650") → "0.67 %" (string arithmetic). */
export function pct(ratio: string | null | undefined, digits = 2): string {
  if (ratio === null || ratio === undefined || ratio === "") return "—";
  return `${decimal(str(mul(dec(ratio), dec("100")), digits), digits)} %`;
}

/** Signed amount with currency: "+12.50 USD", "−3.10 CHF". */
export function signed(value: string | null | undefined, currency?: string | null): string {
  if (value === null || value === undefined) return "—";
  const v = price(str(dec(value), 2), currency);
  return value.startsWith("-") ? v.replace("-", "−") : dec(value) === 0n ? v : `+${v}`;
}

export const rText = (r: string | null | undefined) => (r === null || r === undefined ? "—" : `${signed(r).replace(/ $/, "")} R`);

export const toneOf = (value: string | null | undefined): Tone | undefined => (!value ? undefined : value.startsWith("-") ? "critical" : dec(value) > 0n ? "good" : undefined);

export function hours(h: number | null): string {
  if (h === null) return "—";
  if (h < 48) return `${h.toLocaleString("de-CH", { maximumFractionDigits: 1 })} h`;
  return `${(h / 24).toLocaleString("de-CH", { maximumFractionDigits: 1 })} Tage`;
}

export const rateText = (r: AppliedRate | null) => (r ? `${decimal(r.rate, 4)} (${r.date}${r.previous ? ", Vortages-Fixing" : ""}, ${r.source})` : MISSING_RATE);

/** Net in CHF with the fixing on hover; «Kurs fehlt» when a rate is missing. */
export function ChfCell({ chf, status }: { chf: TradeChf; status: string }) {
  if (status !== "CLOSED") return <span className="text-ink-2">offen</span>;
  if (!chf.split) {
    return (
      <span title={`Einstieg: ${rateText(chf.entry)} · Ausstieg: ${rateText(chf.exit)}`}>
        <Badge tone="warning">{MISSING_RATE}</Badge>
      </span>
    );
  }
  const title = `Ausstiegskurs USD→CHF ${rateText(chf.exit)} · Einstiegskurs ${rateText(chf.entry)} · Handelsergebnis ${signed(chf.split.trading, "CHF")} · Währungseffekt ${signed(chf.split.fxEffect, "CHF")}`;
  return (
    <span title={title} className="cursor-help underline decoration-dotted underline-offset-2">
      {signed(chf.split.total, "CHF")}
    </span>
  );
}

export function TradeStatus({ status }: { status: string }) {
  return status === "OPEN" ? <Badge tone="accent">offen</Badge> : <Badge>geschlossen</Badge>;
}

export const TradeLink = ({ id, children }: { id: string; children: ReactNode }) => (
  <Link href={tradeHref(id)} className="font-medium hover:underline">
    {children}
  </Link>
);

/** Labeled definition row for dense detail blocks. */
export function Row({ label, children, hint }: { label: ReactNode; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 border-b border-[var(--grid)] py-1.5 last:border-0">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="text-right text-sm tabular">
        {children}
        {hint && <div className="text-xs text-ink-2">{hint}</div>}
      </dd>
    </div>
  );
}

/** One step of the decision chain. */
export function Step({ title, time, children, tone = "neutral" }: { title: ReactNode; time?: ReactNode; children?: ReactNode; tone?: Tone }) {
  const dot: Record<Tone, string> = { neutral: "bg-[var(--axis)]", accent: "bg-accent", good: "bg-good", warning: "bg-[var(--warning)]", critical: "bg-critical" };
  return (
    <li className="relative pb-4 pl-6 last:pb-0">
      <span aria-hidden className="absolute left-[5px] top-2 h-full w-px bg-[var(--grid)]" />
      <span aria-hidden className={`absolute left-0 top-1.5 h-[11px] w-[11px] rounded-full border-2 border-surface ${dot[tone]}`} />
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="text-sm font-semibold">{title}</span>
        {time && <span className="text-xs text-ink-2 tabular">{time}</span>}
      </div>
      {children && <div className="mt-1 min-w-0 text-sm">{children}</div>}
    </li>
  );
}
