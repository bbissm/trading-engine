"use client";

import { useEffect, useState } from "react";
import { fetchStepUpStatus, useStepUp, type StepUpStatus } from "@/components/step-up";

/**
 * Step-up für die Live-Aktionen dieser Seite (Mandat aktivieren, Live-Freigabe, Positionen schliessen,
 * Fortsetzen): bestätigt die Sitzung mit Passkey oder TOTP. Die Server Actions prüfen selbst mit
 * `requireStepUp()`; dieser Balken macht nur sichtbar, ob die Bestätigung noch gilt, und erneuert sie.
 */
export function StepUpBar() {
  const { ensureStepUp, dialog } = useStepUp();
  const [status, setStatus] = useState<StepUpStatus | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => fetchStepUpStatus().then((s) => alive && setStatus(s));
    void load();
    const timer = setInterval(load, 20_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const until = status?.ok ? new Date(status.expiresAt).toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit" }) : null;
  return (
    <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface p-3 text-sm">
      {dialog}
      <span className="min-w-0 flex-1">
        {until ? (
          <>
            <strong>Step-up bestätigt</strong> – gültig bis {until}. Geschützte Live-Aktionen sind so lange möglich.
          </>
        ) : (
          <>
            <strong>Step-up nötig</strong> für Mandat, Live-Freigabe, Schliessen und Fortsetzen (Passkey oder TOTP, gilt 5 Minuten).
          </>
        )}
      </span>
      <button
        type="button"
        className="rounded-lg border border-line px-3 py-1.5 text-sm font-medium hover:bg-surface-2"
        onClick={async () => {
          if (await ensureStepUp()) setStatus(await fetchStepUpStatus());
        }}
      >
        {until ? "Erneuern" : "Jetzt bestätigen"}
      </button>
    </div>
  );
}
