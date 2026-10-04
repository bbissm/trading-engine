import type { IconName } from "@/components/icons";

export interface NavItem {
  href: string;
  label: string;
  /** Short label for the desktop top navigation. */
  short?: string;
  icon: IconName;
}

export const APP_NAME = "TradingEngine";

/** Bottom tab bar on phones (the most used sections). */
export const TABS: NavItem[] = [
  { href: "/", label: "Übersicht", icon: "home" },
  { href: "/signals", label: "Signale", icon: "bolt" },
  { href: "/autopilot", label: "Autopilot", icon: "cpu" },
  { href: "/portfolio", label: "Portfolio", icon: "layers" },
  { href: "/more", label: "Mehr", icon: "gear" },
];

/** Everything else lives behind "Mehr" (gear). */
export const MORE: NavItem[] = [
  { href: "/scanner", label: "Scanner", icon: "search" },
  { href: "/paper", label: "Paper-Lab", icon: "flask" },
  { href: "/strategies", label: "Strategien & Lernlabor", short: "Strategien", icon: "target" },
  { href: "/journal", label: "Journal", icon: "book" },
  { href: "/operations", label: "Verbindungen & Betrieb", short: "Betrieb", icon: "plug" },
];

/** Desktop top navigation (everything visible, no "Mehr"). */
export const DESKTOP_NAV: NavItem[] = [...TABS.slice(0, 4), ...MORE];

/** Detail pages without their own nav entry belong to a section (instrument analysis → scanner). */
const PARENT: Record<string, string> = { "/instruments": "/scanner" };

const section = (pathname: string) => {
  const s = "/" + (pathname.split("/").filter(Boolean)[0] ?? "");
  return PARENT[s] ?? s;
};

/** Active entry of the desktop navigation. */
export function activeSection(pathname: string): string {
  const s = section(pathname);
  return DESKTOP_NAV.find((t) => t.href === s)?.href ?? "/";
}

/** Active tab (sections under "Mehr" highlight the gear). */
export function activeTab(pathname: string): string {
  const s = section(pathname);
  if (s === "/more" || MORE.some((m) => m.href === s)) return "/more";
  return TABS.find((t) => t.href === s)?.href ?? "/";
}

export function sectionTitle(pathname: string): string {
  const s = section(pathname);
  return [...TABS, ...MORE].find((t) => t.href === s)?.label ?? APP_NAME;
}

/** Back target: detail pages → their section, sections under "Mehr" → "Mehr", tabs → none. */
export function backTarget(pathname: string): { href: string; label: string } | undefined {
  const parts = pathname.split("/").filter(Boolean);
  const s = section(pathname);
  if (parts.length >= 2) return { href: s, label: sectionTitle(s) };
  if (MORE.some((m) => m.href === s)) return { href: "/more", label: "Mehr" };
  return undefined;
}

/** Navigation depth (direction of the page transition): tabs 0, sections under "Mehr" and detail pages deeper. */
export function depth(pathname: string): number {
  const parts = pathname.split("/").filter(Boolean);
  const underMore = MORE.some((m) => m.href === section(pathname)) ? 1 : 0;
  return (parts.length >= 2 ? 1 : 0) + underMore;
}
