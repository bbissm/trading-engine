import { base32 } from "@better-auth/utils/base32";
import { createOTP } from "@better-auth/utils/otp";
import { eq, like } from "drizzle-orm";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { authSession, authUser } from "@/db/auth-schema";
import { schema, setDb, type Database } from "@/db/client";
import { createTestDb } from "@/test/db";
import { evaluateStepUp } from "./step-up-core";

/**
 * Better Auth gegen PGlite mit den echten Migrationen: Bootstrap des einzigen Benutzers, TOTP-Pflicht,
 * Step-up mit TOTP (inkl. Ablauf und Replay-Schutz), Step-up-Pflicht für Backup-Codes, Audit.
 */
const BASE = "http://localhost:3999";
const EMAIL = "owner@example.org";
const PASSWORD = "correct horse battery staple";
const env = { TE_PASSWORD: PASSWORD, TE_ADMIN_EMAIL: EMAIL, BETTER_AUTH_SECRET: "test-secret-".padEnd(48, "x"), BETTER_AUTH_URL: BASE, DATABASE_URL: "pglite://memory" };

let database: Database;
let handler: (req: Request) => Promise<Response>;
let ipCounter = 0;

class Jar {
  cookies = new Map<string, string>();
  header() {
    return [...this.cookies].map(([k, v]) => `${k}=${v}`).join("; ");
  }
  store(res: Response) {
    for (const c of res.headers.getSetCookie()) {
      const [pair, ...attrs] = c.split(";");
      const i = pair.indexOf("=");
      const name = pair.slice(0, i).trim();
      const value = pair.slice(i + 1).trim();
      const expired = attrs.some((a) => /max-age=0/i.test(a.trim())) || value === "";
      if (expired) this.cookies.delete(name);
      else this.cookies.set(name, value);
    }
  }
}

async function call(jar: Jar, path: string, body?: unknown, method = body === undefined ? "GET" : "POST") {
  const res = await handler(
    new Request(`${BASE}/api/auth${path}`, {
      method,
      headers: { origin: BASE, cookie: jar.header(), "x-forwarded-for": `10.0.0.${++ipCounter}`, "user-agent": "vitest", ...(body !== undefined ? { "content-type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
  );
  jar.store(res);
  const text = await res.text();
  let json: Record<string, unknown> = {};
  try {
    json = text ? JSON.parse(text) : {};
  } catch {
    /* keine JSON-Antwort */
  }
  return { status: res.status, json };
}

const totpFromUri = async (uri: string) => {
  const secret = new TextDecoder().decode(base32.decode(new URL(uri).searchParams.get("secret")!));
  return createOTP(secret, { digits: 6, period: 30 }).totp();
};

describe("Better Auth single-user flow", () => {
  beforeAll(async () => {
    Object.assign(process.env, env);
    database = await createTestDb();
    const { getAuth } = await import("./server");
    handler = (req) => getAuth().handler(req);
  });
  afterAll(() => {
    for (const k of Object.keys(env)) delete process.env[k];
    setDb(undefined);
  });

  let totpUri = "";
  const jar = new Jar();

  it("refuses a wrong password and unknown emails, without creating a user", async () => {
    expect((await call(new Jar(), "/sign-in/email", { email: EMAIL, password: "wrong-password-123" })).status).toBe(401);
    expect((await call(new Jar(), "/sign-in/email", { email: "other@example.org", password: PASSWORD })).status).toBe(401);
    expect((await call(new Jar(), "/sign-up/email", { email: "x@example.org", password: PASSWORD, name: "x" })).status).toBeGreaterThanOrEqual(400);
    expect(await database.select().from(authUser)).toHaveLength(0);
  });

  it("bootstraps the single user on first login and forces TOTP before anything else", async () => {
    const r = await call(jar, "/sign-in/email", { email: EMAIL.toUpperCase(), password: PASSWORD });
    expect(r.status).toBe(200);
    expect(r.json.twoFactorRedirect).toBeUndefined();
    const users = await database.select().from(authUser);
    expect(users).toHaveLength(1);
    expect(users[0].email).toBe(EMAIL);
    expect(users[0].twoFactorEnabled).toBe(false);
    // ohne TOTP: keine Passkeys, keine Sitzungsliste, kein Step-up
    expect((await call(jar, "/passkey/list-user-passkeys")).json.code).toBe("TOTP_SETUP_REQUIRED");
    expect((await call(jar, "/list-sessions")).status).toBe(403);
    expect((await call(jar, "/step-up/totp", { code: "123456" })).status).toBe(403);
  });

  it("enrols TOTP with backup codes", async () => {
    expect((await call(jar, "/two-factor/enable", { password: "nope-nope-nope" })).status).toBe(400);
    const r = await call(jar, "/two-factor/enable", { password: PASSWORD });
    expect(r.status).toBe(200);
    totpUri = r.json.totpURI as string;
    expect(totpUri).toMatch(/^otpauth:\/\/totp\/TradingEngine:/);
    expect(r.json.backupCodes).toHaveLength(10);
    expect((await call(jar, "/two-factor/verify-totp", { code: "000000" })).status).toBe(401);
    const ok = await call(jar, "/two-factor/verify-totp", { code: await totpFromUri(totpUri) });
    expect(ok.status).toBe(200);
    expect((await database.select().from(authUser))[0].twoFactorEnabled).toBe(true);
    // erneutes Einrichten (Geheimnis austauschen) ist mit blosser Sitzung nicht möglich
    expect((await call(jar, "/two-factor/enable", { password: PASSWORD })).json.code).toBe("TOTP_ALREADY_ENABLED");
    expect((await call(jar, "/passkey/list-user-passkeys")).status).toBe(200);
  });

  it("requires a step-up for backup-code regeneration; TOTP step-up sets the per-session timestamp and expires", async () => {
    const denied = await call(jar, "/two-factor/generate-backup-codes", { password: PASSWORD });
    expect(denied.status).toBe(403);
    expect(denied.json.code).toBe("STEP_UP_REQUIRED");

    expect((await call(jar, "/step-up/totp", { code: "000000" })).status).toBe(401);
    const code = await totpFromUri(totpUri);
    const ok = await call(jar, "/step-up/totp", { code });
    expect(ok.status).toBe(200);
    // derselbe Code ein zweites Mal: abgelehnt
    expect((await call(jar, "/step-up/totp", { code })).status).toBe(401);

    const sessions = await database.select().from(authSession);
    const confirmed = sessions.filter((s) => s.stepUpAt);
    expect(confirmed).toHaveLength(1);
    const at = confirmed[0].stepUpAt!;
    expect(evaluateStepUp(at, new Date(at.getTime() + 299_000), 300).ok).toBe(true);
    expect(evaluateStepUp(at, new Date(at.getTime() + 301_000), 300).ok).toBe(false);

    const regenerated = await call(jar, "/two-factor/generate-backup-codes", { password: PASSWORD });
    expect(regenerated.status).toBe(200);
    expect(regenerated.json.backupCodes).toHaveLength(10);

    // abgelaufene Bestätigung → wieder gesperrt
    await database.update(authSession).set({ stepUpAt: new Date(Date.now() - 301_000) }).where(eq(authSession.id, confirmed[0].id));
    expect((await call(jar, "/two-factor/generate-backup-codes", { password: PASSWORD })).json.code).toBe("STEP_UP_REQUIRED");
  });

  it("asks for TOTP on later password logins; step-up does not carry over to a new session", async () => {
    expect((await call(jar, "/sign-out", {})).status).toBe(200);
    const login = new Jar();
    const r = await call(login, "/sign-in/email", { email: EMAIL, password: PASSWORD });
    expect(r.status).toBe(200);
    expect(r.json.twoFactorRedirect).toBe(true);
    const v = await call(login, "/two-factor/verify-totp", { code: await totpFromUri(totpUri) });
    expect(v.status).toBe(200);
    const s = await call(login, "/get-session");
    expect((s.json.user as { email: string }).email).toBe(EMAIL);
    expect((s.json.session as { stepUpAt?: string | null }).stepUpAt ?? null).toBeNull();
  });

  it("never creates a second user, even if the configured email changes", async () => {
    process.env.TE_ADMIN_EMAIL = "new-owner@example.org";
    try {
      expect((await call(new Jar(), "/sign-in/email", { email: "new-owner@example.org", password: PASSWORD })).status).toBe(401);
      expect(await database.select().from(authUser)).toHaveLength(1);
    } finally {
      process.env.TE_ADMIN_EMAIL = EMAIL;
    }
  });

  it("writes audit events with actor user:<email>", async () => {
    const rows = await database.select().from(schema.auditEvents).where(like(schema.auditEvents.kind, "auth.%"));
    const kinds = new Set(rows.map((r) => r.kind));
    for (const k of ["auth.login.failure", "auth.login.success", "auth.login.password_ok", "auth.totp.enrolled", "auth.step_up.success", "auth.step_up.failure", "auth.backup_codes.regenerated", "auth.logout"]) expect(kinds).toContain(k);
    expect(rows.every((r) => r.actor.startsWith("user:"))).toBe(true);
    expect(rows.some((r) => r.actor === `user:${EMAIL}`)).toBe(true);
    // keine Geheimnisse im Audit
    expect(JSON.stringify(rows)).not.toContain(PASSWORD);
  });
});
