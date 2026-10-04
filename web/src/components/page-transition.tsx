"use client";

import { usePathname } from "next/navigation";
import { useLayoutEffect, useRef, type ReactNode } from "react";
import { depth } from "@/lib/nav";

// Last path across navigations (the template remounts when the section changes, not within a section)
const history = { last: null as string | null };

/**
 * Page transition like native apps: deeper (detail, sub page) → slide in from the right,
 * back → from the left, tab switch → short fade. Reduced motion is respected (CSS).
 */
export function PageTransition({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const ref = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const last = history.last;
    history.last = pathname;
    const el = ref.current;
    if (last === null || last === pathname || !el) return;
    const from = depth(last);
    const to = depth(pathname);
    // same section (e.g. /projects ↔ /projects/x): template stays mounted → restart the animation
    el.classList.remove("page-forward", "page-back", "page-fade");
    void el.offsetWidth;
    el.classList.add(to > from ? "page-forward" : to < from ? "page-back" : "page-fade");
  }, [pathname]);

  return <div ref={ref}>{children}</div>;
}
