import { describe, expect, it } from "vitest";
import { BOM, csvCell, isExportKind, parseExportFilter, toCsv, zurichTime } from "./export";

describe("csv format", () => {
  it("escapes separators, quotes and line breaks; numbers keep the decimal point", () => {
    expect(csvCell("Stop")).toBe("Stop");
    expect(csvCell("a;b")).toBe('"a;b"');
    expect(csvCell('sagt "nein"')).toBe('"sagt ""nein"""');
    expect(csvCell("zwei\nZeilen")).toBe('"zwei\nZeilen"');
    expect(csvCell("-46.5000000000")).toBe("-46.5000000000");
    expect(csvCell(12.5)).toBe("12.5");
    expect(csvCell(null)).toBe("");
    expect(csvCell(true)).toBe("true");
    expect(csvCell(new Date("2026-10-04T12:00:00Z"))).toBe("2026-10-04T12:00:00.000Z");
  });

  it("neutralises formula-like text for spreadsheets", () => {
    expect(csvCell("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(csvCell("-Stop")).toBe("'-Stop");
    expect(csvCell("@x")).toBe("'@x");
  });

  it("writes BOM, optional note, header and CRLF rows", () => {
    const csv = toCsv(["mode", "net"], [["PAPER", "1.5"], ["LIVE", "-2"]], "Hinweis");
    expect(csv).toBe(`${BOM}Hinweis\r\nmode;net\r\nPAPER;1.5\r\nLIVE;-2\r\n`);
  });

  it("formats Europe/Zurich local time next to UTC (summer and winter time)", () => {
    expect(zurichTime(new Date("2026-07-01T12:00:00Z"))).toBe("2026-07-01 14:00:00");
    expect(zurichTime(new Date("2026-12-01T12:00:00Z"))).toBe("2026-12-01 13:00:00");
    expect(zurichTime(null)).toBe("");
  });

  it("accepts only known kinds and sane filters", () => {
    expect(isExportKind("trades")).toBe(true);
    expect(isExportKind("passwords")).toBe(false);
    expect(parseExportFilter(new URLSearchParams("account=paper-a&episode=2&year=2026"))).toEqual({ account: "paper-a", episode: 2, year: 2026 });
    // episode without account is meaningless; absurd years are dropped
    expect(parseExportFilter(new URLSearchParams("episode=2&year=1900"))).toEqual({ account: undefined, episode: undefined, year: undefined });
  });
});
