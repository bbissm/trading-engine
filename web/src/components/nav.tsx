"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { activeSection, DESKTOP_NAV } from "@/lib/nav";
import { Icon } from "./icons";

/** Desktop top navigation (phones use the bottom tab bar). Icons appear once there is room for them. */
export function Nav() {
  const active = activeSection(usePathname());
  return (
    <nav className="flex min-w-0 items-center gap-0.5 overflow-x-auto" aria-label="Hauptnavigation">
      {DESKTOP_NAV.map((i) => {
        const on = i.href === active;
        return (
          <Link
            key={i.href}
            href={i.href}
            title={i.label}
            aria-current={on ? "page" : undefined}
            className={`flex items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-sm ${on ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:bg-surface-2"}`}
          >
            <Icon name={i.icon} size={16} className={`hidden xl:block ${on ? "text-accent" : "text-muted"}`} />
            {i.short ?? i.label}
          </Link>
        );
      })}
    </nav>
  );
}
