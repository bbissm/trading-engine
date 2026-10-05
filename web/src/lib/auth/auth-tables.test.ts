import { readFileSync } from "node:fs";
import { getTableName } from "drizzle-orm";
import { describe, expect, it } from "vitest";
import { AUTH_TABLES, authSchema } from "@/db/auth-schema";

describe("auth tables", () => {
  it("lists every Better Auth table", () => {
    expect(Object.values(authSchema).map(getTableName).sort()).toEqual([...AUTH_TABLES].sort());
  });

  it("are explicitly revoked from both engine roles", () => {
    const roles = readFileSync("scripts/roles.sql", "utf8");
    const m = /revoke all privileges on ([a-z_,\s]+?)\s+from te_engine, te_live/.exec(roles);
    expect(m).not.toBeNull();
    expect(m![1].split(",").map((t) => t.trim()).sort()).toEqual([...AUTH_TABLES].sort());
    // die Rücknahme steht nach dem pauschalen «grant select»
    expect(roles.indexOf("revoke all privileges on auth_")).toBeGreaterThan(roles.indexOf("grant select on all tables"));
  });
});
