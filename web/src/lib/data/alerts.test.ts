import { beforeAll, describe, expect, it } from "vitest";
import { eq } from "drizzle-orm";
import type { Database } from "@/db/client";
import { accounts, alertDeliveries, alerts, auditEvents, autopilots, channelStatus, commands, heartbeats, settings } from "@/db/schema";
import { createTestDb } from "@/test/db";
import { issueNotifyCommand, issueTelegramCommand, loadAlerts, openAlertCounts, telegramStatusText } from "./alerts";

const NOW = Date.UTC(2026, 9, 4, 12, 0, 0);
const at = (minutesAgo: number) => new Date(NOW - minutesAgo * 60_000);

let database: Database;
let critId: number;
let infoId: number;
let ackedId: number;

beforeAll(async () => {
  database = await createTestDb();
  await database.insert(accounts).values([
    { id: "paper-trend", mode: "PAPER", name: "Trend 4h", currency: "USD" },
    { id: "live-kraken", mode: "LIVE", name: "Echtgeld", currency: "USD" },
  ]);
  await database.insert(autopilots).values({ accountId: "paper-trend", state: "ENTRIES_PAUSED", reason: "Guardian" });
  await database.insert(heartbeats).values({ service: "engine", lastSeen: at(1), schemaVersion: 3 });
  const rows = await database
    .insert(alerts)
    .values([
      { level: "INFO", mode: "RESEARCH", kind: "LAB", dedupKey: "lab:1", title: "Backtest abgeschlossen", body: "", createdAt: at(30), updatedAt: at(30) },
      { level: "CRITICAL", mode: "PAPER", kind: "LOSS_LIMIT", dedupKey: "guardian:paper-trend:daily:breach:2026-10-04", title: "Tagesverlust-Grenze erreicht – Einstiege pausiert", body: "Tagesverlust 1.62 % ≥ Limit 1.50 %", occurrences: 7, data: { account_id: "paper-trend" }, createdAt: at(20), updatedAt: at(1) },
      { level: "WARNING", mode: "SYSTEM", kind: "FEED_STALE", dedupKey: "feed:1", title: "Datenstrom BTC 4h: STALE", body: "", status: "ACKNOWLEDGED", acknowledgedAt: at(5), acknowledgedBy: "user:admin", createdAt: at(60), updatedAt: at(5) },
      { level: "WARNING", mode: "SYSTEM", kind: "FEED_STALE", dedupKey: "feed:2", title: "Datenstrom ETH 4h: GAP", body: "", status: "RESOLVED", resolvedAt: at(2), createdAt: at(90), updatedAt: at(2) },
    ])
    .returning({ id: alerts.id, kind: alerts.kind, status: alerts.status });
  infoId = rows.find((r) => r.kind === "LAB")!.id;
  critId = rows.find((r) => r.kind === "LOSS_LIMIT")!.id;
  ackedId = rows.find((r) => r.status === "ACKNOWLEDGED")!.id;
  await database.insert(alertDeliveries).values([
    { alertId: critId, channel: "IN_APP", status: "SENT", at: at(20) },
    { alertId: critId, channel: "PUSHOVER", status: "SENT", providerRef: "rcpt1", at: at(20) },
    { alertId: critId, channel: "TELEGRAM", status: "SENT", providerRef: "101", at: at(20) },
    { alertId: critId, channel: "TELEGRAM", status: "SENT", providerRef: "102", at: at(5) },
    { alertId: critId, channel: "EMAIL", status: "SKIPPED_DISABLED", error: "Nicht eingerichtet – fehlt: RESEND_API_KEY", at: at(5) },
  ]);
  await database.insert(channelStatus).values([
    { channel: "TELEGRAM", configured: true, ok: true, detail: "Erreichbar", lastCheckAt: at(3), lastTestSentAt: at(600), lastTestAckAt: at(590) },
    { channel: "PUSHOVER", configured: false, ok: false, detail: "Nicht eingerichtet – fehlt: PUSHOVER_APP_TOKEN, PUSHOVER_USER_KEY", lastCheckAt: at(3) },
  ]);
  await database.insert(settings).values({ key: "notify.quiet_hours", value: { start: "23:00", end: "06:30", critical_bypass: true }, updatedBy: "user:admin" });
});

describe("alerts data", () => {
  it("groups alerts by status with their deliveries and reads settings", async () => {
    const d = await loadAlerts(NOW, { TELEGRAM_BOT_TOKEN: "x", TELEGRAM_CHAT_ID: "1" });
    expect(d.open.map((a) => a.id)).toEqual([critId, infoId]); // critical first
    expect(d.open[0].deliveries.map((x) => x.channel)).toEqual(["IN_APP", "PUSHOVER", "TELEGRAM", "TELEGRAM", "EMAIL"]);
    expect(d.acknowledged.map((a) => a.id)).toEqual([ackedId]);
    expect(d.resolved).toHaveLength(1);
    expect(d.quiet).toEqual({ start: "23:00", end: "06:30", critical_bypass: true });
    expect(d.enabled).toEqual({ TELEGRAM: true, PUSHOVER: true, EMAIL: true });
    expect(d.lastTestSentAt).toEqual(at(600));
    expect(d.lastTestAckAt).toEqual(at(590));
    expect(d.webMissing.webhook).toEqual(["TELEGRAM_WEBHOOK_SECRET"]);
    expect(d.webMissing.watchdogPushover).toEqual(["PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY"]);
    expect(await openAlertCounts()).toEqual({ CRITICAL: 1, INFO: 1 });
  });

  it("writes ALERT_ACK only for open alerts, with an audit event", async () => {
    const r = await issueNotifyCommand("admin", { type: "ALERT_ACK", alertId: String(critId) });
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    const [cmd] = await database.select().from(commands).where(eq(commands.id, r.commandId));
    expect(cmd).toMatchObject({ type: "ALERT_ACK", params: { alert_id: critId }, issuedBy: "user:admin", status: "PENDING" });
    const audit = await database.select().from(auditEvents).where(eq(auditEvents.object, `command:${r.commandId}`));
    expect(audit[0]).toMatchObject({ actor: "user:admin", kind: "COMMAND_ISSUED" });
    expect((await loadAlerts(NOW)).pendingAcks).toContain(critId);
    expect(await issueNotifyCommand("admin", { type: "ALERT_ACK", alertId: ackedId })).toEqual({ ok: false, error: "Meldung ist nicht mehr offen." });
    // the alert itself is untouched: only the engine acknowledges
    const [a] = await database.select().from(alerts).where(eq(alerts.id, critId));
    expect(a.status).toBe("OPEN");
  });

  it("validates settings and test commands", async () => {
    expect((await issueNotifyCommand("admin", { type: "NOTIFY_TEST" })).ok).toBe(true);
    expect((await issueNotifyCommand("admin", { type: "SETTINGS_SET", key: "notify.quiet_hours", value: { start: "22:00", end: "07:00", critical_bypass: false } })).ok).toBe(true);
    expect((await issueNotifyCommand("admin", { type: "SETTINGS_SET", key: "notify.channels", value: { TELEGRAM: true, PUSHOVER: true, EMAIL: false } })).ok).toBe(true);
    expect((await issueNotifyCommand("admin", { type: "SETTINGS_SET", key: "notify.quiet_hours", value: { start: "7 Uhr", end: "07:00", critical_bypass: false } })).ok).toBe(false);
    expect((await issueNotifyCommand("admin", { type: "SETTINGS_SET", key: "risk.daily_loss_limit", value: "0.5" })).ok).toBe(false);
    expect((await issueNotifyCommand("admin", { type: "PAPER_START" })).ok).toBe(false);
    expect((await issueNotifyCommand("admin", { type: "SETTINGS_SET", key: "notify.channels", value: { TELEGRAM: true, PUSHOVER: true, EMAIL: false, SMS: true } })).ok).toBe(false);
  });

  it("Telegram commands: only ack or paper pause", async () => {
    const ack = await issueTelegramCommand({ action: "ack", alert_id: critId });
    expect(ack.ok).toBe(true);
    const pause = await issueTelegramCommand({ action: "pause", account_id: "paper-trend" });
    expect(pause.ok).toBe(true);
    if (!pause.ok) return;
    const [cmd] = await database.select().from(commands).where(eq(commands.id, pause.commandId));
    expect(cmd).toMatchObject({ type: "TELEGRAM_CALLBACK", issuedBy: "telegram", params: { action: "pause", account_id: "paper-trend" } });
    expect(await issueTelegramCommand({ action: "pause", account_id: "live-kraken" })).toEqual({ ok: false, error: "Kein Paper-Konto mit dieser Kennung." });
    expect((await issueTelegramCommand({ action: "start", account_id: "paper-trend" })).ok).toBe(false);
    expect((await issueTelegramCommand({ action: "ack", alert_id: 99999 })).ok).toBe(false);
    expect((await issueTelegramCommand({ action: "ack", alert_id: critId, extra: 1 })).ok).toBe(false);
  });

  it("status text lists paper accounts separately and open critical alerts", async () => {
    const text = await telegramStatusText(NOW);
    expect(text.startsWith("[SYSTEM] Status")).toBe(true);
    expect(text).toContain("Engine: letztes Lebenszeichen vor 1 min");
    expect(text).toContain("[PAPER] Trend 4h (paper-trend): ");
    expect(text).toContain("Offene kritische Meldungen: 1");
    expect(text).toContain("• [PAPER] Tagesverlust-Grenze erreicht");
    expect(text).not.toContain("live-kraken");
  });
});
