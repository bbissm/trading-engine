import "server-only";
import { hasDb } from "@/db/client";

export type Loaded<T> = { ok: true; data: T } | { ok: false; reason: "no-db" | "error"; message?: string };

/**
 * Runs a data loader; without a configured database or when the query fails (database unreachable,
 * migration missing) the page renders a setup hint instead of crashing.
 */
export async function guard<T>(load: () => Promise<T>): Promise<Loaded<T>> {
  if (!hasDb()) return { ok: false, reason: "no-db" };
  try {
    return { ok: true, data: await load() };
  } catch (e) {
    console.error("[data]", e);
    const cause = e instanceof Error && e.cause instanceof Error ? e.cause.message : undefined;
    return { ok: false, reason: "error", message: cause ?? (e instanceof Error ? e.message : String(e)) };
  }
}
