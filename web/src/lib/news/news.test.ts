import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import { eq } from "drizzle-orm";
import type { Database } from "@/db/client";
import { llmBridgeRequests, marketBriefings, newsAssessments, newsItems } from "@/db/schema";
import { createTestDb } from "@/test/db";
import { bridgeTokenOk, claimBridgeRequest, completeBridgeRequest } from "./bridge";
import { cleanText, FEEDS, parseFeed } from "./feeds";
import { loadNews, runNewsCycle } from "./pipeline";

const RSS = `<?xml version="1.0"?><rss><channel><title>X</title>
<item><title><![CDATA[Bank launches tokenized deposits on Quant&#39;s Overledger]]></title>
<link>https://news.example/qnt-bank</link><pubDate>Fri, 06 Nov 2026 08:00:00 +0000</pubDate>
<description>&lt;p&gt;A large European bank &amp;amp; partners go live.&lt;/p&gt;</description></item>
<item><title>Bitcoin price recap</title><link>https://news.example/btc</link><pubDate>Fri, 06 Nov 2026 09:00:00 +0000</pubDate></item>
<item><title>Missing link</title></item>
</channel></rss>`;
const ATOM = `<feed><entry><title>Fed holds rates</title><link href="https://fed.example/rates"/><updated>2026-11-06T10:00:00Z</updated><summary>FOMC statement</summary></entry></feed>`;

const NOW = new Date("2026-11-06T13:30:00Z");
let database: Database;

function fakeFetch(calls: string[] = []) {
  return (async (input: string | URL | Request) => {
    const url = String(input);
    calls.push(url);
    if (url.includes("theblock")) return new Response(RSS);
    if (url.includes("federalreserve")) return new Response(ATOM);
    if (url.includes("ntfy")) return new Response("ok");
    return new Response("nope", { status: 503 });
  }) as typeof fetch;
}

beforeAll(async () => {
  database = await createTestDb();
});

beforeEach(async () => {
  await database.delete(newsAssessments);
  await database.delete(newsItems);
  await database.delete(llmBridgeRequests);
  await database.delete(marketBriefings);
  process.env.LLM_BRIDGE_NOTIFY_URL = "https://ntfy.example/topic";
});

describe("feeds", () => {
  it("parses RSS and Atom, decodes entities and skips entries without link", () => {
    const items = parseFeed(RSS, "The Block");
    expect(items.map((i) => i.title)).toEqual(["Bank launches tokenized deposits on Quant's Overledger", "Bitcoin price recap"]);
    expect(items[0].summary).toBe("A large European bank & partners go live.");
    expect(items[0].publishedAt?.toISOString()).toBe("2026-11-06T08:00:00.000Z");
    expect(parseFeed(ATOM, "Fed")[0]).toMatchObject({ url: "https://fed.example/rates", summary: "FOMC statement" });
    expect(cleanText("x".repeat(700)).length).toBe(600);
    expect(FEEDS.length).toBeGreaterThanOrEqual(6);
  });

  it("accepts only the exact bridge token", () => {
    expect(bridgeTokenOk("Bearer abc123", "abc123")).toBe(true);
    expect(bridgeTokenOk("Bearer abc1234", "abc123")).toBe(false);
    expect(bridgeTokenOk("Bearer abc123", undefined)).toBe(false);
    expect(bridgeTokenOk(null, "abc123")).toBe(false);
  });
});

describe("news cycle", () => {
  it("stores new items once, asks Claude in a batch and stores the assessments when the answer arrives", async () => {
    const calls: string[] = [];
    const first = await runNewsCycle(NOW, fakeFetch(calls));
    expect(first).toMatchObject({ inserted: 3, enqueued: 1, briefing: "nothing" });
    expect(first.feedErrors.length).toBe(FEEDS.length - 2);
    expect(calls.some((u) => u.includes("ntfy"))).toBe(true);

    // zweiter Lauf: nichts doppelt
    expect(await runNewsCycle(NOW, fakeFetch())).toMatchObject({ inserted: 0, enqueued: 0 });

    const claimed = await claimBridgeRequest(NOW);
    expect(claimed?.request).toMatchObject({ kind: "object", schemaName: "news_assessment" });
    expect(claimed?.request.prompt).toContain("[1] The Block");
    expect(await claimBridgeRequest(NOW)).toBeNull();

    const itemIds = (claimed!.request as unknown as { itemIds: string[] }).itemIds;
    const titles = Object.fromEntries((await database.select().from(newsItems)).map((i) => [i.id, i.title]));
    const n = (t: string) => itemIds.findIndex((id) => titles[id] === t) + 1;
    const answer = {
      items: [
        { n: n("Bank launches tokenized deposits on Quant's Overledger"), coins: ["$qnt"], scope: "COIN", category: "ADOPTION", direction: 2, credibility: "OFFICIAL", horizon: "WEEKS", relevance: 80, rationale: "Erster produktiver Einsatz bei einer Grossbank." },
        { n: n("Fed holds rates"), coins: [], scope: "MACRO", category: "MACRO", direction: 0, credibility: "OFFICIAL", horizon: "WEEKS", relevance: 45, rationale: "Zinsen unverändert, erwartet." },
        { n: 99, coins: ["BTC"], scope: "COIN", category: "MARKET", direction: 1, credibility: "REPORTED", horizon: "DAYS", relevance: 10, rationale: "ungültige Nummer" },
      ],
    };
    expect(await completeBridgeRequest({ id: claimed!.id, ok: true, object: answer, model: "claude-sonnet-5-5" }, NOW)).toBe(true);
    expect(await completeBridgeRequest({ id: claimed!.id, ok: false, error: "x", code: "error" }, NOW)).toBe(false);

    const third = await runNewsCycle(new Date(NOW.getTime() + 4 * 3_600_000), fakeFetch());
    expect(third).toMatchObject({ processed: 1, enqueued: 0, briefing: "enqueued" });
    const rows = await database.select().from(newsAssessments);
    expect(rows).toHaveLength(2);
    expect(rows.find((r) => r.scope === "COIN")?.coins).toEqual(["QNT"]);
    // die übergangene Meldung gilt trotzdem als erledigt
    expect((await database.select().from(newsItems)).every((i) => i.assessedAt)).toBe(true);

    const view = await loadNews();
    expect(view.rows.map((r) => r.a.relevance)).toEqual([80, 45]);
  });

  it("writes the daily briefing from the bridge answer, once per day", async () => {
    await runNewsCycle(NOW, fakeFetch());
    const assess = await claimBridgeRequest(NOW);
    const ids = (assess!.request as unknown as { itemIds: string[] }).itemIds;
    await completeBridgeRequest({ id: assess!.id, ok: true, object: { items: ids.map((_, i) => ({ n: i + 1, coins: ["BTC"], scope: "COIN", category: "MARKET", direction: 1, credibility: "REPORTED", horizon: "DAYS", relevance: 50, rationale: "r" })) }, model: "m" }, NOW);
    expect((await runNewsCycle(NOW, fakeFetch())).briefing).toBe("enqueued");

    const b = await claimBridgeRequest(NOW);
    expect(b?.request.schemaName).toBe("market_briefing");
    await completeBridgeRequest({ id: b!.id, ok: true, object: { headline: "Ruhiger Tag", summary: "S", coins: [{ coin: "btc", direction: 1, note: "n" }], watch: ["CPI am Dienstag"] }, model: "m" }, NOW);
    expect((await runNewsCycle(NOW, fakeFetch())).briefing).toBe("exists");
    const [row] = await database.select().from(marketBriefings).where(eq(marketBriefings.day, "2026-11-06"));
    expect(row).toMatchObject({ headline: "Ruhiger Tag", coins: [{ coin: "BTC", direction: 1, note: "n" }], itemCount: 3 });
  });

  it("re-asks after an unusable answer or a request nobody picked up", async () => {
    await runNewsCycle(NOW, fakeFetch());
    const c = await claimBridgeRequest(NOW);
    await completeBridgeRequest({ id: c!.id, ok: true, object: { nonsense: true }, model: "m" }, NOW);
    const later = new Date(NOW.getTime() + 4 * 3_600_000);
    const r = await runNewsCycle(later, fakeFetch());
    expect(r.processed).toBe(0);
    expect(r.enqueued).toBe(1); // dieselben Meldungen, gleiche Anfrage wieder offen
    const [req] = await database.select().from(llmBridgeRequests);
    expect(req.status).toBe("pending");

    // PC drei Tage aus: Anfrage verfällt, die Meldungen sind dann zu alt für eine Einordnung
    const r2 = await runNewsCycle(new Date(later.getTime() + 3 * 86_400_000), fakeFetch());
    expect(r2.enqueued).toBe(0); // Meldungen älter als 3 Tage werden nicht mehr gefragt
    expect((await database.select().from(llmBridgeRequests))[0].status).toBe("cancelled");
  });
});
