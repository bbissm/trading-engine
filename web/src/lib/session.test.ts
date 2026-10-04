import { afterEach, describe, expect, it } from "vitest";
import { userName } from "./session";

describe("userName", () => {
  afterEach(() => {
    delete process.env.TE_USER;
    delete process.env.TE_ADMIN_EMAIL;
  });

  it("prefers TE_USER, then the local part of TE_ADMIN_EMAIL, then admin", () => {
    expect(userName()).toBe("admin");
    process.env.TE_ADMIN_EMAIL = " Martin.B@Example.org ";
    expect(userName()).toBe("martin.b");
    process.env.TE_USER = "martin";
    expect(userName()).toBe("martin");
  });
});
