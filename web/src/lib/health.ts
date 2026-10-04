import { SCHEMA_VERSION } from "@/db/schema";
import { ago } from "@/lib/format";

/** A heartbeat older than this counts as stale (engine services write one every few seconds). */
export const HEARTBEAT_MAX_AGE_MS = 3 * 60_000;

export interface HeartbeatRow {
  service: string;
  lastSeen: Date;
  schemaVersion: number;
  detail: Record<string, unknown> | null;
}

export interface FeedRow {
  feed: string;
  instrumentId: string;
  timeframe: string;
  lastCandleClose: Date | null;
  lastOkAt: Date | null;
  status: string;
  detail: string | null;
  updatedAt: Date;
}

export interface HeartbeatState extends HeartbeatRow {
  ageMs: number;
  /** last seen less than HEARTBEAT_MAX_AGE_MS ago */
  ok: boolean;
  schemaOk: boolean;
}

export function heartbeatState(row: HeartbeatRow, now = Date.now()): HeartbeatState {
  const ageMs = now - row.lastSeen.getTime();
  return { ...row, ageMs, ok: ageMs < HEARTBEAT_MAX_AGE_MS, schemaOk: row.schemaVersion === SCHEMA_VERSION };
}

export interface AttentionItem {
  id: string;
  severity: "critical" | "warning";
  title: string;
  detail: string;
  href?: string;
}

/**
 * Row "Braucht Aufmerksamkeit" of the overview. For now: engine heartbeat missing/stale,
 * schema version mismatch (database or a service), feeds that are not OK.
 */
export function attentionItems(input: { heartbeats: HeartbeatRow[]; feeds: FeedRow[]; dbSchemaVersion: number | null }, now = Date.now()): AttentionItem[] {
  const items: AttentionItem[] = [];
  const href = "/operations";

  if (input.dbSchemaVersion !== SCHEMA_VERSION) {
    items.push({
      id: "schema:db",
      severity: "critical",
      title: "Schema-Version der Datenbank passt nicht",
      detail: input.dbSchemaVersion === null ? `Keine Version in schema_meta gefunden, erwartet ${SCHEMA_VERSION}. Migration ausführen (pnpm db:migrate).` : `Datenbank hat Version ${input.dbSchemaVersion}, die Web-App erwartet ${SCHEMA_VERSION}.`,
      href,
    });
  }

  if (!input.heartbeats.length) {
    items.push({ id: "heartbeat:none", severity: "critical", title: "Kein Engine-Heartbeat", detail: "Noch kein Dienst der Engine hat sich gemeldet. Es werden keine Daten geladen und keine Signale erzeugt.", href });
  }
  for (const h of input.heartbeats.map((r) => heartbeatState(r, now))) {
    if (!h.ok) {
      items.push({ id: `heartbeat:${h.service}`, severity: "critical", title: `Dienst «${h.service}» meldet sich nicht`, detail: `Letztes Lebenszeichen ${ago(h.lastSeen, now)}.`, href });
    }
    if (!h.schemaOk) {
      items.push({ id: `schema:${h.service}`, severity: "warning", title: `Dienst «${h.service}» läuft mit anderer Schema-Version`, detail: `Dienst meldet Version ${h.schemaVersion}, die Web-App erwartet ${SCHEMA_VERSION}.`, href });
    }
  }

  for (const f of input.feeds) {
    if (f.status === "OK") continue;
    items.push({
      id: `feed:${f.feed}:${f.instrumentId}:${f.timeframe}`,
      severity: f.status === "ERROR" ? "critical" : "warning",
      title: `Datenstrom ${f.instrumentId} ${f.timeframe}: ${f.status}`,
      detail: f.detail ?? `Quelle ${f.feed}, letzte Kerze ${f.lastCandleClose ? ago(f.lastCandleClose, now) : "unbekannt"}.`,
      href,
    });
  }

  // critical first, order within a severity stays stable
  return items.sort((a, b) => Number(b.severity === "critical") - Number(a.severity === "critical"));
}
