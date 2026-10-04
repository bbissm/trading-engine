import type { Loaded } from "@/lib/data/guard";

/** Shown instead of page content when no database is configured or it cannot be read. */
export function SetupHint({ state }: { state: Exclude<Loaded<unknown>, { ok: true }> }) {
  return (
    <div className="mb-4 rounded-xl border border-line bg-surface-2 p-4 text-sm">
      <div className="font-medium">{state.reason === "no-db" ? "Einrichtung unvollständig" : "Datenbank nicht lesbar"}</div>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-ink-2">
        {state.reason === "no-db" ? (
          <>
            <li>
              Keine Datenbank verbunden. <code>DATABASE_URL</code> setzen (Neon oder lokales Postgres) und <code>pnpm db:migrate</code> ausführen.
            </li>
            <li>Die Engine schreibt Marktdaten, Signale und Lebenszeichen in dieselbe Datenbank; ohne sie bleibt diese Seite leer.</li>
          </>
        ) : (
          <>
            <li>Die Abfrage ist fehlgeschlagen. Ist die Datenbank erreichbar und die Migration eingespielt (<code>pnpm db:migrate</code>)?</li>
            {state.message && <li className="break-words font-mono text-xs">{state.message}</li>}
          </>
        )}
      </ul>
    </div>
  );
}
