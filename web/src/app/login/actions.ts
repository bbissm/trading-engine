"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { authMode } from "@/lib/auth/config";
import { getAuth } from "@/lib/auth/server";

/** Abmelden: beendet diese Sitzung (DB) und löscht die Cookies (nextCookies-Plugin). */
export async function logoutAction() {
  if (authMode().mode === "enabled") {
    try {
      await getAuth().api.signOut({ headers: await headers() });
    } catch (e) {
      console.error("[logout]", e);
    }
  }
  redirect("/login");
}
