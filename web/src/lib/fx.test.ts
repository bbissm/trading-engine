import { describe, expect, it } from "vitest";
import { add, dec, str } from "./stats";
import { fixingDay, pickRate, reportingSplit, tradeInChf, type FxRate } from "./fx";

const rate = (date: string, r: string): FxRate => ({ base: "USD", quote: "CHF", date, rate: r, source: "ecb-frankfurter" });
const RATES = [rate("2026-10-01", "0.8000"), rate("2026-10-02", "0.8100"), rate("2026-10-05", "0.8200"), rate("2026-09-01", "0.9000")];

describe("fixing selection", () => {
  it("uses the Zurich calendar day", () => {
    // 23:30 UTC on 2 Oct is already 3 Oct in Zurich (CEST)
    expect(fixingDay(new Date("2026-10-02T23:30:00Z"))).toBe("2026-10-03");
    expect(fixingDay(new Date("2026-10-02T21:30:00Z"))).toBe("2026-10-02");
  });

  it("weekend → previous fixing (Friday), marked as previous", () => {
    const sat = pickRate(RATES, "USD", new Date("2026-10-03T10:00:00Z"))!;
    expect(sat).toEqual({ rate: "0.8100", date: "2026-10-02", source: "ecb-frankfurter", previous: true });
    expect(pickRate(RATES, "USD", new Date("2026-10-05T10:00:00Z"))).toMatchObject({ rate: "0.8200", previous: false });
  });

  it("missing rate: nothing on/before the day, or older than 7 days, or another pair", () => {
    expect(pickRate(RATES, "USD", new Date("2026-08-15T10:00:00Z"))).toBeNull();
    expect(pickRate(RATES, "USD", new Date("2026-09-20T10:00:00Z"))).toBeNull(); // 2026-09-01 is too old
    expect(pickRate(RATES, "EUR", new Date("2026-10-02T10:00:00Z"))).toBeNull();
    expect(pickRate(RATES, "CHF", new Date("2026-10-02T10:00:00Z"))).toMatchObject({ rate: "1", previous: false });
  });
});

describe("split into trading result and FX effect (engine core/pnl.py::to_reporting)", () => {
  // expected values computed with the engine: uv run python -c "... to_reporting(...)"
  const cases = [
    { i: { entryValue: "1280", exitValue: "1240", entryFees: "3.3", exitFees: "3.2", fxEntry: "0.9012", fxExit: "0.8875" }, total: "-58.84996", trading: "-41.31396", fx: "-17.536" },
    { i: { entryValue: "1930.5", exitValue: "2101.25", entryFees: "5.02", exitFees: "5.47", fxEntry: "0.79", fxExit: "0.8123" }, total: "173.341294", trading: "130.291144", fx: "43.05015" },
    { i: { entryValue: "600", exitValue: "495", entryFees: "1.5", exitFees: "1.25", fxEntry: "0.85", fxExit: "0.85" }, total: "-91.5875", trading: "-91.5875", fx: "0" },
  ];
  it.each(cases)("matches to_reporting exactly ($total)", ({ i, total, trading, fx }) => {
    const r = reportingSplit(i);
    expect(r).toEqual({ total, trading, fxEffect: fx });
    expect(str(add(dec(r.trading), dec(r.fxEffect)))).toBe(r.total);
  });

  it("trade in CHF needs both rates", () => {
    const t = { currency: "USD", openedAt: new Date("2026-10-01T08:00:00Z"), closedAt: new Date("2026-10-04T08:00:00Z"), entryValue: "1000", exitValue: "1100", entryFees: "2", exitFees: "2" };
    const ok = tradeInChf(t, RATES);
    expect(ok.entry?.date).toBe("2026-10-01");
    expect(ok.exit).toMatchObject({ date: "2026-10-02", previous: true }); // Sunday → Friday
    expect(ok.split?.total).toBe(reportingSplit({ ...t, fxEntry: "0.8", fxExit: "0.81" }).total);
    const missing = tradeInChf({ ...t, openedAt: new Date("2026-08-01T08:00:00Z") }, RATES);
    expect(missing.entry).toBeNull();
    expect(missing.split).toBeNull();
  });
});
