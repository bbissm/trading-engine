import Link from "next/link";
import { liveState, type Check } from "@/app/live/model";
import type { LiveOverview, LiveSnapshot } from "@/lib/data/live";
import { ago, dateTime, decimal, price } from "@/lib/format";
import { ORDER_ROLE, ORDER_SIDE, ORDER_TYPE, orderState, FILL_SIDE } from "@/lib/paper";
import { ModeBadge } from "./mode-badge";
import { RecordList, type Column } from "./paper";
import { Badge, Empty } from "./ui";

/**
 * Presentational pieces of the live (real money) views. Every block carries the LIVE badge; live figures are shown
 * per account and never added to paper figures.
 */

export function LiveStateBadge({ state }: { state: string | null | undefined }) {
  const s = liveState(state);
  return (
    <Badge tone={s.tone} title={state ?? undefined}>
      <span aria-hidden>{s.icon}</span>
      {s.label}
    </Badge>
  );
}

const CHECK_ICON: Record<Check["status"], string> = { ok: "✓", missing: "✕", warning: "!" };
const CHECK_WORD: Record<Check["status"], string> = { ok: "erfüllt", missing: "fehlt", warning: "Hinweis" };

/** Precondition checklist (docs/02, 3.2): every point with its concrete current status. */
export function PreconditionList({ checks }: { checks: Check[] }) {
  return (
    <ul className="divide-y divide-[var(--grid)]" aria-label="Voraussetzungen für Live">
      {checks.map((c) => (
        <li key={c.key} className="flex gap-3 py-2 first:pt-0 last:pb-0">
          <span aria-hidden className={`mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-bold ${c.status === "ok" ? "bg-[color-mix(in_oklab,var(--good)_18%,transparent)] text-good" : "bg-[color-mix(in_oklab,var(--critical)_15%,transparent)] text-critical"}`}>
            {CHECK_ICON[c.status]}
          </span>
          <div className="min-w-0">
            <div className="text-sm font-medium">
              {c.label} <span className="sr-only">– {CHECK_WORD[c.status]}</span>
              {!c.blocking && c.status !== "ok" && <span className="text-xs font-normal text-ink-2"> (im Mandat unten)</span>}
            </div>
            <div className="break-words text-xs text-ink-2">{c.detail}</div>
          </div>
        </li>
      ))}
    </ul>
  );
}

/** How the Kraken key is created and stored – never in the browser, never in this app. */
export function KeyInstructions() {
  return (
    <div className="space-y-3 text-sm">
      <ol className="list-decimal space-y-2 pl-5">
        <li>
          Bei Kraken unter <span className="font-medium">Settings → API</span> einen neuen Schlüssel nur für TradingEngine erstellen. Rechte <span className="font-medium">nur</span>:
          Query Funds · Query Open Orders &amp; Trades · Query Closed Orders &amp; Trades · Create &amp; Modify Orders · Cancel/Close Orders · Query Ledger Entries.
          <span className="font-medium"> Nicht</span> anwählen: Withdraw Funds, Deposit Funds, Export Data.
        </li>
        <li>
          Schlüssel und Secret auf dem Vercel-Projekt <span className="font-mono text-xs">trading-engine-worker</span> als Umgebungsvariablen <span className="font-mono text-xs">KRAKEN_API_KEY</span> und{" "}
          <span className="font-mono text-xs">KRAKEN_API_SECRET</span> (Typ «Sensitive», nur Production) hinterlegen. Nie hier im Browser eingeben, nie in Chat oder Repository.
        </li>
        <li>
          Erst wenn du bereit bist: <span className="font-mono text-xs">LIVE_TRADING_ENABLED=true</span> auf dem Worker-Projekt (und, damit diese Seite den Schalter anzeigt, hier im Web-Projekt) setzen und neu ausrollen.
          Ohne diesen Schalter antwortet die Live-Funktion nur mit «disabled».
        </li>
        <li>«Kraken-Konto verbinden» unten: die Live-Funktion prüft Lese- und Handelsrecht (Trockenlauf mit <span className="font-mono text-xs">validate</span>) und dass das Auszahlungsrecht fehlt.</li>
      </ol>
      <p className="rounded-lg border border-line bg-surface-2 p-3 text-xs">
        <span className="font-semibold">IP-Allowlist ehrlich:</span> Kraken erlaubt, einen Schlüssel auf feste IP-Adressen zu beschränken. Vercel-Funktionen haben wechselnde Ausgangs-IPs, deshalb ist das hier
        nicht möglich. Folge: Wer Schlüssel und Secret erbeutet, kann von überall damit handeln (nicht auszahlen, weil das Recht fehlt). Schutz bleibt: Schlüssel nur als «Sensitive» auf dem Worker, eigener
        Schlüssel nur für TradingEngine, bei Verdacht sofort bei Kraken löschen. Eine feste IP gäbe es erst mit einem eigenen Server (offener Punkt aus docs/11, E-1).
      </p>
      <p className="text-xs text-ink-2">
        Kraken bietet keine Abfrage der Rechte eines Schlüssels. Geprüft wird darum indirekt: Die Abfrage der Auszahlungsmethoden (liest nur, bewegt kein Geld) muss mit «Permission denied» scheitern. Ist das
        Ergebnis unklar, verlangt die Verbindung deine ausdrückliche Bestätigung mit Step-up.
      </p>
    </div>
  );
}

type Position = LiveOverview["positions"][number];
type Order = LiveOverview["orders"][number];
type FillRow = LiveOverview["fills"][number];

export function LivePositions({ rows, snapshot }: { rows: Position[]; snapshot: LiveSnapshot | null }) {
  const foreign = Object.entries(snapshot?.foreign ?? {});
  const columns: Column<Position>[] = [
    { label: "Instrument", cell: (p) => <span className="font-medium">{p.instrumentId}</span> },
    { label: "Strategieversion", wide: true, cell: (p) => <span className="break-all font-mono text-xs">{p.strategyVersionId}</span> },
    { label: "Menge", num: true, cell: (p) => decimal(p.qty, 0) },
    { label: "Ø Einstieg", num: true, cell: (p) => price(p.avgEntry, "USD") },
    { label: "Stop bei Kraken", num: true, cell: (p) => price(p.currentStop, "USD") },
    { label: "Geplantes Risiko", num: true, cell: (p) => price(p.plannedRisk, "USD") },
    { label: "Ausstieg angefordert", wide: true, cell: (p) => p.exitReason ?? "—" },
    { label: "Eröffnet", cell: (p) => <span className="whitespace-nowrap tabular">{dateTime(p.openedAt)}</span> },
  ];
  return (
    <div className="space-y-3">
      {rows.length ? <RecordList rows={rows} columns={columns} rowKey={(p) => p.id} /> : <Empty>Keine verwalteten Live-Positionen.</Empty>}
      {foreign.length > 0 && (
        <div className="rounded-lg border border-line p-3">
          <div className="text-sm font-semibold">Fremd – nicht verwaltet</div>
          <p className="text-xs text-ink-2">Bestände bei Kraken, die nicht aus TradingEngine-Orders stammen: angezeigt und im Budget/in der Konzentration als belegt gezählt, nie gehandelt.</p>
          <ul className="mt-2 text-sm">
            {foreign.map(([instrumentId, qty]) => (
              <li key={instrumentId} className="tabular">
                {instrumentId}: {decimal(qty, 0)}
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-xs text-ink-2">
        Auslöse- und Kontrollregel, kein garantierter Maximalverlust. Kurslücken, Handelsunterbrüche und fehlende Liquidität können die Ausführung verschlechtern oder verhindern. Gewinnziel wird von
        TradingEngine überwacht, nicht von Kraken. Bei Ausfall bleibt der Stop-Loss bei Kraken aktiv, das Ziel nicht.
      </p>
    </div>
  );
}

export function LiveOrders({ rows }: { rows: Order[] }) {
  if (!rows.length) return <Empty>Keine offenen Live-Orders.</Empty>;
  const columns: Column<Order>[] = [
    { label: "Instrument", cell: (o) => <span className="font-medium">{o.instrumentId}</span> },
    { label: "Rolle", cell: (o) => `${ORDER_ROLE[o.role] ?? o.role} · ${ORDER_SIDE[o.side] ?? o.side} ${ORDER_TYPE[o.type] ?? o.type}` },
    { label: "Menge", num: true, cell: (o) => decimal(o.qty, 0) },
    { label: "Preis", num: true, cell: (o) => price(o.limitPrice ?? o.stopPrice, "USD") },
    {
      label: "Zustand",
      wide: true,
      cell: (o) => {
        const s = orderState(o.state, decimal(o.filled, 0), decimal(o.qty, 0));
        return <Badge tone={s.tone}>{o.state === "ACCEPTED" ? "von Kraken angenommen" : s.label}</Badge>;
      },
    },
    { label: "Client-Order-ID", wide: true, cell: (o) => <span className="break-all font-mono text-xs">{o.id}</span> },
  ];
  return <RecordList rows={rows} columns={columns} rowKey={(o) => o.id} />;
}

export function LiveFills({ rows }: { rows: FillRow[] }) {
  if (!rows.length) return <Empty>Noch keine echten Ausführungen.</Empty>;
  const columns: Column<FillRow>[] = [
    { label: "Zeit", cell: (f) => <span className="whitespace-nowrap tabular">{dateTime(f.time)}</span> },
    { label: "Instrument", cell: (f) => f.instrumentId },
    { label: "Art", cell: (f) => `${FILL_SIDE[f.side] ?? f.side} (${ORDER_ROLE[f.role] ?? f.role})` },
    { label: "Menge", num: true, cell: (f) => decimal(f.qty, 0) },
    { label: "Preis", num: true, cell: (f) => price(f.price, "USD") },
    { label: "Gebühr", num: true, cell: (f) => price(f.fee, f.feeCurrency) },
  ];
  return <RecordList rows={rows} columns={columns} rowKey={(f) => f.id} />;
}

const RECON_TONE: Record<string, "good" | "warning" | "critical"> = { OK: "good", DIFF: "warning", ERROR: "critical" };

export function ReconciliationList({ rows, now }: { rows: LiveOverview["reconciliations"]; now: number }) {
  if (!rows.length) return <Empty>Noch kein Abgleich mit Kraken.</Empty>;
  return (
    <ul className="divide-y divide-[var(--grid)]">
      {rows.map((r) => {
        const notes = r.diffs.filter((d) => d.kind !== "SNAPSHOT");
        return (
          <li key={r.id} className="py-2 first:pt-0 last:pb-0">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge tone={RECON_TONE[r.status] ?? "neutral"}>{r.status}</Badge>
              <span className="text-xs text-ink-2" title={dateTime(r.at)}>
                {ago(r.at, now)}
              </span>
            </div>
            {notes.length > 0 && (
              <ul className="mt-1 space-y-0.5 text-xs text-ink-2">
                {notes.map((d, i) => (
                  <li key={i} className="break-words">
                    {String(d.kind)}
                    {d.instrument ? ` · ${String(d.instrument)}` : ""}
                    {d.qty ? ` · ${String(d.qty)}` : ""}
                    {d.note ? ` – ${String(d.note)}` : ""}
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Live card for the autopilot page and the portfolio LIVE section: state, holdings, link to the assistant. */
export function LiveSummary({ summary, compact = false }: { summary: { account: LiveOverview["account"]; autopilot: LiveOverview["autopilot"]; positions: Position[]; orders: Order[]; snapshot: LiveSnapshot | null; flag: boolean; lastRecon: { status: string; at: Date } | null }; compact?: boolean }) {
  const s = liveState(summary.account ? (summary.autopilot?.state ?? "SETUP") : null);
  return (
    <section className="rounded-xl border-2 border-mode-live bg-surface p-4" aria-label="Live-Autopilot">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">{compact ? "Echter Bestand" : "Live-Autopilot"}</h2>
        <ModeBadge mode="LIVE" />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <LiveStateBadge state={summary.account ? (summary.autopilot?.state ?? "SETUP") : null} />
        <Badge tone={summary.flag ? "critical" : "neutral"}>LIVE_TRADING_ENABLED: {summary.flag ? "true" : "false"}</Badge>
      </div>
      <p className="mt-2 text-sm">{s.meaning}</p>
      {summary.autopilot?.reason && <p className="mt-1 break-words text-xs text-ink-2">Letzte Zustandsänderung: {summary.autopilot.reason}</p>}
      {summary.account && (
        <p className="mt-2 text-xs text-ink-2">
          {summary.positions.length} verwaltete {summary.positions.length === 1 ? "Position" : "Positionen"}, {summary.orders.length} offene {summary.orders.length === 1 ? "Order" : "Orders"} bei Kraken
          {summary.lastRecon ? ` · letzter Abgleich ${summary.lastRecon.status}` : ""}. Echtgeld – nie mit Paper summiert.
        </p>
      )}
      {compact && summary.positions.length > 0 && (
        <div className="mt-3">
          <LivePositions rows={summary.positions} snapshot={summary.snapshot} />
        </div>
      )}
      <p className="mt-3 text-sm">
        <Link href="/live" className="text-accent">
          {summary.account ? "Live-Assistent und Bedienung →" : "Live-Assistent: Voraussetzungen ansehen →"}
        </Link>
      </p>
    </section>
  );
}
