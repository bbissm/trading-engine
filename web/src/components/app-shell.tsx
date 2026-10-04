"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { APP_NAME } from "@/lib/nav";
import { MobileTabBar, MobileTopBar } from "./mobile-shell";
import { Nav } from "./nav";

/** Top navigation on desktop, top bar + tab bar on phones; login and first-time setup get no chrome. */
export function AppShell({ children, navActions }: { children: ReactNode; navActions?: ReactNode }) {
  const pathname = usePathname();
  if (pathname.startsWith("/login") || pathname.startsWith("/setup")) return <>{children}</>;
  return (
    <>
      <MobileTopBar />
      <header className="sticky top-0 z-40 hidden border-b border-line bg-surface/90 backdrop-blur md:block">
        <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-4 px-4">
          <Link href="/" className="flex shrink-0 items-center gap-2">
            <span aria-hidden className="inline-block h-3 w-3 rounded-sm bg-accent" />
            <span className="text-sm font-semibold">{APP_NAME}</span>
          </Link>
          <Nav />
          <div className="ml-auto flex shrink-0 items-center gap-2">{navActions}</div>
        </div>
      </header>
      <div className="mx-auto max-w-[1400px] px-4 pt-4 md:py-6">
        <main className="min-w-0 overflow-x-clip pb-[calc(5rem+env(safe-area-inset-bottom))] md:pb-16">{children}</main>
      </div>
      <MobileTabBar />
    </>
  );
}
