import { readFileSync } from "node:fs";
import { getTableName } from "drizzle-orm";
import { describe, expect, it } from "vitest";
import { AUTH_TABLES, authSchema } from "@/db/auth-schema";

describe("auth tables", () => {
  it("lists every Better Auth table", () => {
    expect(Object.values(authSchema).map(getTableName).sort()).toEqual([...AUTH_TABLES].sort());
  });

  it("are explicitly revoked from the engine role", () => {
    const roles = readFileSync("scripts/roles.mjs", "utf8");
    const m = /const AUTH_TABLES = (\[[^\]]+\]);/.exec(roles);
    expect(m).not.toBeNull();
    expect((JSON.parse(m![1]) as string[]).sort()).toEqual([...AUTH_TABLES].sort());
    expect(roles).toMatch(/revoke all privileges on "\$\{table\}" from \$\{ROLE\}/);
    // die Rücknahme steht nach dem pauschalen «grant select»
    expect(roles.indexOf("AUTH_TABLES.map")).toBeGreaterThan(roles.indexOf("grant select on all tables"));
  });
});
