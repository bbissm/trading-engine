import { timingSafeEqual } from "node:crypto";

/** `Authorization: Bearer $CRON_SECRET` — Vercel sends it on cron invocations (same scheme as the engine's api/tick.py). */
export function cronAuthorized(header: string | null, secret = process.env.CRON_SECRET ?? ""): boolean {
  if (!secret || !header) return false;
  const a = Buffer.from(header);
  const b = Buffer.from(`Bearer ${secret}`);
  return a.length === b.length && timingSafeEqual(a, b);
}
