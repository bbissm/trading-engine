import "server-only";
import { createHash, timingSafeEqual } from "node:crypto";
import { and, eq, lt, sql } from "drizzle-orm";
import { z } from "zod";
import { db } from "@/db/client";
import { llmBridgeRequests } from "@/db/schema";

/**
 * Claude über die lokale Claude Code Bridge (01-IT-Projects/claude-bridge, Ziel "trading"), wie bei SiteEngine und
 * CommerceEngine: Anfrage in `llm_bridge_request`, ntfy-Ping weckt die Bridge, sie antwortet mit `claude -p`
 * (Claude-Plan, keine API-Kosten). Anders als dort wartet hier niemand: die Antwort wird verarbeitet, sobald sie
 * eintrifft (/api/llm-bridge/result), oder beim nächsten Nachrichtenlauf. Ist der PC aus, bleibt sie liegen.
 * Wire-Format wie claude-bridge/src/protocol.ts.
 */

export type BridgeObjectRequest = { kind: "object"; model?: string; system?: string; prompt: string; schema: Record<string, unknown>; schemaName?: string };

export const bridgeResultSchema = z.discriminatedUnion("ok", [
  z.object({
    id: z.string(),
    ok: z.literal(true),
    text: z.string().optional(),
    object: z.unknown().optional(),
    citations: z.array(z.string()).optional(),
    model: z.string(),
    inputTokens: z.number().optional(),
    outputTokens: z.number().optional(),
  }),
  z.object({ id: z.string(), ok: z.literal(false), error: z.string().max(4000), code: z.enum(["limit", "auth", "refusal", "error"]) }),
]);
export type BridgeResult = z.infer<typeof bridgeResultSchema>;

/** Eine abgeholte Anfrage, deren Bridge verstummt ist, wird wieder vergeben. */
const CLAIM_TIMEOUT_MS = 15 * 60_000;

export const bridgeConfigured = () => Boolean(process.env.LLM_BRIDGE_TOKEN);

/** Bearer LLM_BRIDGE_TOKEN der lokalen Bridge; ohne Token sind die Routen aus. */
export function bridgeTokenOk(header: string | null, expected = process.env.LLM_BRIDGE_TOKEN): boolean {
  const given = header?.match(/^Bearer\s+(\S+)$/i)?.[1];
  if (!expected || !given) return false;
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

/** Weckt die Bridge (ntfy-Topic, Text = Zielname); ohne Erfolg holt sie die Anfrage beim nächsten Abfragen. */
export async function wakeBridge(f: typeof fetch = fetch) {
  const url = process.env.LLM_BRIDGE_NOTIFY_URL;
  if (!url) return;
  await f(url, { method: "POST", body: "trading", signal: AbortSignal.timeout(3_000) }).catch(() => {});
}

/**
 * Legt eine Anfrage an (gleicher Inhalt = gleiche Anfrage). Eine verfallene oder gescheiterte Anfrage (ausser einer
 * Ablehnung durch Claude) wird wieder offen gesetzt. true, wenn sie jetzt offen ist und vorher nicht.
 */
export async function enqueue(purpose: string, request: BridgeObjectRequest, keyParts: unknown, now = new Date()): Promise<boolean> {
  const key = createHash("sha256").update(JSON.stringify({ purpose, keyParts })).digest("hex");
  const rows = await db()
    .insert(llmBridgeRequests)
    .values({ key, purpose, request, createdAt: now, updatedAt: now })
    .onConflictDoUpdate({
      target: llmBridgeRequests.key,
      set: { status: "pending", request, response: null, error: null, errorCode: null, claimedAt: null, processedAt: null, createdAt: now, updatedAt: now },
      setWhere: sql`${llmBridgeRequests.status} = 'cancelled' OR (${llmBridgeRequests.status} = 'failed' AND coalesce(${llmBridgeRequests.errorCode}, '') <> 'refusal')`,
    })
    .returning({ id: llmBridgeRequests.id });
  return rows.length > 0;
}

/** Älteste offene Anfrage (atomar: zwei Bridges bekommen nie dieselbe). */
export async function claimBridgeRequest(now = new Date()): Promise<{ id: string; request: BridgeObjectRequest } | null> {
  const cutoff = new Date(now.getTime() - CLAIM_TIMEOUT_MS).toISOString();
  const res = await db().execute(
    sql`UPDATE llm_bridge_request SET status = 'claimed', claimed_at = ${now.toISOString()}::timestamptz, updated_at = ${now.toISOString()}::timestamptz
        WHERE id = (
          SELECT id FROM llm_bridge_request
          WHERE status = 'pending' OR (status = 'claimed' AND claimed_at < ${cutoff}::timestamptz)
          ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED
        )
        RETURNING id, request`,
  );
  const rows = (Array.isArray(res) ? res : (res as unknown as { rows: { id: string; request: BridgeObjectRequest }[] }).rows) as { id: string; request: BridgeObjectRequest }[];
  return rows[0] ?? null;
}

/** Antwort der Bridge speichern; false, wenn die Anfrage nicht (mehr) abgeholt ist. */
export async function completeBridgeRequest(result: BridgeResult, now = new Date()): Promise<boolean> {
  const { id, ...rest } = result;
  const set = rest.ok
    ? { status: "done", response: rest as Record<string, unknown>, error: null, errorCode: null, updatedAt: now }
    : { status: "failed", error: rest.error, errorCode: rest.code, updatedAt: now };
  const res = await db()
    .update(llmBridgeRequests)
    .set(set)
    .where(and(eq(llmBridgeRequests.id, id), eq(llmBridgeRequests.status, "claimed")))
    .returning({ id: llmBridgeRequests.id });
  return res.length > 0;
}

/** Anfragen, die niemand abgeholt hat (PC tagelang aus), verfallen; ihre Meldungen werden neu gebündelt. */
export async function expireStale(maxAgeMs: number, now = new Date()): Promise<number> {
  const res = await db()
    .update(llmBridgeRequests)
    .set({ status: "cancelled", updatedAt: now })
    .where(and(eq(llmBridgeRequests.status, "pending"), lt(llmBridgeRequests.createdAt, new Date(now.getTime() - maxAgeMs))))
    .returning({ id: llmBridgeRequests.id });
  return res.length;
}
