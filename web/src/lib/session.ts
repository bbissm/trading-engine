/**
 * INTERIM login: single user, password from the environment, session as a signed cookie
 * (HMAC-SHA256 via Web Crypto — runs in the proxy and in server actions). Same mechanism as the Control Center.
 *
 * TODO(E0-4): REPLACE BEFORE ANY LIVE-TRADING CONTROL EXISTS.
 * Plan task E0-4 requires Better Auth with passkey + TOTP and a step-up mechanism (re-confirmation, max. 5 min old)
 * for mandate activation, limit increases, closing positions, key changes and promotion.
 * This password session is only acceptable while the app is read-only plus the harmless PING command.
 *
 * Changing TE_PASSWORD (or SESSION_SECRET) signs every device out.
 */
export const SESSION_COOKIE = "te_session";
export const SESSION_DAYS = 180;
/** Sessions expiring in less than this are renewed on use (sliding session). */
export const SESSION_RENEW_DAYS = 150;

const DAY = 86_400_000;
const enc = new TextEncoder();

function secret(): string | undefined {
  const password = process.env.TE_PASSWORD;
  if (!password) return undefined;
  return `session:${password}:${process.env.SESSION_SECRET ?? ""}`;
}

async function hmac(data: string, key: string): Promise<string> {
  const k = await crypto.subtle.importKey("raw", enc.encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", k, enc.encode(data)));
  return Array.from(sig, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Constant-time compare without node:crypto (the proxy may run on the edge runtime). */
function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function createSessionToken(now = Date.now()): Promise<{ token: string; expires: Date }> {
  const key = secret();
  if (!key) throw new Error("TE_PASSWORD is not configured");
  const exp = now + SESSION_DAYS * DAY;
  return { token: `v1.${exp}.${await hmac(`v1.${exp}`, key)}`, expires: new Date(exp) };
}

/** Valid session → its expiry timestamp, otherwise null. */
export async function verifySessionToken(token: string | undefined, now = Date.now()): Promise<number | null> {
  const key = secret();
  if (!key || !token) return null;
  const [v, expRaw, sig] = token.split(".");
  const exp = Number(expRaw);
  if (v !== "v1" || !Number.isFinite(exp) || exp <= now || !sig) return null;
  return safeEqual(sig, await hmac(`v1.${exp}`, key)) ? exp : null;
}

export const shouldRenew = (exp: number, now = Date.now()) => exp - now < SESSION_RENEW_DAYS * DAY;

/** The single configured user; used as `user:<name>` in commands and audit events. */
export const userName = () => process.env.TE_USER ?? "admin";

export function checkCredentials(user: string, password: string): boolean {
  const expectedPassword = process.env.TE_PASSWORD;
  if (!expectedPassword) return false;
  return safeEqual(user.trim(), userName()) && safeEqual(password, expectedPassword);
}

export const sessionCookieOptions = (expires: Date) => ({
  httpOnly: true,
  secure: process.env.NODE_ENV === "production",
  sameSite: "lax" as const,
  path: "/",
  expires,
});
