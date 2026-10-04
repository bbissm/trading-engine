import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import { eq } from "drizzle-orm";
import type { Database } from "@/db/client";
import { alertDeliveries, alerts, heartbeats, settings } from "@/db/schema";
import { createTestDb } from "@/test/db";
import { runWatchdog, WATCHDOG_DEDUP, WATCHDOG_KEY } from "./watchdog";

const T = Date.UTC(2026, 9, 4, 12, 0, 0);
const MIN = 60_000;
const ENV = { TELEGRAM_BOT_TOKEN: "987654:FAKE-TOKEN-abc", TELEGRAM_CHAT_ID: "424242", PUSHOVER_APP_TOKEN: "aFAKEpushoverAPP", PUSHOVER_USER_KEY: "uFAKEpushoverUSER" };

let database: Database;

class FakeFetch {
  calls: { url: string; body: string }[] = [];
  acknowledged = false;
  fail = false;
  fn = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    this.calls.push({ url, body: typeof init?.body === "string" ? init.body : String(init?.body ?? "") });
    if (this.fail) throw new TypeError(`fetch failed for ${url}`);
    if (url.includes("api.telegram.org")) return Response.json({ ok: true, result: { message_id: 7 } });
    if (url.includes("/receipts/")) return Response.json({ status: 1, acknowledged: this.acknowledged ? 1 : 0 });
    return Response.json({ status: 1, receipt: "rcptW" });
  }) as typeof fetch;
  count(part: string) {
    return this.calls.filter((c) => c.url.includes(part)).length;
  }
}

async function beat(ms: number) {
  await database.insert(heartbeats).values({ service: "engine", lastSeen: new Date(ms), schemaVersion: 3 }).onConflictDoUpdate({ target: heartbeats.service, set: { lastSeen: new Date(ms) } });
}

beforeAll(async () => {
  database = await createTestDb();
});

beforeEach(async () => {
  await database.delete(alertDeliveries);
  await database.delete(alerts);
  await database.delete(settings);
  await database.delete(heartbeats);
});

describe("watchdog", () => {
  it("stays quiet while the engine reports", async () => {
    const f = new FakeFetch();
    await beat(T - 2 * MIN);
    expect(await runWatchdog(T, ENV, f.fn)).toMatchObject({ stale: false, sent: false });
    expect(f.calls).toHaveLength(0);
    expect(await database.select().from(alerts)).toHaveLength(0);
  });

  it("alerts via Telegram and Pushover at most every 30 minutes and resolves on recovery", async () => {
    const f = new FakeFetch();
    await beat(T - 6 * MIN);
    expect(await runWatchdog(T, ENV, f.fn)).toMatchObject({ stale: true, sent: true });
    expect(f.count("sendMessage")).toBe(1);
    expect(f.count("messages.json")).toBe(1);
    expect(JSON.parse(f.calls[0].body).text.startsWith("[SYSTEM] Engine meldet sich nicht")).toBe(true);
    const form = new URLSearchParams(f.calls[1].body);
    expect([form.get("priority"), form.get("retry"), form.get("expire")]).toEqual(["2", "60", "1800"]);

    const [row] = await database.select().from(alerts).where(eq(alerts.dedupKey, WATCHDOG_DEDUP));
    expect(row).toMatchObject({ level: "CRITICAL", mode: "SYSTEM", status: "OPEN", escalationLevel: 1 });
    expect((await database.select().from(alertDeliveries)).map((d) => [d.channel, d.status])).toEqual([["TELEGRAM", "SENT"], ["PUSHOVER", "SENT"]]);

    for (const m of [5, 10, 25]) await runWatchdog(T + m * MIN, ENV, f.fn);
    expect(f.count("sendMessage")).toBe(1); // only receipt checks in between
    expect(f.count("/receipts/rcptW.json")).toBe(3);
    const [again] = await database.select().from(alerts).where(eq(alerts.dedupKey, WATCHDOG_DEDUP));
    expect(again.occurrences).toBe(4);

    await runWatchdog(T + 31 * MIN, ENV, f.fn);
    expect(f.count("sendMessage")).toBe(2);

    await beat(T + 33 * MIN);
    expect(await runWatchdog(T + 34 * MIN, ENV, f.fn)).toMatchObject({ stale: false });
    const [resolved] = await database.select().from(alerts).where(eq(alerts.dedupKey, WATCHDOG_DEDUP));
    expect(resolved.status).toBe("RESOLVED");
    const [s] = await database.select().from(settings).where(eq(settings.key, WATCHDOG_KEY));
    expect(s.value).toEqual({ at: null, receipt: null, acknowledged: false });
  });

  it("stops repeating once confirmed on the Pushover device", async () => {
    const f = new FakeFetch();
    await beat(T - 10 * MIN);
    await runWatchdog(T, ENV, f.fn);
    f.acknowledged = true;
    expect(await runWatchdog(T + 5 * MIN, ENV, f.fn)).toMatchObject({ reason: "Pushover-Quittung erhalten" });
    const [row] = await database.select().from(alerts).where(eq(alerts.dedupKey, WATCHDOG_DEDUP));
    expect(row).toMatchObject({ status: "ACKNOWLEDGED", acknowledgedBy: "pushover" });
    await runWatchdog(T + 40 * MIN, ENV, f.fn);
    expect(f.count("sendMessage")).toBe(1);
  });

  it("without configured channels only records skipped deliveries; errors never contain secrets", async () => {
    const none = new FakeFetch();
    await runWatchdog(T, {}, none.fn); // no heartbeat at all counts as silent
    expect(none.calls).toHaveLength(0);
    expect((await database.select().from(alertDeliveries)).map((d) => d.status)).toEqual(["SKIPPED_DISABLED", "SKIPPED_DISABLED"]);

    await database.delete(settings);
    const broken = new FakeFetch();
    broken.fail = true;
    await runWatchdog(T + MIN, ENV, broken.fn);
    const errors = (await database.select().from(alertDeliveries)).map((d) => d.error ?? "").join(" ");
    expect(errors).toContain("***");
    for (const secret of [ENV.TELEGRAM_BOT_TOKEN, ENV.PUSHOVER_APP_TOKEN, ENV.PUSHOVER_USER_KEY]) expect(errors).not.toContain(secret);
  });
});
