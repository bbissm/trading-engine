import { createHash, timingSafeEqual } from "node:crypto";

/**
 * Vergleich in konstanter Laufzeit, auch bei unterschiedlicher Länge (beide Seiten werden zuerst gehasht).
 * Läuft im Proxy (Node.js-Runtime ab Next.js 16) und in Better-Auth-Hooks.
 */
export function constantTimeEqualStrings(a: string, b: string): boolean {
  const ha = createHash("sha256").update(a, "utf8").digest();
  const hb = createHash("sha256").update(b, "utf8").digest();
  return timingSafeEqual(ha, hb) && a.length === b.length;
}

/**
 * Maschinenzugang für /api/ops/*: `Authorization: Bearer <TE_API_TOKEN>`. Ohne konfiguriertes Token (oder bei
 * zu kurzem Token, < 32 Zeichen) gibt es keinen Maschinenzugang. Ein Token-Aufruf hat keine Sitzung und kann
 * darum nie einen Step-up bestehen.
 */
export function isValidApiToken(authorization: string | null | undefined, expected = process.env.TE_API_TOKEN): boolean {
  if (!expected || expected.length < 32 || !authorization) return false;
  const m = /^Bearer\s+(\S+)\s*$/i.exec(authorization);
  if (!m) return false;
  return constantTimeEqualStrings(m[1], expected);
}
