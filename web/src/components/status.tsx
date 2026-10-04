import type { FeedRow, HeartbeatState } from "@/lib/health";
import { SCHEMA_VERSION } from "@/db/schema";
import { ago, dateTime } from "@/lib/format";
import { Badge, Empty, TableWrap, type Tone } from "./ui";

const REGIME: Record<string, { label: string; tone: Tone; icon: string }> = {
  UP: { label: "Aufwärts", tone: "good", icon: "↗" },
  DOWN: { label: "Abwärts", tone: "critical", icon: "↘" },
  SIDEWAYS: { label: "Seitwärts", tone: "neutral", icon: "→" },
  STRESS: { label: "Stress", tone: "warning", icon: "⚡" },
  UNKNOWN: { label: "Unbekannt", tone: "neutral", icon: "?" },
};
export const regimeLabel = (regime: string) => REGIME[regime]?.label ?? regime;

export function RegimeBadge({ regime, prefix }: { regime: string; prefix?: string }) {
  const r = REGIME[regime] ?? { label: regime, tone: "neutral" as Tone, icon: "·" };
  return (
    <Badge tone={r.tone} title={`Regime ${regime}`}>
      {prefix && <span className="font-normal opacity-80">{prefix}</span>}
      <span aria-hidden>{r.icon}</span>
      {r.label}
    </Badge>
  );
}

const ACTION: Record<string, { label: string; tone: Tone }> = {
  BUY: { label: "BUY", tone: "accent" },
  HOLD: { label: "HOLD", tone: "neutral" },
  REDUCE: { label: "REDUCE", tone: "warning" },
  EXIT: { label: "EXIT", tone: "warning" },
  NO_TRADE: { label: "NO TRADE", tone: "neutral" },
};

/** Strategy output, not an execution: BUY here never means "gekauft". */
export function ActionBadge({ action }: { action: string }) {
  const a = ACTION[action] ?? { label: action, tone: "neutral" as Tone };
  return <Badge tone={a.tone}>{a.label}</Badge>;
}

const FEED_TONE: Record<string, Tone> = { OK: "good", STALE: "warning", GAP: "warning", ERROR: "critical" };

export function FeedBadge({ status, prefix }: { status: string; prefix?: string }) {
  return (
    <Badge tone={FEED_TONE[status] ?? "neutral"}>
      {prefix && <span className="font-normal opacity-80">{prefix}</span>}
      {status === "OK" ? "✓ OK" : status}
    </Badge>
  );
}

export function OkBadge({ ok, okLabel = "OK", failLabel }: { ok: boolean; okLabel?: string; failLabel: string }) {
  return <Badge tone={ok ? "good" : "critical"}>{ok ? `✓ ${okLabel}` : `✕ ${failLabel}`}</Badge>;
}

/** Heartbeat rows: service, last seen (relative), OK if younger than 3 minutes, schema version vs. SCHEMA_VERSION. */
export function HeartbeatList({ rows, now, detailed = false }: { rows: HeartbeatState[]; now: number; detailed?: boolean }) {
  if (!rows.length) return <Empty>Kein Lebenszeichen der Engine. Solange kein Dienst läuft, entstehen keine Daten und keine Signale.</Empty>;
  return (
    <ul className="divide-y divide-[var(--grid)]">
      {rows.map((h) => (
        <li key={h.service} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 first:pt-0 last:pb-0">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium">{h.service}</div>
            <div className="text-xs text-ink-2">
              Zuletzt gesehen {ago(h.lastSeen, now)}
              {detailed && ` (${dateTime(h.lastSeen)})`}
            </div>
            {detailed && h.detail && Object.keys(h.detail).length > 0 && <div className="mt-0.5 break-all font-mono text-[11px] text-muted">{JSON.stringify(h.detail)}</div>}
          </div>
          <OkBadge ok={h.ok} failLabel="Keine Meldung" />
          <Badge tone={h.schemaOk ? "neutral" : "warning"} title={`Die Web-App erwartet Schema-Version ${SCHEMA_VERSION}`}>
            Schema v{h.schemaVersion}
            {!h.schemaOk && ` ≠ v${SCHEMA_VERSION}`}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

/** Feed rows: compact list (overview) or full table (operations). */
export function FeedList({ rows, now, detailed = false }: { rows: FeedRow[]; now: number; detailed?: boolean }) {
  if (!rows.length) return <Empty>Noch kein Datenstrom gemeldet.</Empty>;
  if (!detailed) {
    return (
      <ul className="divide-y divide-[var(--grid)]">
        {rows.map((f) => (
          <li key={`${f.feed}:${f.instrumentId}:${f.timeframe}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 first:pt-0 last:pb-0">
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">
                {f.instrumentId} <span className="font-normal text-ink-2">· {f.timeframe}</span>
              </div>
              <div className="text-xs text-ink-2">
                {f.feed} · letzte Kerze {f.lastCandleClose ? ago(f.lastCandleClose, now) : "—"}
              </div>
            </div>
            <FeedBadge status={f.status} />
          </li>
        ))}
      </ul>
    );
  }
  return (
    <TableWrap>
      <table className="data">
        <thead>
          <tr>
            <th>Instrument</th>
            <th>Zeitebene</th>
            <th>Quelle</th>
            <th>Status</th>
            <th>Letzte Kerze (Schluss)</th>
            <th>Zuletzt OK</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((f) => (
            <tr key={`${f.feed}:${f.instrumentId}:${f.timeframe}`}>
              <td className="whitespace-nowrap font-medium">{f.instrumentId}</td>
              <td>{f.timeframe}</td>
              <td>{f.feed}</td>
              <td>
                <FeedBadge status={f.status} />
              </td>
              <td className="whitespace-nowrap tabular">{dateTime(f.lastCandleClose)}</td>
              <td className="whitespace-nowrap">{ago(f.lastOkAt, now)}</td>
              <td className="text-ink-2">{f.detail ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}
