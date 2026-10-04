"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useTransition } from "react";
import { activeTab, backTarget, sectionTitle, TABS } from "@/lib/nav";
import { Icon } from "./icons";

/**
 * Top bar on phones: app name on the tabs, back arrow on sub pages.
 * A swipe from the left edge to the right also goes back (like iOS apps).
 */
export function MobileTopBar() {
  const pathname = usePathname();
  const router = useRouter();
  const back = backTarget(pathname);
  const start = useRef<{ x: number; y: number } | null>(null);
  const [refreshing, startRefresh] = useTransition();

  useEffect(() => {
    if (!back) return;
    const onStart = (e: TouchEvent) => {
      const t = e.touches[0];
      start.current = t.clientX < 28 ? { x: t.clientX, y: t.clientY } : null;
    };
    const onEnd = (e: TouchEvent) => {
      const s = start.current;
      start.current = null;
      if (!s) return;
      const t = e.changedTouches[0];
      if (t.clientX - s.x > 70 && Math.abs(t.clientY - s.y) < 60) router.push(back.href);
    };
    window.addEventListener("touchstart", onStart, { passive: true });
    window.addEventListener("touchend", onEnd, { passive: true });
    return () => {
      window.removeEventListener("touchstart", onStart);
      window.removeEventListener("touchend", onEnd);
    };
  }, [back, router]);

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-surface/90 pt-[env(safe-area-inset-top)] backdrop-blur md:hidden">
      <div className="flex h-12 items-center gap-2 px-2">
        {back ? (
          <Link href={back.href} className="-ml-1 flex h-11 items-center gap-0.5 rounded-md pr-2 text-[15px] font-medium text-accent active:opacity-60" aria-label={`Zurück zu ${back.label}`}>
            <Icon name="chevronLeft" size={26} />
            {back.label}
          </Link>
        ) : (
          <Link href="/" className="flex items-center gap-2 pl-2 active:opacity-60" aria-label={`TradingEngine · ${sectionTitle(pathname)}`}>
            <span aria-hidden className="inline-block h-3.5 w-3.5 rounded-sm bg-accent" />
            <span className="text-[15px] font-semibold tracking-tight">TradingEngine</span>
          </Link>
        )}
        {/* Home-screen apps have no reload button: fetch the server data again */}
        <button
          type="button"
          onClick={() => startRefresh(() => router.refresh())}
          disabled={refreshing}
          aria-label="Aktualisieren"
          className="ml-auto flex h-11 w-11 items-center justify-center rounded-md text-ink-2 active:opacity-60"
        >
          <Icon name="refresh" size={20} className={refreshing ? "animate-spin" : ""} />
        </button>
      </div>
    </header>
  );
}

/** Bottom tab bar with icons; the active section is highlighted. */
export function MobileTabBar() {
  const pathname = usePathname();
  const active = activeTab(pathname);
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden" aria-label="Hauptnavigation">
      <ul className="grid grid-cols-5">
        {TABS.map((t) => {
          const on = t.href === active;
          return (
            <li key={t.href}>
              <Link
                href={t.href}
                aria-current={on ? "page" : undefined}
                className={`flex h-14 flex-col items-center justify-center gap-0.5 text-[10.5px] font-medium transition-colors active:opacity-60 ${on ? "text-accent" : "text-muted"}`}
              >
                <span className={`flex h-7 w-12 items-center justify-center rounded-full transition-colors ${on ? "bg-[color-mix(in_oklab,var(--accent)_14%,transparent)]" : ""}`}>
                  <Icon name={t.icon} size={22} strokeWidth={on ? 2.2 : 1.8} />
                </span>
                {t.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
