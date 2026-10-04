import Link from "next/link";
import { ChfCell, exportHref, hours, journalHref, ModeTag, pct, rText, signed, toneOf, TradeLink, TradeStatus } from "@/components/journal";
import { RecordList, type Column } from "@/components/paper";
import { SetupHint } from "@/components/setup-hint";
import { Badge, Banner, Card, Empty, PageHeader, Stat, TableWrap } from "@/components/ui";
import { guard } from "@/lib/data/guard";
import { loadJournal, MIN_DAILY_RETURNS, parseJournalFilter, type JournalData, type JournalTrade } from "@/lib/data/journal";
import { FX_MAX_AGE_DAYS } from "@/lib/fx";
import { date, dateTime, decimal, int, price } from "@/lib/format";
import { MIN_TRADES_FOR_CI, sampleCaveat, TOO_FEW_TRADES } from "@/lib/stats";

export const dynamic = "force-dynamic";

const EXPORTS: { kind: string; label: string }[] = [
  { kind: "trades", label: "Trades" },
  { kind: "orders", label: "Orders" },
  { kind: "fills", label: "Ausführungen" },
  { kind: "fees", label: "Gebühren" },
  { kind: "fx", label: "Umrechnungskurse" },
];

function Filters({ d }: { d: JournalData }) {
  const f = d.filter;
  return (
    <form method="get" action="/journal" className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-7">
      <label className="col-span-2 block text-xs font-medium text-muted sm:col-span-1 lg:col-span-2">
        Konto
        <select name="account" defaultValue={d.account?.id} className="mt-1 block w-full">
          {d.accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.mode === "LIVE" ? "LIVE · Echtgeld" : "PAPER"} · {a.name}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-xs font-medium text-muted">
        Episode
        <select name="episode" defaultValue={d.episode?.number} className="mt-1 block w-full">
          {d.account?.episodes.map((e) => (
            <option key={e.id} value={e.number}>
              {e.number} · {e.endedAt ? "abgeschlossen" : "laufend"}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-xs font-medium text-muted">
        Status
        <select name="status" defaultValue={f.status} className="mt-1 block w-full">
          <option value="ALL">alle</option>
          <option value="CLOSED">geschlossen</option>
          <option value="OPEN">offen</option>
        </select>
      </label>
      <label className="block text-xs font-medium text-muted">
        Instrument
        <select name="instrument" defaultValue={f.instrument ?? ""} className="mt-1 block w-full">
          <option value="">alle</option>
          {d.instruments.map((i) => (
            <option key={i} value={i}>
              {i}
            </option>
          ))}
        </select>
      </label>
      <label className="col-span-2 block text-xs font-medium text-muted sm:col-span-1 lg:col-span-2">
        Strategieversion
        <select name="strategy" defaultValue={f.strategy ?? ""} className="mt-1 block w-full">
          <option value="">alle</option>
          {d.strategies.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-xs font-medium text-muted">
        Von
        <input type="date" name="from" defaultValue={f.from} className="mt-1 block w-full" />
      </label>
      <label className="block text-xs font-medium text-muted">
        Bis
        <input type="date" name="to" defaultValue={f.to} className="mt-1 block w-full" />
      </label>
      <div className="col-span-2 flex items-end gap-2 sm:col-span-1">
        <button type="submit" className="btn">
          Anwenden
        </button>
        <Link href={journalHref({ account: d.account?.id })} className="btn-ghost">
          Zurücksetzen
        </Link>
      </div>
    </form>
  );
}

function Kpis({ d }: { d: JournalData }) {
  const k = d.kpis;
  const e = d.equity;
  const cur = d.account?.currency ?? "USD";
  const ci = k.expectancyCI;
  return (
    <Card title="Kennzahlen der Auswahl" subtitle={`Abgeschlossene Trades nach Gebühren · Beträge in ${cur} · Konto-Kennzahlen aus den Bewertungen der Episode`} actions={<ModeTag mode={d.account?.mode ?? "PAPER"} />}>
      <p className="mb-3 rounded-lg bg-surface-2 p-2 text-xs text-ink-2">{sampleCaveat(k.closed)}</p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-3 lg:grid-cols-4">
        <Stat label="Netto-Ergebnis" value={<span className={toneOf(k.net) === "critical" ? "text-critical" : ""}>{signed(k.net, cur)}</span>} hint={`${int(k.closed)} geschlossen, ${int(k.open)} offen (offene nicht enthalten)`} />
        <Stat
          label="Netto in CHF"
          value={k.netChf === null ? "Kurs fehlt" : signed(k.netChf, "CHF")}
          hint={k.chfMissing ? `ohne ${int(k.chfMissing)} Trade(s) ohne Kurs – Summe unvollständig` : "Einstiegs- und Ausstiegs-Fixing je Trade"}
        />
        <Stat
          label="Erwartungswert je Trade"
          value={rText(k.expectancyR)}
          hint={ci ? `95 %-Intervall ${rText(ci.ci[0])} bis ${rText(ci.ci[1])} (Bootstrap, ${int(ci.resamples)} Ziehungen, Seed ${ci.seed})` : `${TOO_FEW_TRADES} (unter ${MIN_TRADES_FOR_CI})`}
        />
        <Stat label="Profit-Faktor" value={k.profitFactor === null ? "—" : decimal(k.profitFactor, 2)} hint={k.losses === 0 ? "ohne Verlusttrade nicht definiert" : "Summe Gewinne ÷ |Summe Verluste|"} />
        <Stat label="Ø Gewinn / Ø Verlust" value={`${signed(k.avgWin)} / ${signed(k.avgLoss)}`} hint={`${int(k.wins)} mit Gewinn, ${int(k.losses)} mit Verlust – eine hohe Trefferquote beweist nichts`} />
        <Stat label="Rendite (zeitgewichtet)" value={pct(e?.return)} hint={e ? `Basis ${price(decimal(e.baseEquity), cur)}; ohne Ein-/Auszahlungen = einfache Rendite` : undefined} />
        <Stat label="Max. Drawdown" value={pct(e?.maxDrawdown)} hint={e?.snapshots ? `aus ${int(e.snapshots)} Bewertungen` : "keine Bewertung"} />
        <Stat label="Exposure" value={pct(e?.exposure, 0)} hint="Anteil der Bewertungen mit offener Position" />
        <Stat label="Umschlag" value={e?.turnover ? `${decimal(e.turnover, 2)} ×` : "—"} hint={`Volumen ${price(decimal(k.volume), cur)} ÷ Ø Eigenkapital`} />
        <Stat label="Ø Haltedauer" value={hours(k.avgHoldHours)} />
        <Stat label="Kostenanteil" value={pct(k.feeShareOfVolume, 3)} hint={`Gebühren ${price(decimal(k.fees), cur)} vom Volumen; ${k.feeShareOfGross ? `${pct(k.feeShareOfGross, 0)} des |Brutto-Ergebnisses|` : "Brutto 0"}`} />
        <Stat
          label="Sharpe-Kennzahl (annualisiert)"
          value={e?.sharpe ? decimal(e.sharpe, 2) : "—"}
          hint={e?.sharpe ? `Tagesrenditen × √365 (Crypto handelt täglich); ${int(e.dailyReturns)} Tage – kleine Stichprobe, nur grob` : `erst ab ${MIN_DAILY_RETURNS} Tagesrenditen (jetzt ${int(e?.dailyReturns ?? 0)})`}
        />
      </div>
      {d.equityScopeDiffers && <p className="mt-3 text-xs text-ink-2">Rendite, Drawdown, Exposure, Umschlag und Sharpe gelten für die ganze Episode, nicht nur für den Instrument-/Strategiefilter.</p>}
      <p className="mt-3 text-xs text-ink-2">
        Erwartungswert in R = Mittel von Netto ÷ geplantem Risiko je Trade. Das Intervall behandelt Trades als unabhängig (keine Cluster) und ist deshalb eher zu eng. Vergangene Ergebnisse sind keine Prognose.
      </p>
    </Card>
  );
}

function BaselineCard({ d }: { d: JournalData }) {
  const b = d.baselines;
  const cur = d.account?.currency ?? "USD";
  if (!b) return null;
  const rows: { name: string; net: string | null; ret: string | null; note: string }[] = [
    { name: "Strategie – abgeschlossene Trades", net: b.strategyNet, ret: null, note: "Netto nach Gebühren" },
    { name: "Strategie – Veränderung Eigenkapital", net: b.strategyEquityChange, ret: d.equity?.return ?? null, note: "inkl. offener Positionen" },
    { name: "Nicht handeln", net: "0", ret: "0", note: "reguläre Alternative mit Ergebnis 0" },
    {
      name: "Buy-and-Hold",
      net: b.buyHold.net,
      ret: b.buyHold.return,
      note: b.buyHold.legs.length ? `${b.buyHold.legs.map((l) => l.instrumentId).join(", ")} zu gleichen Teilen` : "keine gehandelten Instrumente",
    },
  ];
  return (
    <Card title="Vergleich mit Baselines" subtitle={`${date(b.start)} bis ${date(b.end)} · Kapital ${price(decimal(b.capital), cur)}`} actions={<ModeTag mode={d.account?.mode ?? "PAPER"} />}>
      <TableWrap>
        <table className="data">
          <thead>
            <tr>
              <th>Variante</th>
              <th className="num">Ergebnis ({cur})</th>
              <th className="num">Rendite</th>
              <th>Hinweis</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name}>
                <td className="font-medium">{r.name}</td>
                <td className="num">{r.net === null ? "—" : signed(r.net)}</td>
                <td className="num">{pct(r.ret)}</td>
                <td className="text-xs text-ink-2">{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
      {b.buyHold.missing.length > 0 && <p className="mt-2 text-xs text-critical">Buy-and-Hold nicht berechenbar: Kursdaten oder Gebührensatz fehlen für {b.buyHold.missing.join(", ")}.</p>}
      <p className="mt-3 text-xs text-ink-2">
        Buy-and-Hold: Kapital zu gleichen Teilen auf die gehandelten Instrumente, Kauf zum ersten {b.timeframe}-Kerzenschluss ab Beginn, Verkauf zum letzten Schluss bis Ende, je Seite derselbe effektive Gebührensatz wie die Fills der Auswahl
        {b.feeRate ? ` (${pct(b.feeRate, 3)})` : ""}. Exakt mit Dezimalzahlen gerechnet; Spread und Slippage nicht zusätzlich modelliert. Ein Long-only-System in einem steigenden Markt sieht ohne diesen Vergleich automatisch gut aus.
      </p>
    </Card>
  );
}

function TradeList({ d }: { d: JournalData }) {
  const cur = d.account?.currency ?? "USD";
  const columns: Column<JournalTrade>[] = [
    {
      label: "Trade",
      cell: (t) => (
        <span className="inline-flex flex-wrap items-center gap-1.5">
          <TradeLink id={t.id}>{t.instrumentId}</TradeLink>
          <TradeStatus status={t.status} />
        </span>
      ),
    },
    { label: "Eröffnet", cell: (t) => <span className="whitespace-nowrap tabular">{dateTime(t.openedAt)}</span> },
    { label: "Geschlossen", cell: (t) => <span className="whitespace-nowrap tabular">{t.closedAt ? dateTime(t.closedAt) : "—"}</span> },
    { label: `Netto (${cur})`, num: true, cell: (t) => <span className={`font-medium ${toneOf(t.net) === "critical" ? "text-critical" : ""}`}>{signed(t.net)}</span> },
    { label: "Netto (CHF)", num: true, cell: (t) => <ChfCell chf={t.chf} status={t.status} /> },
    { label: "R", num: true, cell: (t) => rText(t.r) },
    { label: "Gebühren", num: true, cell: (t) => decimal(t.fees) },
    { label: "Strategie", wide: true, cell: (t) => <span className="break-all font-mono text-xs">{t.strategyVersionId}</span> },
    { label: "Ausstiegsgrund", wide: true, cell: (t) => t.exitReason ?? (t.status === "OPEN" ? "—" : "nicht gespeichert") },
    {
      label: "Details",
      cell: (t) => (
        <Link href={`/journal/${encodeURIComponent(t.id)}`} className="text-accent">
          Warum? →
        </Link>
      ),
    },
  ];
  return <RecordList rows={d.trades} columns={columns} rowKey={(t) => t.id} />;
}

export default async function JournalPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const filter = parseJournalFilter(await searchParams);
  const r = await guard(() => loadJournal(filter));
  if (!r.ok) {
    return (
      <>
        <PageHeader title="Journal & Performance" subtitle="Warum hat er das gemacht, und was kam heraus?" />
        <SetupHint state={r} />
      </>
    );
  }
  const d = r.data;
  const scope = { account: d.account?.id, episode: d.episode?.number };
  const year = new Date(d.now).getUTCFullYear();
  return (
    <>
      <PageHeader
        title="Journal & Performance"
        subtitle="Warum hat er das gemacht, und was kam heraus? Je Auswahl genau ein Konto und eine Episode – Beträge verschiedener Konten oder Modi werden nie addiert. Zeiten in Europe/Zurich."
        actions={d.account ? <ModeTag mode={d.account.mode} /> : undefined}
      />
      {d.account?.mode !== "LIVE" && (
        <Banner tone="info">
          <strong>Alle Zahlen dieses Kontos sind simuliert.</strong> Paper-Fills stammen vom Simulator, nicht von einem Handelsplatz. Kennzahlen sind kein Gewinnversprechen und kein Qualitätsnachweis der Strategie.
        </Banner>
      )}

      {!d.account || !d.episode ? (
        <Empty>Noch kein Konto mit Episode vorhanden. Unter «Paper-Lab» lässt sich ein virtuelles Konto anlegen; danach erscheinen hier seine Trades.</Empty>
      ) : (
        <div className="space-y-4">
          <Card title="Auswahl">
            <Filters d={d} />
          </Card>

          <Kpis d={d} />
          <BaselineCard d={d} />

          <Card
            title="Trades"
            subtitle={`Neueste zuerst · ${int(d.trades.length)}${d.truncated ? " (gekürzt auf die neuesten 500)" : ""} · CHF mit dem Fixing am oder vor dem Ein-/Ausstiegstag (höchstens ${FX_MAX_AGE_DAYS} Tage alt), Kurs beim Darüberfahren`}
            actions={<ModeTag mode={d.account.mode} />}
          >
            {d.trades.length ? <TradeList d={d} /> : <Empty>Keine Trades in dieser Auswahl.</Empty>}
            {d.fxSources.length > 0 && <p className="mt-3 text-xs text-ink-2">Kursquelle: {d.fxSources.join(", ")}.</p>}
          </Card>

          <Card title="Exporte (CSV)" subtitle="Semikolon-getrennt, UTF-8 mit BOM für Excel, Dezimalpunkt, Zeiten UTC plus Europe/Zurich; jede Zeile mit Modus PAPER/LIVE">
            <div className="flex flex-wrap gap-2">
              {EXPORTS.map((x) => (
                <a key={x.kind} href={exportHref(x.kind, scope)} className="btn-ghost" download>
                  {x.label} · Episode {d.episode!.number}
                </a>
              ))}
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <a href={exportHref("year", { account: d.account.id, year })} className="btn-ghost" download>
                Jahresübersicht {year} · {d.account.name}
              </a>
              <a href={exportHref("year", { account: d.account.id })} className="btn-ghost" download>
                Jahresübersicht alle Jahre · {d.account.name}
              </a>
              <a href={exportHref("year", {})} className="btn-ghost" download>
                Jahresübersicht alle Konten
              </a>
            </div>
            <p className="mt-3 text-xs text-ink-2">
              <Badge>Hinweis</Badge> Grundlage für die eigene Auswertung – keine Steuerberatung; Steuerbarkeit hängt von persönlichen Umständen ab. Fehlende Kurse stehen als «Kurs fehlt» in der Datei.
            </p>
          </Card>

          <p className="text-xs text-ink-2">
            Alle Entscheide inklusive NO TRADE stehen unter{" "}
            <Link href="/signals?all=1" className="text-accent">
              Signale
            </Link>
            .
          </p>
        </div>
      )}
    </>
  );
}
