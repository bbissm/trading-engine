import Link from "next/link";
import { notFound } from "next/navigation";
import { AutoRefresh } from "@/components/auto-refresh";
import { EquityChart } from "@/components/equity-chart";
import { ModeBadge } from "@/components/mode-badge";
import { AccountControls, AutopilotBadge, paperHref, PositionList, RecordList, Simulated, type Column } from "@/components/paper";
import { SetupHint } from "@/components/setup-hint";
import { instrumentHref } from "@/components/signal-list";
import { Badge, Card, Empty, PageHeader, Stat } from "@/components/ui";
import { guard } from "@/lib/data/guard";
import { listPaperCommands, loadPaperAccount, type ClosedTrade, type FillRow, type WorkingOrder } from "@/lib/data/paper";
import { dateTime, decimal, int, price } from "@/lib/format";
import { autopilotState, FILL_SIDE, ORDER_ROLE, ORDER_SIDE, ORDER_TYPE, orderState } from "@/lib/paper";

export const dynamic = "force-dynamic";

/** Segment arrives URL-encoded; tolerate an already decoded value. */
function decodeId(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

async function load(id: string, episode: number | undefined) {
  const [detail, commands] = await Promise.all([loadPaperAccount(id, episode), listPaperCommands({ limit: 30 })]);
  return detail ? { detail, commands } : null;
}

export default async function PaperAccountPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ episode?: string }> }) {
  const id = decodeId((await params).id);
  const wanted = Number((await searchParams).episode);
  const r = await guard(() => load(id, Number.isInteger(wanted) && wanted > 0 ? wanted : undefined));
  if (r.ok && !r.data) notFound();
  if (!r.ok || !r.data) {
    return (
      <>
        <PageHeader title={id} actions={<ModeBadge mode="PAPER" />} />
        {!r.ok && <SetupHint state={r} />}
      </>
    );
  }

  const { detail: d, commands } = r.data;
  const { account: a, episode: e, kpi } = d;
  const cur = a.currency;
  const ended = e.endedAt !== null;
  const tab = (on: boolean) => `rounded-md px-2.5 py-1 text-xs ${on ? "bg-surface-2 font-medium text-ink" : "text-muted hover:text-ink"}`;

  const orderColumns: Column<WorkingOrder>[] = [
    {
      label: "Order",
      cell: (o) => (
        <>
          {ORDER_ROLE[o.role] ?? o.role}{" "}
          <Link href={instrumentHref(o.instrumentId)} className="font-normal text-ink-2 hover:underline">
            · {o.instrumentId}
          </Link>
        </>
      ),
    },
    { label: "Typ", cell: (o) => ORDER_TYPE[o.type] ?? o.type },
    { label: "Seite", cell: (o) => ORDER_SIDE[o.side] ?? o.side },
    { label: "Menge", num: true, cell: (o) => decimal(o.qty, 0) },
    { label: "Limit", num: true, cell: (o) => price(o.limitPrice, o.quoteCurrency) },
    { label: "Stop", num: true, cell: (o) => price(o.stopPrice, o.quoteCurrency) },
    {
      label: "Status",
      wide: true,
      cell: (o) => {
        const s = orderState(o.state, decimal(o.filledQty, 0), decimal(o.qty, 0));
        return <Badge tone={s.tone}>{s.label}</Badge>;
      },
    },
    { label: "Erstellt", cell: (o) => <span className="whitespace-nowrap tabular">{dateTime(o.createdAt)}</span> },
    { label: "Gültig bis", cell: (o) => <span className="whitespace-nowrap tabular">{o.validUntil ? dateTime(o.validUntil) : "bis Widerruf"}</span> },
  ];

  const closedColumns: Column<ClosedTrade>[] = [
    {
      label: "Instrument",
      cell: (t) => (
        <Link href={instrumentHref(t.instrumentId)} className="font-medium hover:underline">
          {t.instrumentId}
        </Link>
      ),
    },
    { label: "Eröffnet", cell: (t) => <span className="whitespace-nowrap tabular">{dateTime(t.openedAt)}</span> },
    { label: "Geschlossen", cell: (t) => <span className="whitespace-nowrap tabular">{dateTime(t.closedAt)}</span> },
    { label: "Strategie", wide: true, cell: (t) => <span className="break-all font-mono text-xs">{t.strategyVersionId}</span> },
    { label: "Menge", num: true, cell: (t) => decimal(t.qty, 0) },
    { label: "Einstiegswert", num: true, cell: (t) => decimal(t.entryValue) },
    { label: "Ausstiegswert", num: true, cell: (t) => decimal(t.exitValue) },
    { label: "Gebühren", num: true, cell: (t) => decimal(t.fees) },
    { label: `Netto (${cur})`, num: true, cell: (t) => <span className="font-medium">{decimal(t.net)}</span> },
    { label: "Ausstiegsgrund", wide: true, cell: (t) => t.exitReason ?? "—" },
  ];

  const fillColumns: Column<FillRow>[] = [
    {
      label: "Ausführung",
      cell: (f) => (
        <>
          {FILL_SIDE[f.side] ?? f.side} <span className="font-normal text-ink-2">· {f.instrumentId}</span>
        </>
      ),
    },
    { label: "Zeit", cell: (f) => <span className="whitespace-nowrap tabular">{dateTime(f.time)}</span> },
    { label: "Order", cell: (f) => ORDER_ROLE[f.role] ?? f.role },
    { label: "Menge", num: true, cell: (f) => decimal(f.qty, 0) },
    { label: "Preis", num: true, cell: (f) => price(f.price, f.quoteCurrency) },
    { label: "Gebühr", num: true, cell: (f) => price(f.fee, f.feeCurrency) },
    { label: "Herkunft", cell: (f) => (f.simulated ? <Simulated /> : <Badge tone="warning">vom Anbieter beobachtet</Badge>) },
  ];

  return (
    <>
      <PageHeader
        title={a.name}
        subtitle={
          <>
            <span className="break-all font-mono">{a.id}</span> · virtuelles Konto in {cur} · alle Zahlen simuliert
          </>
        }
        actions={
          <>
            <AutopilotBadge state={a.state} />
            <ModeBadge mode="PAPER" />
          </>
        }
      />
      <AutoRefresh seconds={15} />

      <div className="space-y-4">
        <Card title="Paper-Autopilot" subtitle={autopilotState(a.state).meaning}>
          {a.reason && <p className="mb-3 break-words text-xs text-ink-2">Letzte Zustandsänderung: {a.reason} ({dateTime(a.stateAt)})</p>}
          <AccountControls account={a} commands={commands} />
        </Card>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-muted">Episode</span>
          <div className="inline-flex flex-wrap rounded-lg border border-line bg-surface p-0.5" role="group" aria-label="Episode">
            {d.episodes.map((x) => (
              <Link key={x.id} href={paperHref(a.id, x.number)} aria-current={x.id === e.id ? "true" : undefined} className={tab(x.id === e.id)}>
                {x.number}
                {x.endedAt ? " · abgeschlossen" : " · laufend"}
              </Link>
            ))}
          </div>
        </div>

        <Card
          title={`Episode ${e.number}${ended ? " · abgeschlossen" : ""}`}
          subtitle={
            ended
              ? `${dateTime(e.startedAt)} bis ${dateTime(e.endedAt)} · nur lesbar, wird nicht mehr verändert`
              : `Seit ${dateTime(e.startedAt)} · letzte simulierte Kerze ${dateTime(e.simThrough)} · Kostenmodell ${e.costModel} · Simulator ${e.simVersion}`
          }
          actions={<Simulated />}
        >
          <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
            <Stat label="Startkapital" value={price(kpi.startCash, cur)} />
            <Stat label="Eigenkapital" value={price(kpi.equity ?? kpi.cash, cur)} hint={kpi.equity ? `Bewertung ${dateTime(kpi.equityAt)}` : "Noch keine Bewertung – entspricht Cash"} />
            <Stat label="Cash" value={price(kpi.cash, cur)} />
            <Stat label="Investiert" value={price(kpi.invested, cur)} />
            <Stat label="Reserviert" value={price(kpi.reserved, cur)} hint="für offene Einstiegsorders" />
            <Stat label="Offenes Stop-Risiko" value={price(kpi.openRisk, cur)} hint="geplant, offene Positionen" />
            <Stat label="Realisiertes Ergebnis" value={price(kpi.realized, cur)} hint="nach Gebühren" />
            <Stat label="Gebühren" value={price(kpi.feesPaid, cur)} />
            <Stat label="Abgeschlossene Trades" value={int(kpi.closedTrades)} />
            <Stat label="Davon mit Gewinn" value={int(kpi.closedWithGain)} hint={`von ${int(kpi.closedTrades)}`} />
          </div>
          <p className="mt-3 text-xs text-ink-2">Stops sind Auslöse- und Kontrollregeln, kein garantierter Maximalverlust.</p>
        </Card>

        <Card title="Eigenkapitalkurve" subtitle="Bewertung je simuliertem 4h-Kerzenschluss" actions={<Simulated />}>
          {d.equityCurve.length ? (
            <>
              <EquityChart points={d.equityCurve} startCash={Number(kpi.startCash)} />
              <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-ink-2">
                <span className="inline-flex items-center gap-1.5">
                  <span aria-hidden className="inline-block w-4 border-t-2" style={{ borderColor: "var(--s1)" }} />
                  Eigenkapital (simuliert), {int(d.equityCurve.length)} Bewertungen
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span aria-hidden className="inline-block w-4 border-t-2 border-dashed" style={{ borderColor: "var(--muted)" }} />
                  Startkapital {price(kpi.startCash, cur)}
                </span>
                <span>Zeiten in Europe/Zurich.</span>
              </div>
            </>
          ) : (
            <Empty>Noch keine Bewertung gespeichert. Die Kurve entsteht mit dem ersten simulierten Kerzenschluss nach dem Start.</Empty>
          )}
        </Card>

        <Card title="Offene Positionen" subtitle="Simulierter Bestand dieser Episode" actions={<ModeBadge mode="PAPER" />}>
          {d.positions.length ? <PositionList rows={d.positions} /> : <Empty>{ended ? "Diese Episode ist abgeschlossen; sie hat keine offenen Positionen." : "Keine offenen Positionen."}</Empty>}
        </Card>

        <Card title="Offene Orders" subtitle="Orders beim Simulator, die noch ausgeführt werden können" actions={<ModeBadge mode="PAPER" />}>
          {d.orders.length ? <RecordList rows={d.orders} columns={orderColumns} rowKey={(o) => o.id} /> : <Empty>Keine offenen Orders.</Empty>}
        </Card>

        <Card title="Abgeschlossene Trades" subtitle={`Neueste zuerst · Beträge in ${cur} · Netto nach Gebühren`} actions={<Simulated />}>
          {d.closed.length ? <RecordList rows={d.closed} columns={closedColumns} rowKey={(t) => t.id} /> : <Empty>Noch kein Trade abgeschlossen.</Empty>}
        </Card>

        <Card title="Letzte Ausführungen" subtitle="Fills des Simulators, neueste zuerst (höchstens 50)" actions={<Simulated />}>
          {d.fills.length ? <RecordList rows={d.fills} columns={fillColumns} rowKey={(f) => f.id} /> : <Empty>Noch keine Ausführung. Es wurde nichts gekauft oder verkauft.</Empty>}
        </Card>
      </div>
    </>
  );
}
