import Link from "next/link";
import { notFound } from "next/navigation";
import { CandleChart, type PriceLine } from "@/components/candle-chart";
import { hours, journalHref, ModeTag, pct, rateText, rText, Row, signed, Step, toneOf, TradeStatus } from "@/components/journal";
import { Simulated } from "@/components/paper";
import { SetupHint } from "@/components/setup-hint";
import { instrumentHref } from "@/components/signal-list";
import { RegimeBadge } from "@/components/status";
import { Badge, Card, Empty, PageHeader, Stat } from "@/components/ui";
import { guard } from "@/lib/data/guard";
import { loadTradeDetail, type DetailOrder, type TradeDetail } from "@/lib/data/journal";
import { dateTime, decimal, price } from "@/lib/format";
import { FILL_SIDE, ORDER_SIDE, ORDER_TYPE, orderState } from "@/lib/paper";

export const dynamic = "force-dynamic";

function decodeId(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

const ageText = (s: number) => (s < 120 ? `${s} s` : s < 7200 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`);

function OrderLine({ o, cur }: { o: DetailOrder; cur: string }) {
  const s = orderState(o.state, decimal(o.filledQty, 0), decimal(o.qty, 0));
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span>
        {ORDER_TYPE[o.type] ?? o.type} · {ORDER_SIDE[o.side] ?? o.side} {decimal(o.qty, 0)}
        {o.limitPrice && <> · Limit {price(o.limitPrice, cur)}</>}
        {o.stopPrice && <> · Stop {price(o.stopPrice, cur)}</>}
      </span>
      <Badge tone={s.tone}>{s.label}</Badge>
      {o.avgFill && <span className="text-xs text-ink-2">Ø Ausführung {price(o.avgFill, cur)}</span>}
      {o.reason && <span className="w-full break-words text-xs text-ink-2">Grund: {o.reason}</span>}
    </div>
  );
}

function Timeline({ d }: { d: TradeDetail }) {
  const t = d.trade;
  const cur = t.currency;
  const s = d.signal;
  const entries = d.orders.filter((o) => o.role === "ENTRY");
  const protects = d.orders.filter((o) => o.role === "PROTECT");
  const exits = d.orders.filter((o) => o.role === "EXIT");
  const fillsOf = (o: DetailOrder) => d.fills.filter((f) => f.orderId === o.id);
  const fillList = (o: DetailOrder) =>
    fillsOf(o).map((f) => (
      <div key={f.id} className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        <span className="font-medium">
          {FILL_SIDE[f.side] ?? f.side} {decimal(f.qty, 0)} zu {price(f.price, cur)}
        </span>
        <span className="text-ink-2">Gebühr {price(f.fee, f.feeCurrency)}</span>
        <span className="text-ink-2 tabular">{dateTime(f.time)}</span>
        {f.simulated ? <Simulated /> : <Badge tone="warning">vom Anbieter beobachtet</Badge>}
      </div>
    ));

  return (
    <ol className="mt-1">
      <Step title="Signal" time={s ? `Kerzenschluss ${dateTime(s.candleClose)}` : undefined} tone="accent">
        {s ? (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="accent">{s.action}</Badge>
              <RegimeBadge regime={s.regime} prefix="Regime " />
              <span className="text-xs text-ink-2">
                Setup-Score – keine Gewinnwahrscheinlichkeit: <span className="font-medium text-ink">{s.score === null ? "—" : `${s.score} / 100`}</span>
              </span>
              <span className="text-xs text-ink-2">
                Datenalter bei Erzeugung {ageText(s.dataAgeS)} · Quelle {s.dataSource} · Version <span className="font-mono">{s.strategyVersionId}</span>
              </span>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <div className="text-xs font-medium text-muted">Auslöser</div>
                {s.triggers.length ? (
                  <ul className="mt-0.5 list-disc pl-4">
                    {s.triggers.map((x, i) => (
                      <li key={i} className="break-words">
                        {x}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="text-ink-2">keine gespeichert</div>
                )}
              </div>
              <div>
                <div className="text-xs font-medium text-muted">Gegenfaktoren</div>
                {s.counter.length ? (
                  <ul className="mt-0.5 list-disc pl-4">
                    {s.counter.map((x, i) => (
                      <li key={i} className="break-words">
                        {x}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="text-ink-2">keine gespeichert</div>
                )}
              </div>
            </div>
            <div className="text-xs text-ink-2">
              Geplant: Einstieg {price(s.entry, cur)} · Stop {price(s.stop, cur)} · Ziel {price(s.target, cur)} · max. {s.maxHoldBars ?? "—"} Kerzen · gültig bis {dateTime(s.validUntil)}
            </div>
          </div>
        ) : (
          <span className="text-ink-2">Kein Signal mit diesem Trade verknüpft.</span>
        )}
      </Step>

      <Step title="Risikoprüfung" time={d.outcome ? dateTime(d.outcome.createdAt) : undefined} tone={d.outcome?.status === "BLOCKED" ? "critical" : "good"}>
        {d.outcome ? (
          <div className="space-y-1">
            <Badge tone={d.outcome.status === "ORDERED" ? "good" : "critical"}>{d.outcome.status === "ORDERED" ? "✓ Order erteilt" : "✕ blockiert"}</Badge>
            {d.outcome.reasons.length > 0 && (
              <ul className="list-disc pl-4">
                {d.outcome.reasons.map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            )}
            {Object.keys(d.outcome.values).length > 0 && (
              <dl className="grid grid-cols-1 gap-x-4 text-xs sm:grid-cols-2">
                {Object.entries(d.outcome.values).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-2 border-b border-[var(--grid)] py-0.5">
                    <dt className="text-ink-2">{k.replace(/_/g, " ")}</dt>
                    <dd className="font-mono">{v}</dd>
                  </div>
                ))}
              </dl>
            )}
          </div>
        ) : (
          <span className="text-ink-2">Kein Ergebnis der Risikoprüfung gespeichert.</span>
        )}
      </Step>

      {entries.map((o) => (
        <Step key={o.id} title="Einstiegsorder" time={dateTime(o.createdAt)}>
          <OrderLine o={o} cur={cur} />
          {fillList(o)}
        </Step>
      ))}

      <Step title="Schutz-Stop" tone="warning">
        {protects.map((o) => (
          <div key={o.id} className="mb-1">
            <OrderLine o={o} cur={cur} />
            {fillList(o)}
          </div>
        ))}
        <div className="text-xs text-ink-2">
          Geplanter Stop {price(t.plannedStop, cur)} → letzter Stop {price(t.currentStop, cur)}
          {t.currentStop !== t.plannedStop ? " (nachgezogen)" : " (unverändert)"}. Der Verlauf dazwischen wird nicht gespeichert: Die Engine aktualisiert die Schutzorder an Ort und Stelle (nur Plan- und letzter Wert
          liegen vor).
        </div>
      </Step>

      {exits.map((o) => (
        <Step key={o.id} title={o.type === "LIMIT" ? "Ziel-Order" : "Ausstiegsorder"} time={dateTime(o.createdAt)}>
          <OrderLine o={o} cur={cur} />
          {fillList(o)}
        </Step>
      ))}

      <Step title="Ausstieg" time={t.closedAt ? dateTime(t.closedAt) : undefined} tone={t.status === "OPEN" ? "accent" : toneOf(t.net) ?? "neutral"}>
        {t.status === "OPEN" ? (
          <span className="text-ink-2">Trade noch offen · betreut bis {dateTime(d.managedThrough)} · {d.barsHeld} Kerzen gehalten</span>
        ) : (
          <span>
            Grund: <span className="font-medium">{t.exitReason ?? "nicht gespeichert"}</span> · Ø Ausstieg {price(t.avgExit, cur)} · nach {d.barsHeld} Kerzen
          </span>
        )}
      </Step>
    </ol>
  );
}

function Analysis({ d }: { d: TradeDetail }) {
  const t = d.trade;
  const p = d.plan;
  const c = d.classification;
  const cur = t.currency;
  const plan = d.exitPlan;
  const planItems = [
    typeof plan.trail_atr === "number" ? `Trailing-Stop ${plan.trail_atr} × ATR vom höchsten Schluss` : null,
    plan.target ? `Ziel ${price(String(plan.target), cur)}` : null,
    typeof plan.max_hold_bars === "number" ? `maximale Haltedauer ${plan.max_hold_bars} Kerzen` : null,
    Array.isArray(plan.exit_unless_regime) ? `Ausstieg, sobald das Regime nicht mehr ${plan.exit_unless_regime.join("/")} ist` : null,
    plan.breakout_level ? `Ausbruchsniveau ${price(String(plan.breakout_level), cur)} (${plan.failed_breakout_bars ?? 0} Kerzen Frist)` : null,
  ].filter((x): x is string => !!x);

  return (
    <Card title="Warum hat er das gemacht?" subtitle="Regelbasiert aus den gespeicherten Daten – kein Sprachmodell, keine nachträgliche Deutung">
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-3">
          <div>
            <div className="text-xs font-medium text-muted">Ausgelöste Regel</div>
            <p className="mt-0.5 text-sm">
              {d.signal?.triggers.length ? d.signal.triggers.join(" · ") : "Keine Auslöser gespeichert."} <span className="text-ink-2">(Strategie {t.strategyVersionId})</span>
            </p>
          </div>
          <div>
            <div className="text-xs font-medium text-muted">Exit-Plan beim Einstieg</div>
            <p className="mt-0.5 text-sm">{planItems.length ? planItems.join(" · ") : "Kein Exit-Plan gespeichert."}</p>
          </div>
          <div className="rounded-lg border border-line bg-surface-2 p-3">
            <div className="text-xs font-medium text-muted">Hauptabweichung</div>
            <div className="mt-0.5 text-base font-semibold">{c.label}</div>
            <p className="mt-1 text-xs text-ink-2">Regel: {c.rule}</p>
          </div>
          <div>
            <div className="text-xs font-medium text-muted">Planprüfung</div>
            <ul className="mt-1 space-y-1 text-sm">
              <li>{p.stopNeverWidened ? "✓ Stop nie weiter entfernt als geplant" : "✕ Stop liegt unter dem geplanten Stop – Regelverstoss"}</li>
              {p.entryWithinLimit !== null && <li>{p.entryWithinLimit ? "✓ Einstieg innerhalb des Limits" : "✕ Einstieg über dem Limit"}</li>}
              <li>{d.outcome?.status === "ORDERED" ? "✓ Risikoprüfung bestanden, Order regelkonform erteilt" : "· Risikoprüfung nicht gespeichert"}</li>
            </ul>
          </div>
        </div>
        <dl>
          <Row label="Geplanter Stop">{price(p.plannedStop, cur)}</Row>
          <Row label="Letzter Stop vor dem Ausstieg">{price(p.currentStop, cur)}</Row>
          <Row label="Ø Ausstieg (ausgeführt)">{price(p.avgExit, cur)}</Row>
          <Row label="Abweichung zum geplanten Stop" hint="(Ø Ausstieg − geplanter Stop) × Menge">
            {signed(p.vsPlannedStop, cur)}
          </Row>
          <Row label="Abweichung zum letzten Stop" hint="negativ = schlechter ausgeführt als der Stop (Lücke/Slippage)">
            {signed(p.vsCurrentStop, cur)}
          </Row>
          <Row label="Geplantes Risiko">{price(decimal(p.plannedRisk), cur)}</Row>
          <Row label="Realisiert" hint={p.realizedR ? `${rText(p.realizedR)} – Stops sind kein garantierter Maximalverlust` : undefined}>
            {signed(p.realized, cur)}
          </Row>
          <Row label="Gebühren" hint={`${p.feesOfRisk ? `${pct(p.feesOfRisk, 0)} des geplanten Risikos` : ""}${p.feesOfGross ? ` · ${pct(p.feesOfGross, 0)} des |Brutto|` : ""}`}>
            {price(decimal(p.fees), cur)}
          </Row>
        </dl>
      </div>
    </Card>
  );
}

export default async function TradeDetailPage({ params }: { params: Promise<{ tradeId: string }> }) {
  const id = decodeId((await params).tradeId);
  const r = await guard(() => loadTradeDetail(id));
  if (r.ok && !r.data) notFound();
  if (!r.ok || !r.data) {
    return (
      <>
        <PageHeader title={`Trade ${id}`} />
        {!r.ok && <SetupHint state={r} />}
      </>
    );
  }
  const d = r.data;
  const t = d.trade;
  const cur = t.currency;
  const chf = t.chf;
  const lines: PriceLine[] = [
    ...(t.avgEntry ? [{ price: Number(t.avgEntry), title: "Ø Einstieg", kind: "entry" as const }] : []),
    { price: Number(t.plannedStop), title: "Stop geplant", kind: "stop" },
    ...(t.currentStop !== t.plannedStop ? [{ price: Number(t.currentStop), title: "Stop zuletzt", kind: "stop" as const }] : []),
  ];

  return (
    <>
      <PageHeader
        title={`${t.instrumentId} · Trade`}
        subtitle={
          <>
            <span className="break-all font-mono">{t.id}</span> · {d.account.name}
            {d.episodeNumber !== null && <> · Episode {d.episodeNumber}</>} · eröffnet {dateTime(t.openedAt)}
            {t.closedAt && <> · geschlossen {dateTime(t.closedAt)}</>}
          </>
        }
        actions={
          <>
            <TradeStatus status={t.status} />
            <ModeTag mode={d.account.mode} />
          </>
        }
      />
      <div className="mb-3 flex flex-wrap gap-3 text-sm">
        <Link href={journalHref({ account: d.account.id, episode: d.episodeNumber })} className="text-accent">
          ← Journal
        </Link>
        <Link href={instrumentHref(t.instrumentId, t.timeframe)} className="text-accent">
          Instrumentanalyse →
        </Link>
      </div>

      <div className="space-y-4">
        <Card title="Ergebnis" subtitle={`Nach Gebühren · Handelswährung ${cur}`} actions={<ModeTag mode={d.account.mode} />}>
          <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label={`Netto (${cur})`} value={signed(t.net, cur)} />
            <Stat label="Netto (CHF)" value={chf.split ? signed(chf.split.total, "CHF") : t.status === "OPEN" ? "—" : "Kurs fehlt"} hint={chf.split ? `Handel ${signed(chf.split.trading, "CHF")} · Währung ${signed(chf.split.fxEffect, "CHF")}` : undefined} />
            <Stat label="In R" value={rText(t.r)} hint="Netto ÷ geplantes Risiko" />
            <Stat label="Gebühren" value={price(decimal(t.fees), cur)} />
            <Stat label="Menge" value={decimal(t.qty, 0)} hint={`Ø Einstieg ${price(t.avgEntry, cur)}`} />
            <Stat label="Haltedauer" value={hours(t.closedAt ? (t.closedAt.getTime() - t.openedAt.getTime()) / 3_600_000 : null)} hint={`${d.barsHeld} Kerzen ${t.timeframe}`} />
          </div>
          {t.status === "CLOSED" && (
            <dl className="mt-3 text-sm">
              <Row label={`Kurs ${cur}→CHF beim Einstieg`}>{rateText(chf.entry)}</Row>
              <Row label={`Kurs ${cur}→CHF beim Ausstieg`}>{rateText(chf.exit)}</Row>
            </dl>
          )}
        </Card>

        <Analysis d={d} />

        <Card title="Entscheidungskette" subtitle="Signal → Risikoprüfung → Orders → Ausführungen → Ausstieg" actions={<ModeTag mode={d.account.mode} />}>
          <Timeline d={d} />
        </Card>

        <Card title="Kursverlauf" subtitle={`${t.timeframe}-Kerzen um den Trade · Marker an Einstiegs- und Ausstiegskerze · Zeiten Europe/Zurich`}>
          {d.candles.length ? <CandleChart candles={d.candles} markers={d.markers} regimes={[]} lines={lines} /> : <Empty>Für diesen Zeitraum sind keine Kerzen gespeichert.</Empty>}
        </Card>
      </div>
    </>
  );
}
