import "server-only";
import { and, asc, desc, eq, inArray, ne, sql } from "drizzle-orm";
import { z } from "zod";
import { db } from "@/db/client";
import { accounts, alertDeliveries, alerts, autopilots, channelStatus, commands, heartbeats, settings } from "@/db/schema";
import { missing, WEB_ENV } from "@/lib/notify/env";
import { enabledChannelsOf, HHMM, MODE_PREFIX, quietHoursOf, type QuietHours } from "@/lib/notify/labels";
import { autopilotState } from "@/lib/paper";
import { ago, dateTime } from "@/lib/format";

/**
 * Alerts («Meldungen»), their deliveries, channel status and notification settings. The web app only reads here
 * and writes commands (ALERT_ACK, NOTIFY_TEST, SETTINGS_SET, TELEGRAM_CALLBACK) for the engine — acknowledging
 * stops the escalation but never approves a trade.
 */

export type AlertRow = typeof alerts.$inferSelect;
export type DeliveryRow = typeof alertDeliveries.$inferSelect;
export type ChannelRow = typeof channelStatus.$inferSelect;
export type CommandRow = typeof commands.$inferSelect;
export interface AlertWithDeliveries extends AlertRow {
  deliveries: DeliveryRow[];
}

export const NOTIFY_COMMANDS = ["ALERT_ACK", "NOTIFY_TEST", "SETTINGS_SET"] as const;

export interface AlertsData {
  now: number;
  open: AlertWithDeliveries[];
  acknowledged: AlertWithDeliveries[];
  resolved: AlertWithDeliveries[];
  channels: ChannelRow[];
  quiet: QuietHours;
  enabled: Record<"TELEGRAM" | "PUSHOVER" | "EMAIL", boolean>;
  smsEnabled: boolean;
  lastTestSentAt: Date | null;
  lastTestAckAt: Date | null;
  /** Names (never values) of variables this web app is missing for webhook and watchdog. */
  webMissing: { webhook: string[]; watchdogTelegram: string[]; watchdogPushover: string[]; cron: string[] };
  /** ALERT_ACK commands still waiting for the engine, by alert id. */
  pendingAcks: number[];
  pendingCommands: CommandRow[];
}

async function withDeliveries(rows: AlertRow[]): Promise<AlertWithDeliveries[]> {
  if (!rows.length) return [];
  const ds = await db().select().from(alertDeliveries).where(inArray(alertDeliveries.alertId, rows.map((r) => r.id))).orderBy(asc(alertDeliveries.at), asc(alertDeliveries.id));
  return rows.map((r) => ({ ...r, deliveries: ds.filter((d) => d.alertId === r.id) }));
}

const LEVEL_ORDER = sql`array_position(array['CRITICAL','WARNING','SIGNAL','INFO']::text[], ${alerts.level})`;

export async function loadAlerts(now = Date.now(), env: Record<string, string | undefined> = process.env): Promise<AlertsData> {
  const [open, acked, resolved, channels, sets, pending] = await Promise.all([
    db().select().from(alerts).where(eq(alerts.status, "OPEN")).orderBy(LEVEL_ORDER, desc(alerts.updatedAt)).limit(100),
    db().select().from(alerts).where(eq(alerts.status, "ACKNOWLEDGED")).orderBy(desc(alerts.acknowledgedAt)).limit(30),
    db().select().from(alerts).where(eq(alerts.status, "RESOLVED")).orderBy(desc(alerts.resolvedAt)).limit(30),
    db().select().from(channelStatus).orderBy(asc(channelStatus.channel)),
    db().select().from(settings).where(inArray(settings.key, ["notify.quiet_hours", "notify.channels", "notify.sms_enabled"])),
    db().select().from(commands).where(and(inArray(commands.type, [...NOTIFY_COMMANDS]), eq(commands.status, "PENDING"))).orderBy(desc(commands.id)).limit(20),
  ]);
  const setting = (key: string) => sets.find((s) => s.key === key)?.value;
  const sent = channels.map((c) => c.lastTestSentAt).filter((d): d is Date => !!d);
  const lastTestSentAt = sent.length ? new Date(Math.max(...sent.map((d) => d.getTime()))) : null;
  const acks = channels.map((c) => c.lastTestAckAt).filter((d): d is Date => !!d);
  const lastTestAckAt = acks.length ? new Date(Math.max(...acks.map((d) => d.getTime()))) : null;
  const [o, a, r] = await Promise.all([withDeliveries(open), withDeliveries(acked), withDeliveries(resolved)]);
  return {
    now,
    open: o,
    acknowledged: a,
    resolved: r,
    channels,
    quiet: quietHoursOf(setting("notify.quiet_hours")),
    enabled: enabledChannelsOf(setting("notify.channels")),
    smsEnabled: setting("notify.sms_enabled") === true,
    lastTestSentAt,
    lastTestAckAt,
    webMissing: {
      webhook: missing(WEB_ENV.webhook, env),
      watchdogTelegram: missing(WEB_ENV.watchdogTelegram, env),
      watchdogPushover: missing(WEB_ENV.watchdogPushover, env),
      cron: missing(WEB_ENV.cron, env),
    },
    pendingAcks: pending.filter((c) => c.type === "ALERT_ACK").map((c) => Number((c.params as { alert_id?: unknown }).alert_id)),
    pendingCommands: pending,
  };
}

/** Open alerts by level, for the overview / navigation badge. */
export async function openAlertCounts(): Promise<Record<string, number>> {
  const rows = await db().select({ level: alerts.level, n: sql<number>`count(*)::int` }).from(alerts).where(eq(alerts.status, "OPEN")).groupBy(alerts.level);
  return Object.fromEntries(rows.map((r) => [r.level, r.n]));
}

// ───────────────────────── commands ─────────────────────────

const hhmm = z.string().regex(HHMM, "Zeit im Format HH:MM");
export const notifyCommandInput = z.union([
  z.object({ type: z.literal("ALERT_ACK"), alertId: z.coerce.number().int().positive() }),
  z.object({ type: z.literal("NOTIFY_TEST") }),
  z.object({
    type: z.literal("SETTINGS_SET"),
    key: z.literal("notify.quiet_hours"),
    value: z.object({ start: hhmm, end: hhmm, critical_bypass: z.boolean() }).strict(),
  }),
  z.object({
    type: z.literal("SETTINGS_SET"),
    key: z.literal("notify.channels"),
    value: z.object({ TELEGRAM: z.boolean(), PUSHOVER: z.boolean(), EMAIL: z.boolean() }).strict(),
  }),
  z.object({ type: z.literal("SETTINGS_SET"), key: z.literal("notify.sms_enabled"), value: z.boolean() }),
]);
export type NotifyCommandInput = z.infer<typeof notifyCommandInput>;

export type IssueResult = { ok: true; commandId: number } | { ok: false; error: string };

const rowsOf = (res: unknown): Record<string, unknown>[] => (Array.isArray(res) ? res : ((res as { rows?: Record<string, unknown>[] }).rows ?? []));

/** Command row plus audit event in one statement (the Neon HTTP driver has no interactive transactions). */
async function insertCommand(actor: string, type: string, params: Record<string, unknown>, condition = sql`true`): Promise<number | null> {
  const json = JSON.stringify(params);
  const res = await db().execute(sql`
    with c as (
      insert into "command" ("type", "params", "issued_by")
      select ${type}::text, ${json}::jsonb, ${actor}::text where ${condition}
      returning "id", "type"
    )
    insert into "audit_event" ("actor", "kind", "object", "data")
    select ${actor}::text, 'COMMAND_ISSUED', 'command:' || c."id", jsonb_build_object('type', c."type", 'commandId', c."id", 'params', ${json}::jsonb)
    from c
    returning ("data"->>'commandId')::int as "commandId"
  `);
  const id = rowsOf(res)[0]?.commandId;
  return typeof id === "number" ? id : null;
}

/** Validated with zod; only the three notification commands exist here. Acknowledging approves nothing. */
export async function issueNotifyCommand(user: string, raw: unknown): Promise<IssueResult> {
  const parsed = notifyCommandInput.safeParse(raw);
  if (!parsed.success) return { ok: false, error: parsed.error.issues[0]?.message ?? "Ungültige Eingabe." };
  const input = parsed.data;
  const actor = `user:${user}`;
  if (input.type === "ALERT_ACK") {
    const id = await insertCommand(actor, "ALERT_ACK", { alert_id: input.alertId }, sql`exists (select 1 from "alert" a where a."id" = ${input.alertId} and a."status" = 'OPEN')`);
    return id === null ? { ok: false, error: "Meldung ist nicht mehr offen." } : { ok: true, commandId: id };
  }
  const params = input.type === "SETTINGS_SET" ? { key: input.key, value: input.value } : {};
  const id = await insertCommand(actor, input.type, params);
  return id === null ? { ok: false, error: "Befehl konnte nicht gespeichert werden." } : { ok: true, commandId: id };
}

const telegramInput = z.union([
  z.object({ action: z.literal("ack"), alert_id: z.number().int().positive() }).strict(),
  z.object({ action: z.literal("pause"), alert_id: z.number().int().positive() }).strict(),
  z.object({ action: z.literal("pause"), account_id: z.string().regex(/^[a-z0-9][a-z0-9-]{0,79}$/) }).strict(),
]);

/** TELEGRAM_CALLBACK for the engine: only «ack» or «pause» (paper; the engine re-checks the account mode). */
export async function issueTelegramCommand(params: unknown): Promise<IssueResult> {
  const parsed = telegramInput.safeParse(params);
  if (!parsed.success) return { ok: false, error: "Ungültige Telegram-Aktion." };
  const p = parsed.data;
  const condition =
    "account_id" in p ? sql`exists (select 1 from "account" a where a."id" = ${p.account_id} and a."mode" = 'PAPER')` : sql`exists (select 1 from "alert" a where a."id" = ${p.alert_id})`;
  const id = await insertCommand("telegram", "TELEGRAM_CALLBACK", p, condition);
  return id === null ? { ok: false, error: "account_id" in p ? "Kein Paper-Konto mit dieser Kennung." : "Meldung unbekannt." } : { ok: true, commandId: id };
}

/** Short read-only status for Telegram `/status`. Paper accounts are listed one by one, never summed. */
export async function telegramStatusText(now = Date.now()): Promise<string> {
  const [hb, paper, critical] = await Promise.all([
    db().select().from(heartbeats).where(eq(heartbeats.service, "engine")),
    db().select({ id: accounts.id, name: accounts.name, state: autopilots.state }).from(accounts).leftJoin(autopilots, eq(autopilots.accountId, accounts.id)).where(eq(accounts.mode, "PAPER")).orderBy(asc(accounts.id)),
    db().select().from(alerts).where(and(eq(alerts.status, "OPEN"), eq(alerts.level, "CRITICAL"), ne(alerts.kind, "DAILY_REPORT"))).orderBy(desc(alerts.updatedAt)).limit(5),
  ]);
  const lines = [`[SYSTEM] Status ${dateTime(new Date(now))}`];
  const beat = hb[0];
  lines.push(beat ? `Engine: letztes Lebenszeichen ${ago(beat.lastSeen, now)}${now - beat.lastSeen.getTime() > 5 * 60_000 ? " – überfällig" : ""}` : "Engine: noch kein Lebenszeichen");
  if (paper.length) for (const p of paper) lines.push(`[PAPER] ${p.name} (${p.id}): ${autopilotState(p.state).label}`);
  else lines.push("Keine Paper-Konten.");
  lines.push(`Offene kritische Meldungen: ${critical.length}`);
  for (const c of critical) lines.push(`• ${c.title.startsWith("[") ? c.title : `${MODE_PREFIX[c.mode] ?? "[SYSTEM]"} ${c.title}`}`);
  return lines.join("\n");
}
