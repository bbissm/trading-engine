import type { ReactNode } from "react";
import type { AlertWithDeliveries, ChannelRow, DeliveryRow } from "@/lib/data/alerts";
import { ago, dateTime } from "@/lib/format";
import { CHANNEL_LABEL } from "@/lib/notify/env";
import { ALERT_STATUS, deliveryOf, levelOf } from "@/lib/notify/labels";
import { ModeBadge } from "./mode-badge";
import { Badge, Empty, TableWrap } from "./ui";

/** PAPER / LIVE / FORSCHUNG with the regular mode badge, SYSTEM as a plain text badge. */
export function AlertModeBadge({ mode }: { mode: string }) {
  if (mode === "PAPER" || mode === "LIVE" || mode === "RESEARCH") return <ModeBadge mode={mode} />;
  return (
    <span className="inline-flex items-center whitespace-nowrap rounded-md border border-line px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-ink-2">
      {mode === "SYSTEM" ? "SYSTEM" : mode}
    </span>
  );
}

export function LevelBadge({ level }: { level: string }) {
  const l = levelOf(level);
  return (
    <Badge tone={l.tone}>
      <span aria-hidden>{l.icon}</span>
      {l.label}
    </Badge>
  );
}

/** Latest attempt per channel, plus the full attempt list on demand. */
function Deliveries({ rows, now }: { rows: DeliveryRow[]; now: number }) {
  if (!rows.length) return <p className="text-xs text-ink-2">Noch nicht zugestellt – die Engine verarbeitet Meldungen im Minutentakt.</p>;
  const channels = [...new Set(rows.map((d) => d.channel))];
  return (
    <div className="space-y-1.5">
      <ul className="flex flex-wrap gap-1.5" aria-label="Zustellung je Kanal">
        {channels.map((ch) => {
          const mine = rows.filter((d) => d.channel === ch);
          const last = mine[mine.length - 1];
          const s = deliveryOf(last.status, last.providerRef);
          return (
            <li key={ch}>
              <Badge tone={s.tone} title={`${s.hint}${last.error ? ` – ${last.error}` : ""}`}>
                {CHANNEL_LABEL[ch] ?? ch}: {s.label}
                {mine.length > 1 && <span className="font-normal opacity-75">· {mine.length}×</span>}
              </Badge>
            </li>
          );
        })}
      </ul>
      <details className="text-xs">
        <summary className="cursor-pointer text-ink-2">Alle Zustellversuche ({rows.length})</summary>
        <ul className="mt-1.5 space-y-0.5">
          {rows.map((d) => {
            const s = deliveryOf(d.status, d.providerRef);
            return (
              <li key={d.id} className="break-words">
                <span className="tabular text-ink-2" title={dateTime(d.at)}>
                  {ago(d.at, now)}
                </span>{" "}
                · {CHANNEL_LABEL[d.channel] ?? d.channel} · {s.label}
                {d.error && <span className="text-ink-2"> – {d.error}</span>}
              </li>
            );
          })}
        </ul>
      </details>
    </div>
  );
}

export function AlertItem({ alert, now, action }: { alert: AlertWithDeliveries; now: number; action?: ReactNode }) {
  const status = ALERT_STATUS[alert.status] ?? { label: alert.status, tone: "neutral" as const };
  return (
    <li className="space-y-2 py-3 first:pt-0 last:pb-0" data-level={alert.level}>
      <div className="flex flex-wrap items-center gap-1.5">
        <LevelBadge level={alert.level} />
        <AlertModeBadge mode={alert.mode} />
        {alert.status !== "OPEN" && <Badge tone={status.tone}>{status.label}</Badge>}
        {alert.occurrences > 1 && (
          <Badge title="So oft wurde derselbe Zustand festgestellt (keine neue Meldung)">
            {alert.occurrences}× festgestellt
          </Badge>
        )}
        <span className="ml-auto text-xs text-ink-2 tabular" title={`erstellt ${dateTime(alert.createdAt)}, aktualisiert ${dateTime(alert.updatedAt)}`}>
          {ago(alert.updatedAt, now)}
        </span>
      </div>
      <div>
        <h3 className="break-words text-sm font-semibold">{alert.title}</h3>
        {alert.body && <p className="mt-0.5 whitespace-pre-line break-words text-sm text-ink-2">{alert.body}</p>}
      </div>
      <Deliveries rows={alert.deliveries} now={now} />
      {alert.status === "ACKNOWLEDGED" && (
        <p className="text-xs text-ink-2">
          Bestätigt {dateTime(alert.acknowledgedAt)} durch {alert.acknowledgedBy ?? "—"}. Bestätigen genehmigt keinen Trade.
        </p>
      )}
      {alert.status === "RESOLVED" && alert.resolvedAt && <p className="text-xs text-ink-2">Erledigt {dateTime(alert.resolvedAt)}.</p>}
      {action}
    </li>
  );
}

export function AlertList({ alerts, now, empty, action }: { alerts: AlertWithDeliveries[]; now: number; empty: string; action?: (a: AlertWithDeliveries) => ReactNode }) {
  if (!alerts.length) return <Empty>{empty}</Empty>;
  return (
    <ul className="divide-y divide-[var(--grid)]">
      {alerts.map((a) => (
        <AlertItem key={a.id} alert={a} now={now} action={action?.(a)} />
      ))}
    </ul>
  );
}

export function ChannelTable({ rows, enabled, now }: { rows: ChannelRow[]; enabled: Record<string, boolean>; now: number }) {
  const order = ["IN_APP", "TELEGRAM", "PUSHOVER", "EMAIL"];
  const sorted = [...rows].sort((a, b) => order.indexOf(a.channel) - order.indexOf(b.channel));
  if (!sorted.length) return <Empty>Die Engine hat die Kanäle noch nicht geprüft (Prüfung alle 15 Minuten).</Empty>;
  return (
    <TableWrap>
      <table className="data">
        <thead>
          <tr>
            <th>Kanal</th>
            <th>Zustand</th>
            <th>Geprüft</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((c) => {
            const off = c.channel in enabled && !enabled[c.channel];
            const tone = !c.configured ? "neutral" : off ? "neutral" : c.ok ? "good" : "critical";
            const label = !c.configured ? "Nicht eingerichtet" : off ? "Abgeschaltet" : c.ok ? "Bereit" : "Fehler";
            return (
              <tr key={c.channel}>
                <td className="min-w-0">
                  <div className="whitespace-nowrap font-medium">{CHANNEL_LABEL[c.channel] ?? c.channel}</div>
                  <div className="break-words text-xs text-ink-2">{c.detail ?? "—"}</div>
                </td>
                <td>
                  <Badge tone={tone}>{label}</Badge>
                </td>
                <td className="whitespace-nowrap" title={dateTime(c.lastCheckAt)}>
                  {ago(c.lastCheckAt, now)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableWrap>
  );
}
