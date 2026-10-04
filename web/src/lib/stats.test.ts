import { describe, expect, it } from "vitest";
import { bootstrapMeanCI, dec, div, expectancyR, expectancyRCI, MIN_TRADES_FOR_CI, mul, profitFactor, rMultiple, sampleCaveat, str, toFixed } from "./stats";

describe("exact decimals", () => {
  it("parses, multiplies, divides and rounds without float errors", () => {
    expect(str(dec("0.1") + dec("0.2"))).toBe("0.3");
    expect(str(mul(dec("64250.5"), dec("0.03")))).toBe("1927.515");
    expect(str(div(dec("1"), dec("3"))!, 4)).toBe("0.3333");
    expect(toFixed(dec("1.005"), 2)).toBe("1.01");
    expect(toFixed(dec("-1.005"), 2)).toBe("-1.01");
    expect(toFixed(dec("-0.0001"), 2)).toBe("0.00");
    expect(div(dec("1"), dec("0"))).toBeNull();
    expect(() => dec("1e-7")).toThrow();
  });
});

describe("trade statistics", () => {
  it("profit factor = gains ÷ |losses|, undefined without losses", () => {
    expect(profitFactor(["42.2", "-46.5", "-41", "10"])).toBe("0.5966"); // 52.2 / 87.5
    expect(profitFactor(["5", "7"])).toBeNull();
    expect(profitFactor(["-5"])).toBe("0");
  });

  it("expectancy in R = mean(net ÷ planned risk), trades without planned risk are skipped", () => {
    const trades = [
      { net: "-46.5", plannedRisk: "40" }, // -1.1625
      { net: "-41", plannedRisk: "40" }, // -1.025
      { net: "42.2", plannedRisk: "40" }, // 1.055
      { net: "100", plannedRisk: "0" }, // no R
    ];
    expect(str(rMultiple(trades[0])!, 4)).toBe("-1.1625");
    expect(rMultiple(trades[3])).toBeNull();
    expect(expectancyR(trades)).toBe("-0.3775");
    expect(expectancyR([])).toBeNull();
  });

  it("bootstrap interval is deterministic for a fixed seed and brackets the mean", () => {
    const values = ["-1", "-1", "-0.5", "2", "1.5", "-1", "3", "-1", "0.2", "-0.8", "1.1", "-1"].map(dec);
    const a = bootstrapMeanCI(values)!;
    const b = bootstrapMeanCI(values)!;
    expect(a).toEqual(b);
    expect(a.n).toBe(12);
    const meanR = Number(str(div(values.reduce((s, x) => s + x, 0n), dec("12"))!, 4));
    expect(Number(a.ci[0])).toBeLessThan(meanR);
    expect(Number(a.ci[1])).toBeGreaterThan(meanR);
    // another seed gives a (slightly) different interval
    expect(bootstrapMeanCI(values, { seed: 7 })!.ci).not.toEqual(a.ci);
  });

  it("suppresses the interval below the minimum sample size", () => {
    const nine = Array.from({ length: MIN_TRADES_FOR_CI - 1 }, (_, i) => ({ net: String(i - 4), plannedRisk: "1" }));
    expect(expectancyRCI(nine)).toBeNull();
    expect(expectancyRCI([...nine, { net: "1", plannedRisk: "1" }])).not.toBeNull();
    expect(sampleCaveat(3)).toMatch(/Nur 3/);
    expect(sampleCaveat(0)).toMatch(/Keine/);
  });
});
