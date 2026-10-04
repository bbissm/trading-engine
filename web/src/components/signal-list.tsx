import Link from "next/link";
import type { SignalOutcomeRow } from "@/lib/data/paper";
import type { SignalRow } from "@/lib/data/signals";
import { dateTime, price } from "@/lib/format";
import { ModeBadge } from "./mode-badge";
import { ActionBadge, RegimeBadge } from "./status";
import { Banner } from "./ui";

export const instrumentHref = (id: string, timeframe?: string) => `/instruments/${encodeURIComponent(id)}${timeframe ? `?tf=${timeframe}` : ""}`;

/** Required on every page that shows signals as long as no strategy version has passed a gate. */
export function UnverifiedBanner() {
  return (
    <Banner>
      <strong>Signale ungeprüft – kein Qualitätsnachweis.</strong> Es entstehen keine Echtgeld-Orders; nur der Paper-Autopilot kann daraus simulierte Orders ableiten.
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
export function SignalList({ rows, linkInstrument = true, outcomes }: { rows: SignalRow[]; linkInstrument?: boolean; outcomes?: Record<string, SignalOutcomeRow[]> }) {
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

            {outcomes?.[s.id]?.length ? (
              <div className="mt-3 border-t border-[var(--grid)] pt-3">
                <div className="text-xs font-medium text-muted">Was daraus wurde (simuliert)</div>
                <ul className="mt-1 space-y-1.5">
                  {outcomes[s.id].map((o) => (
                    <li key={`${o.accountId}:${o.episodeNumber}`} className="flex flex-wrap items-start gap-x-2 gap-y-1 text-sm">
                      <ModeBadge mode="PAPER" />
                      <span className="min-w-0 flex-1 basis-56 break-words">
                        {o.status === "ORDERED" ? (
                          <>
                            Order erteilt (Paper,{" "}
                            <Link href={`/paper/${encodeURIComponent(o.accountId)}`} className="hover:underline">
                              {o.accountName}
                            </Link>
                            )
                          </>
                        ) : o.status === "BLOCKED" ? (
                          <>
                            Blockiert: {o.reasons.length ? o.reasons.join("; ") : "kein Grund gespeichert"}{" "}
                            <span className="text-ink-2">
                              (
                              <Link href={`/paper/${encodeURIComponent(o.accountId)}`} className="hover:underline">
                                {o.accountName}
                              </Link>
                              )
                            </span>
                          </>
                        ) : (
                          `${o.status} (${o.accountName})`
                        )}
                        {o.episodeNumber !== null && <span className="text-xs text-ink-2"> · Episode {o.episodeNumber}</span>}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
