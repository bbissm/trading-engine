"use server";

import { refresh } from "next/cache";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { getAuth } from "@/lib/auth/server";

/**
 * Sitzungen beenden. Die Sitzungstoken verlassen den Server nie: der Client kennt nur die Sitzungs-ID,
 * das Token wird hier über die eigene Sitzungsliste aufgelöst.
 */
export async function revokeSessionAction(id: string): Promise<{ error?: string }> {
  const h = await headers();
  const auth = getAuth();
  const current = await auth.api.getSession({ headers: h });
  if (!current) return { error: "Nicht angemeldet." };
  const sessions = await auth.api.listSessions({ headers: h });
  const target = sessions.find((s) => s.id === id);
  if (!target) return { error: "Sitzung nicht gefunden." };
  await auth.api.revokeSession({ headers: h, body: { token: target.token } });
  if (target.id === current.session.id) redirect("/login");
  refresh();
  return {};
}

/** Überall abmelden (alle Sitzungen inklusive dieser). */
export async function revokeAllSessionsAction(): Promise<void> {
  await getAuth().api.revokeSessions({ headers: await headers() });
  redirect("/login");
}
