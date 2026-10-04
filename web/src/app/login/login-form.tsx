"use client";

import { useActionState } from "react";
import { loginAction } from "./actions";

export function LoginForm({ next }: { next: string }) {
  const [state, action, pending] = useActionState(loginAction, undefined);
  const input = "w-full !rounded-xl !px-3 !py-3 !text-base";
  return (
    <form action={action} className="space-y-3">
      <input type="hidden" name="next" value={next} />
      <input name="user" autoComplete="username" placeholder="Benutzer" defaultValue="admin" autoCapitalize="none" autoCorrect="off" aria-label="Benutzer" className={input} required />
      <input name="password" type="password" autoComplete="current-password" placeholder="Passwort" aria-label="Passwort" className={input} required autoFocus />
      {state?.error && <p className="text-sm text-critical">{state.error}</p>}
      <button type="submit" disabled={pending} className="btn w-full justify-center !rounded-xl !py-3 !text-base disabled:opacity-60">
        {pending ? "Anmelden …" : "Anmelden"}
      </button>
    </form>
  );
}
