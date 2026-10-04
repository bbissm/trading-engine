import { describe, expect, it } from "vitest";
import { cronAuthorized } from "./cron";
import { missing, redact } from "./env";
import { deliveryOf, enabledChannelsOf, quietHoursOf } from "./labels";
import { fromAllowedChat, parseUpdate, verifySecret } from "./telegram";

const ENV = { TELEGRAM_WEBHOOK_SECRET: "hook-secret_123", TELEGRAM_CHAT_ID: "424242", TELEGRAM_BOT_TOKEN: "987654:FAKE-TOKEN-abc" };

describe("Telegram webhook", () => {
  it("accepts only the configured secret header", () => {
    expect(verifySecret("hook-secret_123", ENV)).toBe(true);
    expect(verifySecret("hook-secret_12", ENV)).toBe(false);
    expect(verifySecret(null, ENV)).toBe(false);
    expect(verifySecret("anything", {})).toBe(false); // without a configured secret nothing is accepted
  });

  it("accepts only updates from the configured chat", () => {
    expect(fromAllowedChat({ message: { chat: { id: 424242 }, text: "/status" } }, ENV)).toBe(true);
    expect(fromAllowedChat({ message: { chat: { id: 1 }, text: "/status" } }, ENV)).toBe(false);
    expect(fromAllowedChat({ callback_query: { id: "q", data: "a:1", message: { chat: { id: "424242" } } } }, ENV)).toBe(true);
    expect(fromAllowedChat({ callback_query: { id: "q", data: "a:1" } }, ENV)).toBe(false);
    expect(fromAllowedChat({ message: { chat: { id: 424242 } } }, {})).toBe(false);
  });

  it("parses only acknowledge, pause and status", () => {
    expect(parseUpdate({ callback_query: { id: "q1", data: "a:17" } })).toEqual({ kind: "callback", callbackId: "q1", action: "ack", alertId: 17 });
    expect(parseUpdate({ callback_query: { id: "q2", data: "p:17" } })).toEqual({ kind: "callback", callbackId: "q2", action: "pause", alertId: 17 });
    for (const data of ["approve:17", "a:17;drop", "start:paper-a", "p:", "a:-1", "x"]) expect(parseUpdate({ callback_query: { id: "q", data } }).kind).toBe("ignore");
    expect(parseUpdate({ message: { text: "/status" } })).toEqual({ kind: "status" });
    expect(parseUpdate({ message: { text: "/status@te_bot" } })).toEqual({ kind: "status" });
    expect(parseUpdate({ message: { text: "/pause paper-trend" } })).toEqual({ kind: "pause", accountId: "paper-trend" });
    for (const text of ["/pause", "/pause Paper Trend", "/start paper-trend", "/buy BTC", "/close_all paper-a", "hallo", "/pause ../x"]) expect(parseUpdate({ message: { text } }).kind).toBe("ignore");
  });
});

describe("helpers", () => {
  it("cron bearer must match CRON_SECRET exactly", () => {
    expect(cronAuthorized("Bearer s3cret", "s3cret")).toBe(true);
    expect(cronAuthorized("Bearer s3cre", "s3cret")).toBe(false);
    expect(cronAuthorized("Bearer ", "")).toBe(false);
    expect(cronAuthorized(null, "s3cret")).toBe(false);
  });

  it("reports missing names and redacts secret values", () => {
    expect(missing(["TELEGRAM_BOT_TOKEN", "PUSHOVER_APP_TOKEN"], ENV)).toEqual(["PUSHOVER_APP_TOKEN"]);
    expect(redact("POST https://api.telegram.org/bot987654:FAKE-TOKEN-abc/sendMessage failed", ENV)).toBe("POST https://api.telegram.org/bot***/sendMessage failed");
  });

  it("labels and settings fall back to safe defaults", () => {
    expect(quietHoursOf({ start: "25:00", end: "07:00", critical_bypass: true })).toEqual({ start: "22:00", end: "07:00", critical_bypass: true });
    expect(quietHoursOf({ start: "23:00", end: "06:30", critical_bypass: false })).toEqual({ start: "23:00", end: "06:30", critical_bypass: false });
    expect(enabledChannelsOf({ EMAIL: false, SMS: true })).toEqual({ TELEGRAM: true, PUSHOVER: true, EMAIL: false });
    expect(deliveryOf("SENT", null).label).toBe("Angenommen");
    expect(deliveryOf("SENT", "cancel:r1").label).toBe("Wiederholung beendet");
  });
});
