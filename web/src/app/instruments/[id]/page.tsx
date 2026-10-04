import Link from "next/link";
import { notFound } from "next/navigation";
import { CandleChart, type PriceLine } from "@/components/candle-chart";
import { SetupHint } from "@/components/setup-hint";
import { SignalList, UnverifiedBanner, instrumentHref } from "@/components/signal-list";
import { regimeLabel } from "@/components/status";
import { Card, Empty, PageHeader } from "@/components/ui";
import { guard } from "@/lib/data/guard";
import { loadInstrument, parseTimeframe, TIMEFRAMES } from "@/lib/data/instrument";
import { dateTime, price } from "@/lib/format";
import { REGIME_TOKEN } from "@/lib/regime";

export const dynamic = "force-dynamic";

/** Segment arrives URL-encoded ("KRAKEN%3ABTC%2FUSD"); tolerate an already decoded value. */
function decodeId(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

function LegendLine({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden className="inline-block w-4 border-t-2 border-dashed" style={{ borderColor: color }} />
      {label}
    </span>
  );
}

export default async function InstrumentPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ tf?: string }> }) {
  const id = decodeId((await params).id);
  const timeframe = parseTimeframe((await searchParams).tf);
  const r = await guard(() => loadInstrument(id, timeframe));
  if (r.ok && !r.data) notFound();

  const d = r.ok ? r.data : null;
  const currency = d?.instrument.quoteCurrency;
  const lines: PriceLine[] = [];
  if (d?.latestBuy?.entry) lines.push({ price: Number(d.latestBuy.entry), title: "Einstieg", kind: "entry" });
  if (d?.latestBuy?.stop) lines.push({ price: Number(d.latestBuy.stop), title: "Stop", kind: "stop" });
  const regimesShown = d ? [...new Set(d.regimes.map((x) => x.regime))] : [];
  const tab = (on: boolean) => `rounded-md px-3 py-1 text-xs ${on ? "bg-surface-2 font-medium text-ink" : "text-muted hover:text-ink"}`;

  return (
    <>
      <PageHeader
        title={id}
        subtitle={d ? `${d.instrument.name} · ${d.instrument.venue} · Handelswährung ${d.instrument.quoteCurrency}` : undefined}
        actions={
          <div className="inline-flex rounded-lg border border-line bg-surface p-0.5" role="group" aria-label="Zeitebene">
            {TIMEFRAMES.map((tf) => (
              <Link key={tf} href={instrumentHref(id, tf)} aria-current={tf === timeframe ? "true" : undefined} className={tab(tf === timeframe)}>
                {tf}
              </Link>
            ))}
          </div>
        }
      />
      <UnverifiedBanner />
      {!r.ok || !d ? (
        !r.ok && <SetupHint state={r} />
      ) : (
        <>
          <Card
            title={`Kerzen ${timeframe}`}
            subtitle={d.candles.length ? `Letzter Schlusskurs ${price(d.lastClose, currency)} · Kerzenschluss ${dateTime(d.lastCloseTime)} · ${d.candles.length} Kerzen geladen` : undefined}
          >
            {d.candles.length ? (
              <>
                <CandleChart candles={d.candles} markers={d.markers} regimes={d.regimes} lines={lines} />
                <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-ink-2">
                  <span className="inline-flex items-center gap-1.5">
                    <span aria-hidden className="text-accent">▲</span>
                    BUY-Signal (gespeichert{d.markers.length ? `, ${d.markers.length} in den geladenen Kerzen` : ", keines in den geladenen Kerzen"})
                  </span>
                  {d.latestBuy?.entry && <LegendLine color="var(--accent)" label={`Einstieg ${price(d.latestBuy.entry, currency)}`} />}
                  {d.latestBuy?.stop && <LegendLine color="var(--critical)" label={`Stop ${price(d.latestBuy.stop, currency)}`} />}
                  {d.latestBuy && <span>(letztes BUY-Signal, Kerzenschluss {dateTime(d.latestBuy.candleClose)})</span>}
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-ink-2">
                  <span className="font-medium">Regime-Streifen (oben im Chart):</span>
                  {regimesShown.length ? (
                    regimesShown.map((x) => (
                      <span key={x} className="inline-flex items-center gap-1.5">
                        <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: `var(${REGIME_TOKEN[x] ?? "--axis"})` }} />
                        {regimeLabel(x)}
                      </span>
                    ))
                  ) : (
                    <span>keine Regime gespeichert</span>
                  )}
                </div>
                <p className="mt-2 text-xs text-muted">Marker, Linien und Regime stammen ausschliesslich aus gespeicherten Datensätzen; im Browser wird nichts neu berechnet. Zeiten in Europe/Zurich.</p>
              </>
            ) : (
              <Empty>Keine Kerzen für {timeframe} gespeichert.</Empty>
            )}
          </Card>

          <h2 className="mb-2 mt-6 text-sm font-semibold">Letzte Entscheide ({timeframe})</h2>
          {d.decisions.length ? <SignalList rows={d.decisions} linkInstrument={false} /> : <Empty>Noch keine Entscheide für diese Zeitebene gespeichert.</Empty>}
        </>
      )}
    </>
  );
}
