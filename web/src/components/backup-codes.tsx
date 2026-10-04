"use client";

import { useState } from "react";

/** Backup-Codes anzeigen (nur einmal sichtbar) mit Kopierknopf. */
export function BackupCodes({ codes }: { codes: string[] }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="rounded-xl border border-line bg-surface-2 p-3">
      <ul className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-sm tabular" aria-label="Backup-Codes">
        {codes.map((c) => (
          <li key={c} className="select-all">
            {c}
          </li>
        ))}
      </ul>
      <button
        type="button"
        className="btn-ghost mt-3 w-full justify-center"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(codes.join("\n"));
            setCopied(true);
          } catch {
            setCopied(false);
          }
        }}
      >
        {copied ? "Kopiert" : "Codes kopieren"}
      </button>
    </div>
  );
}
