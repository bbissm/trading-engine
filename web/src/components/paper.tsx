import Link from "next/link";
import type { ReactNode } from "react";
import type { CommandRow, OpenPosition, PaperAccountSummary } from "@/lib/data/paper";
import { ago, dateTime, decimal, price } from "@/lib/format";
import { autopilotState, COMMAND_LABEL, COMMAND_STATUS, rejectionReason } from "@/lib/paper";
import { PaperControls, type ControlCommand } from "./paper-controls";
import { instrumentHref } from "./signal-list";
import { Badge, Empty, TableWrap } from "./ui";

export const paperHref = (id: string, episode?: number) => `/paper/${encodeURIComponent(id)}${episode ? `?episode=${episode}` : ""}`;

/** Autopilot state as a word badge with icon — colour is never the only carrier. */
export function AutopilotBadge({ state }: { state: string | null }) {
  const s = autopilotState(state);
  return (
    <Badge tone={s.tone} title={state ?? undefined}>
      <span aria-hidden>{s.icon}</span>
      {s.label}
    </Badge>
  );
}

/** Marks figures and executions that come from the simulator. */
export const Simulated = () => <Badge title="Vom Simulator erzeugt – keine echte Ausführung">simuliert</Badge>;

export function CommandStatusBadge({ status }: { status: string }) {
  const s = COMMAND_STATUS[status];
  return <Badge tone={s?.tone ?? "neutral"}>{s?.label ?? status}</Badge>;
}

/** Compact list of PAPER_* commands with status and rejection reason. */
export function PaperCommandList({ rows, now }: { rows: CommandRow[]; now: number }) {
  if (!rows.length) return <Empty>Noch kein Paper-Befehl gesendet.</Empty>;
  return (
    <ul className="divide-y divide-[var(--grid)]">
      {rows.map((c) => {
        const reason = rejectionReason(c.result);
        const created = c.status === "DONE" && typeof c.result?.account_id === "string" ? c.result.account_id : null;
        const name = typeof c.params?.name === "string" ? c.params.name : null;
        return (
          <li key={c.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 first:pt-0 last:pb-0">
            <div className="min-w-0 flex-1 basis-48">
              <div className="text-sm font-medium">
                {COMMAND_LABEL[c.type] ?? c.type}
                <span className="font-normal text-ink-2"> · {c.target ?? (name ? `«${name}»` : "neues Konto")}</span>
              </div>
              <div className="text-xs text-ink-2">
                Nr. {c.id} · <span title={dateTime(c.issuedAt)}>{ago(c.issuedAt, now)}</span> · {c.issuedBy}
                {c.handledAt && <> · quittiert {dateTime(c.handledAt)}</>}
              </div>
              {reason && <div className="break-words text-xs text-critical">Grund: {reason}</div>}
              {created && (
                <div className="text-xs text-ink-2">
                  Konto{" "}
                  <Link href={paperHref(created)} className="text-accent">
                    {created}
                  </Link>{" "}
                  angelegt.
                </div>
              )}
            </div>
            <CommandStatusBadge status={c.status} />
          </li>
        );
      })}
    </ul>
  );
}

/** Serialisable command rows of one account for the client-side controls. */
export function controlCommands(rows: CommandRow[], accountId: string): ControlCommand[] {
  return rows.filter((c) => c.target === accountId).map((c) => ({ id: c.id, type: c.type, status: c.status, reason: rejectionReason(c.result), issued: dateTime(c.issuedAt) }));
}

/** Control buttons of one paper account, wired to its current state and holdings. */
export function AccountControls({ account, commands }: { account: PaperAccountSummary; commands: CommandRow[] }) {
  if (!account.episode) return <Empty>Für dieses Konto ist keine Episode gespeichert.</Empty>;
  return (
    <PaperControls
      accountId={account.id}
      state={account.state}
      openPositions={account.openPositions}
      workingOrders={account.workingOrders}
      workingEntryOrders={account.workingEntryOrders}
      episodeNumber={account.episode.number}
      startCash={decimal(account.episode.startCash, 0).replace(/’/g, "")}
      commands={controlCommands(commands, account.id)}
    />
  );
}

export interface Column<T> {
  label: string;
  cell: (row: T) => ReactNode;
  num?: boolean;
  /** Phone card: use the full width (long badges, free text). */
  wide?: boolean;
}

/**
 * Dense records: full table from md upwards, card list on phones (docs/02, section 1).
 * The first column is the card title.
 */
export function RecordList<T>({ rows, columns, rowKey }: { rows: T[]; columns: Column<T>[]; rowKey: (row: T) => string }) {
  const [head, ...rest] = columns;
  return (
    <>
      <ul className="space-y-2 md:hidden">
        {rows.map((r) => (
          <li key={rowKey(r)} className="rounded-lg border border-line p-3">
            <div className="text-sm font-medium">{head.cell(r)}</div>
            <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-2">
              {rest.map((c) => (
                <div key={c.label} className={`min-w-0 ${c.wide ? "col-span-2" : ""}`}>
                  <dt className="text-[11px] font-medium uppercase tracking-wide text-muted">{c.label}</dt>
                  <dd className={`break-words text-sm ${c.num ? "tabular" : ""}`}>{c.cell(r)}</dd>
                </div>
              ))}
            </dl>
          </li>
        ))}
      </ul>
      <div className="hidden md:block">
        <TableWrap>
          <table className="data">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c.label} className={c.num ? "num" : undefined}>
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={rowKey(r)}>
                  {columns.map((c) => (
                    <td key={c.label} className={c.num ? "num" : undefined}>
                      {c.cell(r)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </div>
    </>
  );
}

/** Open positions of one paper account (simulated holdings). */
export function PositionList({ rows }: { rows: OpenPosition[] }) {
  const columns: Column<OpenPosition>[] = [
    {
      label: "Instrument",
      cell: (p) => (
        <Link href={instrumentHref(p.instrumentId, p.timeframe)} className="font-medium hover:underline">
          {p.instrumentId}
        </Link>
      ),
    },
    { label: "Strategieversion", wide: true, cell: (p) => <span className="break-all font-mono text-xs">{p.strategyVersionId}</span> },
    { label: "Menge", num: true, cell: (p) => decimal(p.qty, 0) },
    { label: "Ø Einstieg", num: true, cell: (p) => price(p.avgEntry, p.quoteCurrency) },
    { label: "Aktueller Stop", num: true, cell: (p) => price(p.currentStop, p.quoteCurrency) },
    { label: "Geplantes Risiko", num: true, cell: (p) => price(p.plannedRisk, p.quoteCurrency) },
    { label: "Kerzen gehalten", num: true, cell: (p) => p.barsHeld },
    { label: "Eröffnet", cell: (p) => <span className="whitespace-nowrap tabular">{dateTime(p.openedAt)}</span> },
  ];
  return <RecordList rows={rows} columns={columns} rowKey={(p) => p.id} />;
}
