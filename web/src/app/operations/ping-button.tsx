"use client";

import { useActionState } from "react";
import { pingAction } from "./actions";

export function PingButton() {
  const [state, action, pending] = useActionState(pingAction, undefined);
  return (
    <form action={action} className="flex flex-wrap items-center gap-3">
      <button type="submit" disabled={pending} className="btn disabled:opacity-60">
        {pending ? "Sende …" : "Engine-Ping senden"}
      </button>
      {state?.error && <p className="text-sm text-critical">{state.error}</p>}
    </form>
  );
}
