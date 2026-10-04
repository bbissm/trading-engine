import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { checkCredentials, createSessionToken, SESSION_DAYS, shouldRenew, verifySessionToken } from "./session";

describe("login session", () => {
  beforeEach(() => {
    process.env.TE_PASSWORD = "secret-pw";
    process.env.TE_USER = "admin";
  });
  afterEach(() => {
    delete process.env.TE_PASSWORD;
    delete process.env.TE_USER;
  });

  it("issues tokens that verify until they expire", async () => {
    const now = Date.UTC(2026, 9, 4);
    const { token, expires } = await createSessionToken(now);
    expect(expires.getTime()).toBe(now + SESSION_DAYS * 86_400_000);
    expect(await verifySessionToken(token, now + 1000)).toBe(expires.getTime());
    expect(await verifySessionToken(token, expires.getTime() + 1)).toBeNull();
    expect(shouldRenew(expires.getTime(), now)).toBe(false);
    expect(shouldRenew(expires.getTime(), now + 40 * 86_400_000)).toBe(true);
  });

  it("rejects tampered tokens and tokens after a password change", async () => {
    const { token } = await createSessionToken();
    const [v, exp, sig] = token.split(".");
    expect(await verifySessionToken(`${v}.${Number(exp) + 1000}.${sig}`)).toBeNull();
    expect(await verifySessionToken("garbage")).toBeNull();
    expect(await verifySessionToken(undefined)).toBeNull();
    process.env.TE_PASSWORD = "new-pw";
    expect(await verifySessionToken(token)).toBeNull();
  });

  it("checks credentials", () => {
    expect(checkCredentials("admin", "secret-pw")).toBe(true);
    expect(checkCredentials(" admin ", "secret-pw")).toBe(true);
    expect(checkCredentials("admin", "wrong")).toBe(false);
    expect(checkCredentials("root", "secret-pw")).toBe(false);
    delete process.env.TE_PASSWORD;
    expect(checkCredentials("admin", "")).toBe(false);
  });
});
