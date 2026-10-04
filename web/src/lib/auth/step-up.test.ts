import { describe, expect, it } from "vitest";
import { effectiveMaxAge, evaluateStepUp, STEP_UP_DEFAULT_MAX_AGE_SECONDS } from "./step-up-core";

describe("step-up expiry", () => {
  const at = new Date("2026-10-04T10:00:00Z");
  const plus = (s: number) => new Date(at.getTime() + s * 1000);

  it("passes within 5 minutes and expires afterwards", () => {
    expect(STEP_UP_DEFAULT_MAX_AGE_SECONDS).toBe(300);
    expect(evaluateStepUp(at, plus(0), 300)).toEqual({ ok: true, at });
    expect(evaluateStepUp(at, plus(299), 300).ok).toBe(true);
    expect(evaluateStepUp(at, plus(300), 300).ok).toBe(true);
    const expired = evaluateStepUp(at, plus(301), 300);
    expect(expired.ok).toBe(false);
    if (!expired.ok) expect(expired.reason).toMatch(/abgelaufen/);
  });

  it("honours a shorter max age requested by the caller", () => {
    expect(evaluateStepUp(at, plus(61), 60).ok).toBe(false);
    expect(evaluateStepUp(at, plus(59), 60).ok).toBe(true);
  });

  it("fails without a confirmation, with garbage, or with a timestamp from the future", () => {
    expect(evaluateStepUp(null, plus(1), 300).ok).toBe(false);
    expect(evaluateStepUp(undefined, plus(1), 300).ok).toBe(false);
    expect(evaluateStepUp("not a date", plus(1), 300).ok).toBe(false);
    expect(evaluateStepUp(plus(60), at, 300).ok).toBe(false);
    expect(evaluateStepUp(at, plus(1), 0).ok).toBe(false);
  });

  it("accepts ISO strings as stored by some drivers", () => {
    expect(evaluateStepUp(at.toISOString(), plus(10), 300).ok).toBe(true);
  });

  it("lets the environment shorten but never lengthen the window", () => {
    expect(effectiveMaxAge(300, {})).toBe(300);
    expect(effectiveMaxAge(300, { TE_STEP_UP_MAX_AGE_SECONDS: "20" })).toBe(20);
    expect(effectiveMaxAge(300, { TE_STEP_UP_MAX_AGE_SECONDS: "9999" })).toBe(300);
    expect(effectiveMaxAge(300, { TE_STEP_UP_MAX_AGE_SECONDS: "abc" })).toBe(300);
    expect(effectiveMaxAge(-1, {})).toBe(0);
  });
});
