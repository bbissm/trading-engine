"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { checkCredentials, createSessionToken, SESSION_COOKIE, sessionCookieOptions } from "@/lib/session";

/** Only internal paths as redirect target (no open redirect). */
const safeNext = (next: string) => (next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/login") ? next : "/");

export async function loginAction(_prev: { error?: string } | undefined, form: FormData): Promise<{ error?: string }> {
  const user = String(form.get("user") ?? "");
  const password = String(form.get("password") ?? "");
  if (!checkCredentials(user, password)) {
    // slows down guessing
    await new Promise((r) => setTimeout(r, 800));
    return { error: "Benutzer oder Passwort falsch." };
  }
  const { token, expires } = await createSessionToken();
  (await cookies()).set(SESSION_COOKIE, token, sessionCookieOptions(expires));
  redirect(safeNext(String(form.get("next") ?? "/")));
}

export async function logoutAction() {
  (await cookies()).delete(SESSION_COOKIE);
  redirect("/login");
}
