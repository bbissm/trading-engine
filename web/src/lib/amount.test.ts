import { describe, expect, it } from "vitest";
import { compareDecimal, normalizeAmount, parseStartCash, toScaled } from "./amount";

describe("amount", () => {
  it("normalises user input without float conversion", () => {
    expect(normalizeAmount(" 10’000.50 ")).toBe("10000.50");
    expect(normalizeAmount("10'000,5")).toBe("10000.5");
    expect(normalizeAmount("007")).toBe("7");
    expect(normalizeAmount("0.10")).toBe("0.10");
    for (const bad of ["", "abc", "-5", "1e5", "1.2.3", "1.", ".5", "NaN", "12345678901234"]) expect(normalizeAmount(bad)).toBeNull();
  });

  it("scales and compares decimal strings exactly", () => {
    expect(toScaled("1.5")).toBe(15_000_000_000n);
    expect(toScaled("-0.0000000001")).toBe(-1n);
    expect(compareDecimal("0.1", "0.10")).toBe(0);
    expect(compareDecimal("100000000.01", "100000000")).toBe(1);
    expect(compareDecimal("-2", "1")).toBe(-1);
    // beyond float precision
    expect(compareDecimal("9007199254740993", "9007199254740992")).toBe(1);
    expect(() => toScaled("x")).toThrow();
  });

  it("accepts a start capital > 0 and ≤ 100 000 000 with at most two decimals", () => {
    expect(parseStartCash("10000")).toBe("10000");
    expect(parseStartCash("100’000’000")).toBe("100000000");
    expect(parseStartCash("0.01")).toBe("0.01");
    for (const bad of ["0", "0.00", "100000000.01", "250000000", "10.001", "-1", "zehn"]) expect(parseStartCash(bad)).toBeNull();
  });
});
