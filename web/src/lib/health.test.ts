import { describe, expect, it } from "vitest";
import { SCHEMA_VERSION } from "@/db/schema";
import { decimal, price, ago } from "./format";
import { attentionItems, HEARTBEAT_MAX_AGE_MS, heartbeatState, type FeedRow, type HeartbeatRow } from "./health";

const NOW = Date.UTC(2026, 9, 4, 12, 0, 0);
const hb = (service: string, ageMs: number, schemaVersion = SCHEMA_VERSION): HeartbeatRow => ({ service, lastSeen: new Date(NOW - ageMs), schemaVersion, detail: null });
const feed = (status: string, detail: string | null = null): FeedRow => ({ feed: "kraken", instrumentId: "KRAKEN:BTC/USD", timeframe: "4h", lastCandleClose: new Date(NOW - 3_600_000), lastOkAt: null, status, detail, updatedAt: new Date(NOW) });

describe("heartbeat staleness", () => {
  it("is OK below three minutes and stale from three minutes on", () => {
    expect(heartbeatState(hb("guardian", 10_000), NOW)).toMatchObject({ ok: true, schemaOk: true, ageMs: 10_000 });
    expect(heartbeatState(hb("guardian", HEARTBEAT_MAX_AGE_MS - 1), NOW).ok).toBe(true);
    expect(heartbeatState(hb("guardian", HEARTBEAT_MAX_AGE_MS), NOW).ok).toBe(false);
    expect(heartbeatState(hb("guardian", 1000, SCHEMA_VERSION + 1), NOW).schemaOk).toBe(false);
  });
});

describe("attention items", () => {
  it("is empty when everything is healthy", () => {
    expect(attentionItems({ heartbeats: [hb("guardian", 5000)], feeds: [feed("OK")], dbSchemaVersion: SCHEMA_VERSION }, NOW)).toEqual([]);
  });

  it("reports a missing heartbeat", () => {
    const items = attentionItems({ heartbeats: [], feeds: [], dbSchemaVersion: SCHEMA_VERSION }, NOW);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ id: "heartbeat:none", severity: "critical" });
  });

  it("reports stale heartbeats, schema mismatches and feeds that are not OK — critical first", () => {
    const items = attentionItems({ heartbeats: [hb("guardian", 5000, 99), hb("marketdata", 600_000)], feeds: [feed("STALE"), feed("ERROR", "HTTP 503")], dbSchemaVersion: null }, NOW);
    expect(items.map((i) => [i.id, i.severity])).toEqual([
      ["schema:db", "critical"],
      ["heartbeat:marketdata", "critical"],
      ["feed:kraken:KRAKEN:BTC/USD:4h", "critical"],
      ["schema:guardian", "warning"],
      ["feed:kraken:KRAKEN:BTC/USD:4h", "warning"],
    ]);
    expect(items[1].detail).toContain("vor 10 min");
    expect(items[2].detail).toBe("HTTP 503");
  });
});

describe("format", () => {
  it("formats numeric strings without float math", () => {
    expect(decimal("64250.5000000000")).toBe("64’250.50");
    expect(decimal("0.0000123400")).toBe("0.00001234");
    expect(decimal("1234567.0000000000")).toBe("1’234’567.00");
    expect(decimal("123456789012345678.1234567891")).toBe("123’456’789’012’345’678.1234567891");
    expect(decimal("-1500.2500000000")).toBe("-1’500.25");
    expect(decimal(null)).toBe("—");
    expect(price("62900.0000000000", "USD")).toBe("62’900.00 USD");
    expect(price(null, "USD")).toBe("—");
  });

  it("formats relative ages", () => {
    expect(ago(new Date(NOW - 2000), NOW)).toBe("gerade eben");
    expect(ago(new Date(NOW - 40_000), NOW)).toBe("vor 40 s");
    expect(ago(new Date(NOW - 5 * 60_000), NOW)).toBe("vor 5 min");
    expect(ago(new Date(NOW - 3 * 3_600_000), NOW)).toBe("vor 3 h");
    expect(ago(new Date(NOW - 2 * 86_400_000), NOW)).toBe("vor 2 Tagen");
    expect(ago(null, NOW)).toBe("—");
  });
});
