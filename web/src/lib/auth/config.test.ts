import { describe, expect, it } from "vitest";
import { isValidApiToken } from "./api-token";
import { authMode, isSelfAuthenticatedPath, safeNext } from "./config";

const full = { TE_PASSWORD: "pw-pw-pw-pw-pw", TE_ADMIN_EMAIL: "me@example.org", BETTER_AUTH_SECRET: "x".repeat(40), BETTER_AUTH_URL: "https://te.example.org", DATABASE_URL: "postgres://x" };

describe("auth mode", () => {
  it("is open locally without a password, 503 on Vercel", () => {
    expect(authMode({})).toEqual({ mode: "open" });
    expect(authMode({ VERCEL_ENV: "development" })).toEqual({ mode: "open" });
    expect(authMode({ VERCEL_ENV: "production" }).mode).toBe("unconfigured");
    expect(authMode({ VERCEL_ENV: "preview" }).mode).toBe("unconfigured");
  });

  it("requires every value once a password is set", () => {
    expect(authMode(full)).toEqual({ mode: "enabled" });
    const m = authMode({ ...full, BETTER_AUTH_SECRET: "", DATABASE_URL: undefined });
    expect(m).toEqual({ mode: "unconfigured", missing: ["BETTER_AUTH_SECRET", "DATABASE_URL"] });
  });
});

describe("paths", () => {
  it("leaves self-authenticating routes to themselves", () => {
    for (const p of ["/api/auth/sign-in/email", "/api/telegram", "/api/cron/watchdog"]) expect(isSelfAuthenticatedPath(p)).toBe(true);
    for (const p of ["/api/ops/database", "/api/step-up", "/", "/api/authx", "/api/telegramx"]) expect(isSelfAuthenticatedPath(p)).toBe(false);
  });

  it("only redirects to internal paths after login", () => {
    expect(safeNext("/signals?x=1")).toBe("/signals?x=1");
    for (const bad of ["//evil.example", "/\\evil", "https://evil.example", "/login", "/setup", "", null, undefined]) expect(safeNext(bad)).toBe("/");
  });
});

describe("API token", () => {
  const token = "t".repeat(40);
  it("accepts only the exact bearer token", () => {
    expect(isValidApiToken(`Bearer ${token}`, token)).toBe(true);
    expect(isValidApiToken(`bearer ${token}`, token)).toBe(true);
    expect(isValidApiToken(`Bearer ${token}x`, token)).toBe(false);
    expect(isValidApiToken(`Bearer ${token.slice(1)}`, token)).toBe(false);
    expect(isValidApiToken(`Basic ${btoa("admin:pw")}`, token)).toBe(false);
    expect(isValidApiToken(null, token)).toBe(false);
  });
  it("is disabled without a sufficiently long token", () => {
    expect(isValidApiToken("Bearer short", "short")).toBe(false);
    expect(isValidApiToken(`Bearer ${token}`, undefined)).toBe(false);
  });
});
