import { describe, expect, it } from "vitest";
import { activeSection, activeTab, backTarget, depth, DESKTOP_NAV, MORE, sectionTitle, TABS } from "./nav";

describe("navigation", () => {
  it("has the five tabs and the sections behind Mehr", () => {
    expect(TABS.map((t) => t.href)).toEqual(["/", "/signals", "/autopilot", "/portfolio", "/more"]);
    expect(MORE.map((t) => t.href)).toEqual(["/scanner", "/paper", "/strategies", "/journal", "/operations"]);
    expect(DESKTOP_NAV).toHaveLength(9);
    expect(DESKTOP_NAV.some((t) => t.href === "/more")).toBe(false);
  });

  it("marks the right tab as active", () => {
    expect(activeTab("/")).toBe("/");
    expect(activeTab("/signals")).toBe("/signals");
    expect(activeTab("/autopilot")).toBe("/autopilot");
    expect(activeTab("/portfolio")).toBe("/portfolio");
    expect(activeTab("/scanner")).toBe("/more");
    expect(activeTab("/operations")).toBe("/more");
    expect(activeTab("/more")).toBe("/more");
    expect(activeTab("/instruments/KRAKEN%3ABTC%2FUSD")).toBe("/more");
    expect(activeSection("/instruments/KRAKEN%3ABTC%2FUSD")).toBe("/scanner");
    expect(activeSection("/journal")).toBe("/journal");
    expect(activeSection("/unknown")).toBe("/");
  });

  it("knows where back leads", () => {
    expect(backTarget("/")).toBeUndefined();
    expect(backTarget("/signals")).toBeUndefined();
    expect(backTarget("/more")).toBeUndefined();
    expect(backTarget("/instruments/KRAKEN%3ABTC%2FUSD")).toEqual({ href: "/scanner", label: "Scanner" });
    expect(backTarget("/operations")).toEqual({ href: "/more", label: "Mehr" });
    expect(sectionTitle("/strategies")).toBe("Strategien & Lernlabor");
  });

  it("orders pages by depth for the slide direction", () => {
    expect(depth("/")).toBe(0);
    expect(depth("/more")).toBe(0);
    expect(depth("/signals")).toBe(0);
    expect(depth("/scanner")).toBe(1);
    expect(depth("/instruments/KRAKEN%3ABTC%2FUSD")).toBe(2);
  });
});
