import Link from "next/link";
import type { ReactNode } from "react";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3 md:mb-6">
      <div className="min-w-0">
        <h1 className="break-words text-xl font-semibold tracking-tight md:text-2xl">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-xs text-ink-2 md:text-sm">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, children, className = "", actions, subtitle }: { title?: ReactNode; subtitle?: ReactNode; children: ReactNode; className?: string; actions?: ReactNode }) {
  return (
    <section className={`min-w-0 rounded-xl border border-line bg-surface p-4 ${className}`}>
      {(title || actions) && (
        <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
          <div>
            {title && <h2 className="text-sm font-semibold">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="ml-auto">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "critical" | "warning" | "good" }) {
  const toneClass = tone === "critical" ? "text-critical" : tone === "good" ? "text-good" : "";
  return (
    <div className="min-w-0">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className={`mt-0.5 break-words text-lg font-semibold tracking-tight tabular ${toneClass}`}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-ink-2">{hint}</div>}
    </div>
  );
}

const BADGE: Record<string, string> = {
  neutral: "bg-surface-2 text-ink-2",
  accent: "bg-[color-mix(in_oklab,var(--accent)_15%,transparent)] text-accent",
  good: "bg-[color-mix(in_oklab,var(--good)_15%,transparent)] text-good",
  warning: "bg-[color-mix(in_oklab,var(--warning)_22%,transparent)] text-ink",
  critical: "bg-[color-mix(in_oklab,var(--critical)_15%,transparent)] text-critical",
};
export type Tone = "neutral" | "accent" | "good" | "warning" | "critical";

export function Badge({ children, tone = "neutral", title }: { children: ReactNode; tone?: Tone; title?: string }) {
  return (
    <span title={title} className={`inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-xs font-medium ${BADGE[tone]}`}>
      {children}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line p-4 text-sm text-ink-2">{children}</div>;
}

export function TableWrap({ children }: { children: ReactNode }) {
  return <div className="-mx-4 overflow-x-auto px-4">{children}</div>;
}

/** Prominent, calm notice (e.g. "Signale ungeprüft"). */
export function Banner({ children, tone = "warning" }: { children: ReactNode; tone?: "warning" | "info" }) {
  const cls = tone === "warning" ? "border-[color-mix(in_oklab,var(--warning)_60%,transparent)] bg-[color-mix(in_oklab,var(--warning)_14%,transparent)]" : "border-line bg-surface-2";
  return (
    <div role="note" className={`mb-4 flex gap-2 rounded-xl border p-3 text-sm ${cls}`}>
      <span aria-hidden>{tone === "warning" ? "⚠️" : "ℹ️"}</span>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

const SEVERITY_ICON: Record<string, string> = { critical: "⛔", warning: "⚠️", info: "ℹ️" };
const SEVERITY_LABEL: Record<string, string> = { critical: "Kritisch", warning: "Warnung", info: "Hinweis" };

export function AlertList({ alerts }: { alerts: { id: string; severity: string; title: string; detail: string; href?: string }[] }) {
  if (!alerts.length) return <Empty>Nichts offen.</Empty>;
  return (
    <ul className="divide-y divide-[var(--grid)]">
      {alerts.map((a) => (
        <li key={a.id} className="flex gap-3 py-2 first:pt-0 last:pb-0">
          <span aria-hidden>{SEVERITY_ICON[a.severity]}</span>
          <div className="min-w-0">
            <div className="text-sm font-medium">
              <span className="sr-only">{SEVERITY_LABEL[a.severity]}: </span>
              {a.href ? (
                <Link href={a.href} className="hover:underline">
                  {a.title}
                </Link>
              ) : (
                a.title
              )}
            </div>
            <div className="break-words text-xs text-ink-2">{a.detail}</div>
          </div>
        </li>
      ))}
    </ul>
  );
}
