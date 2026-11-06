import { createHash } from "node:crypto";

/**
 * Nachrichtenquellen (frei zugängliche RSS-Feeds). Krypto-Medien für Coin-Meldungen, US-Notenbank und SEC für
 * Politik/Regulierung. Eine neue Quelle ist ein Eintrag hier; der Rest des Ablaufs bleibt gleich.
 */
export const FEEDS: { source: string; url: string }[] = [
  { source: "CoinDesk", url: "https://www.coindesk.com/arc/outboundfeeds/rss" },
  { source: "The Block", url: "https://www.theblock.co/rss.xml" },
  { source: "Decrypt", url: "https://decrypt.co/feed" },
  { source: "Cointelegraph", url: "https://cointelegraph.com/rss" },
  { source: "Bitcoin Magazine", url: "https://bitcoinmagazine.com/feed" },
  { source: "CryptoSlate", url: "https://cryptoslate.com/feed/" },
  { source: "Federal Reserve", url: "https://www.federalreserve.gov/feeds/press_all.xml" },
  { source: "SEC", url: "https://www.sec.gov/news/pressreleases.rss" },
];

export interface FeedItem {
  id: string;
  source: string;
  url: string;
  title: string;
  summary: string | null;
  publishedAt: Date | null;
}

const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };

/** HTML-Entities auflösen (auch doppelt kodierte wie `&amp;#039;`). */
export function decodeEntities(s: string): string {
  let out = s;
  for (let i = 0; i < 2; i++) {
    out = out
      .replace(/&#x([0-9a-f]+);/gi, (_, h: string) => String.fromCodePoint(parseInt(h, 16)))
      .replace(/&#(\d+);/g, (_, d: string) => String.fromCodePoint(Number(d)))
      .replace(/&([a-z]+);/gi, (m, n: string) => ENTITIES[n.toLowerCase()] ?? m);
  }
  return out;
}

/** Text ohne CDATA, HTML-Tags und überflüssige Leerzeichen. */
export function cleanText(raw: string | undefined, max = 600): string {
  if (!raw) return "";
  const text = decodeEntities(raw.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1"))
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

const tag = (block: string, name: string) => block.match(new RegExp(`<${name}(?:\\s[^>]*)?>([\\s\\S]*?)</${name}>`, "i"))?.[1];

export const itemId = (url: string) => createHash("sha256").update(url).digest("hex");

/** RSS 2.0 (`<item>`) und Atom (`<entry>`). Einträge ohne Titel oder Link werden übersprungen. */
export function parseFeed(xml: string, source: string): FeedItem[] {
  const blocks = xml.match(/<item[\s>][\s\S]*?<\/item>|<entry[\s>][\s\S]*?<\/entry>/gi) ?? [];
  const items: FeedItem[] = [];
  for (const block of blocks) {
    const title = cleanText(tag(block, "title"), 300);
    const link = cleanText(tag(block, "link")) || block.match(/<link[^>]*href="([^"]+)"/i)?.[1] || "";
    const url = decodeEntities(link).trim();
    if (!title || !/^https?:\/\//.test(url)) continue;
    const date = tag(block, "pubDate") ?? tag(block, "published") ?? tag(block, "updated") ?? tag(block, "dc:date");
    const published = date ? new Date(cleanText(date)) : null;
    const summary = cleanText(tag(block, "description") ?? tag(block, "summary") ?? tag(block, "content:encoded"));
    items.push({ id: itemId(url), source, url, title, summary: summary || null, publishedAt: published && !Number.isNaN(published.getTime()) ? published : null });
  }
  return items;
}
