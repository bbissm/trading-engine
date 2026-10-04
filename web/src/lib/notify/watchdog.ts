import "server-only";
import { and, eq, ne, sql } from "drizzle-orm";
import { db } from "@/db/client";
import { alertDeliveries, alerts, heartbeats, settings } from "@/db/schema";
import { ago, dateTime } from "@/lib/format";
import { pushoverAcknowledged, pushoverConfigured, sendPushover, sendTelegram, telegramConfigured, type Fetch, type SendResult } from "./senders";

/**
 * Vercel watchdog (second, independent monitoring path, docs/06 4.3): runs every 5 minutes in the web project.
 * If the engine heartbeat is older than 5 minutes it alerts directly via Telegram and Pushover — at most every
 * 30 minutes (state in `setting` key `watchdog.last_alert`) — and keeps one alert row open. A Pushover confirmation
 * on the device stops the repetition for this outage. A fresh heartbeat resolves the row and resets the state.
 *
 * The alert row is created with escalation_level 1 so the engine never re-sends it once it is running again
 * (by then the watchdog resolves it).
 */

export const WATCHDOG_KEY = "watchdog.last_alert";
export const WATCHDOG_DEDUP = "watchdog:engine-silent";
export const ENGINE_STALE_MS = 5 * 60_000;
export const RESEND_MS = 30 * 60_000;
const PUSHOVER_EXPIRE_S = 1800;

interface WatchdogState {
  at: string | null;
  receipt: string | null;
  acknowledged: boolean;
}

export interface WatchdogResult {
  stale: boolean;
  ageMs: number | null;
  sent: boolean;
  reason: string;
}

type Env = Record<string, string | undefined>;

const stateOf = (v: unknown): WatchdogState => {
  const s = (v ?? {}) as Partial<WatchdogState>;
  return { at: typeof s.at === "string" ? s.at : null, receipt: typeof s.receipt === "string" ? s.receipt : null, acknowledged: s.acknowledged === true };
};

async function saveState(state: WatchdogState, now: Date) {
  const value = JSON.stringify(state);
  await db().execute(sql`
    insert into "setting" ("key", "value", "updated_at", "updated_by") values (${WATCHDOG_KEY}, ${value}::jsonb, ${now.toISOString()}::timestamptz, 'watchdog')
    on conflict ("key") do update set "value" = excluded."value", "updated_at" = excluded."updated_at", "updated_by" = excluded."updated_by"
  `);
}

export async function runWatchdog(nowMs = Date.now(), env: Env = process.env, f: Fetch = fetch): Promise<WatchdogResult> {
  const now = new Date(nowMs);
  const [hb] = await db().select().from(heartbeats).where(eq(heartbeats.service, "engine"));
  const ageMs = hb ? nowMs - hb.lastSeen.getTime() : null;
  const [setting] = await db().select().from(settings).where(eq(settings.key, WATCHDOG_KEY));
  const state = stateOf(setting?.value);

  if (ageMs !== null && ageMs <= ENGINE_STALE_MS) {
    await db().update(alerts).set({ status: "RESOLVED", resolvedAt: now, updatedAt: now, nextEscalationAt: null }).where(and(eq(alerts.dedupKey, WATCHDOG_DEDUP), ne(alerts.status, "RESOLVED")));
    if (state.at) await saveState({ at: null, receipt: null, acknowledged: false }, now);
    return { stale: false, ageMs, sent: false, reason: "Engine meldet sich" };
  }

  const since = hb ? `Letztes Lebenszeichen ${ago(hb.lastSeen, nowMs)} (${dateTime(hb.lastSeen)}).` : "Noch nie ein Lebenszeichen gespeichert.";
  const body =
    `${since} Paper-Autopilot und Zustellung der Engine-Meldungen ruhen, bis die Engine wieder läuft; verpasste Kerzen werden danach ` +
    "nachgeholt (Stops und Ausstiege), verpasste Signale nicht. Meldung vom Vercel-Watchdog (zweiter, unabhängiger Überwachungsweg).";
  const title = "Engine meldet sich nicht";
  const [row] = await db()
    .insert(alerts)
    .values({ createdAt: now, updatedAt: now, level: "CRITICAL", mode: "SYSTEM", kind: "ENGINE_SILENT", dedupKey: WATCHDOG_DEDUP, title, body, escalationLevel: 1, data: { source: "watchdog" } })
    .onConflictDoUpdate({
      target: alerts.dedupKey,
      targetWhere: sql`${alerts.status} <> 'RESOLVED'`,
      set: { occurrences: sql`${alerts.occurrences} + 1`, updatedAt: now, body },
    })
    .returning({ id: alerts.id, status: alerts.status });

  if (state.acknowledged) return { stale: true, ageMs, sent: false, reason: "Auf dem Gerät bestätigt – keine Wiederholung bis zur Erholung" };
  if (state.receipt && (await pushoverAcknowledged(state.receipt, env, f))) {
    await saveState({ ...state, acknowledged: true }, now);
    await db().update(alerts).set({ status: "ACKNOWLEDGED", acknowledgedAt: now, acknowledgedBy: "pushover", updatedAt: now }).where(and(eq(alerts.id, row.id), eq(alerts.status, "OPEN")));
    return { stale: true, ageMs, sent: false, reason: "Pushover-Quittung erhalten" };
  }
  if (row.status !== "OPEN") return { stale: true, ageMs, sent: false, reason: "Meldung bereits bestätigt" };
  if (state.at && nowMs - new Date(state.at).getTime() < RESEND_MS) return { stale: true, ageMs, sent: false, reason: "Zuletzt vor weniger als 30 min gesendet" };

  const text = `[SYSTEM] ${title}\n\n${body}`;
  const results: { channel: string; r: SendResult | null }[] = [
    { channel: "TELEGRAM", r: telegramConfigured(env) ? await sendTelegram(text, {}, env, f) : null },
    { channel: "PUSHOVER", r: pushoverConfigured(env) ? await sendPushover(`[SYSTEM] ${title}`, body, { priority: 2, retry: 60, expire: PUSHOVER_EXPIRE_S }, env, f) : null },
  ];
  await db()
    .insert(alertDeliveries)
    .values(results.map(({ channel, r }) => ({ alertId: row.id, channel, at: now, status: r === null ? "SKIPPED_DISABLED" : r.ok ? "SENT" : "FAILED", providerRef: r?.ref ?? null, error: r === null ? "Nicht eingerichtet (Web-App)" : (r.error ?? null) })));
  const sent = results.some(({ r }) => r?.ok);
  if (sent) await db().update(alerts).set({ lastSentAt: now }).where(eq(alerts.id, row.id));
  const receipt = results.find((x) => x.channel === "PUSHOVER")?.r?.ref ?? null;
  await saveState({ at: now.toISOString(), receipt, acknowledged: false }, now);
  return { stale: true, ageMs, sent, reason: sent ? "Alarm gesendet" : "Kein Kanal hat angenommen (oder keiner eingerichtet)" };
}
