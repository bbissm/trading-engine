import Link from "next/link";
import type { SignalRow } from "@/lib/data/signals";
import { dateTime, price } from "@/lib/format";
import { ActionBadge, RegimeBadge } from "./status";
import { Banner } from "./ui";

export const instrumentHref = (id: string, timeframe?: string) => `/instruments/${encodeURIComponent(id)}${timeframe ? `?tf=${timeframe}` : ""}`;

/** Required on every page that shows signals as long as no strategy version has passed a gate. */
export function UnverifiedBanner() {
  return (
    <Banner>
      <strong>Signale ungeprüft – kein Qualitätsnachweis.</strong> Es werden keine Orders erzeugt.
    </Banner>
  );
}

function Reasons({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs font-medium text-muted">{title}</div>
      {items.length ? (
        <ul className="mt-0.5 list-disc space-y-0.5 pl-4 text-sm">
          {items.map((t, i) => (
            <li key={i} className="break-words">
              {t}
            </li>
          ))}
        </ul>
      ) : (
        <div className="mt-0.5 text-sm text-ink-2">{empty}</div>
      )}
    </div>
  );
}

/** Stored signals as cards (phone and desktop). Shows what the strategy stored — nothing is recomputed. */
export function SignalList({ rows, linkInstrument = true }: { rows: SignalRow[]; linkInstrument?: boolean }) {
  return (
    <ul className="space-y-3">
      {rows.map((s) => {
        const noTrade = s.action === "NO_TRADE";
        return (
          <li key={s.id} className="rounded-xl border border-line bg-surface p-4">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              {linkInstrument ? (
                <Link href={instrumentHref(s.instrumentId, s.timeframe)} className="text-sm font-semibold hover:underline">
                  {s.instrumentId}
                </Link>
              ) : (
                <span className="text-sm font-semibold">{s.instrumentId}</span>
              )}
              <span className="text-xs text-ink-2">{s.timeframe}</span>
              <ActionBadge action={s.action} />
              <RegimeBadge regime={s.regime} />
              <span className="ml-auto text-xs text-ink-2">
                Kerzenschluss <span className="tabular">{dateTime(s.candleClose)}</span>
              </span>
            </div>

            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
              <div className="col-span-2 sm:col-span-1">
                <dt className="text-xs font-medium text-muted">Setup-Score (keine Gewinnwahrscheinlichkeit)</dt>
                <dd className="text-sm font-medium tabular">{s.score === null ? "—" : `${s.score} / 100`}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium text-muted">Einstieg</dt>
                <dd className="text-sm tabular">{price(s.entry, s.quoteCurrency)}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium text-muted">Stop</dt>
                <dd className="text-sm tabular">{price(s.stop, s.quoteCurrency)}</dd>
              </div>
              <div className="col-span-2 sm:col-span-1">
                <dt className="text-xs font-medium text-muted">Strategieversion</dt>
                <dd className="break-all font-mono text-xs leading-5">{s.strategyVersionId}</dd>
              </div>
            </dl>

            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <Reasons title={noTrade ? "Gründe für NO TRADE" : "Auslöser"} items={s.triggers} empty="Keine gespeichert." />
              <Reasons title="Gegenfaktoren" items={s.counter} empty="Keine gespeichert." />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
