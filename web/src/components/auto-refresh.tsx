"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Re-fetches the server data of the current page every few seconds (no realtime service in V1); pauses in hidden tabs. */
export function AutoRefresh({ seconds = 5 }: { seconds?: number }) {
  const router = useRouter();
  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState === "visible") router.refresh();
    }, seconds * 1000);
    return () => clearInterval(id);
  }, [router, seconds]);
  return null;
}
