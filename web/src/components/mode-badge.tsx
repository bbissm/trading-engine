export type Mode = "PAPER" | "LIVE" | "RESEARCH";

/** The word is always part of the label — colour is never the only carrier of the mode. */
export const MODE_LABEL: Record<Mode, string> = {
  PAPER: "PAPER",
  LIVE: "LIVE · Echtgeld",
  RESEARCH: "FORSCHUNG",
};

const STYLE: Record<Mode, string> = {
  // neutral/blue, tinted
  PAPER: "border-mode-paper bg-[color-mix(in_oklab,var(--mode-paper)_12%,transparent)] text-ink",
  // strong signal colour, filled
  LIVE: "border-mode-live bg-mode-live text-[var(--mode-live-ink)]",
  // grey, outlined
  RESEARCH: "border-mode-research bg-transparent text-ink-2",
};

/**
 * Mode label for every card, table, message and export (document 02, section 1).
 * Live and paper figures are never added up; each mode gets its own card/column.
 */
export function ModeBadge({ mode, className = "" }: { mode: Mode; className?: string }) {
  return (
    <span data-mode={mode} className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${STYLE[mode]} ${className}`}>
      {mode !== "LIVE" && <span aria-hidden className={`inline-block h-2 w-2 rounded-full ${mode === "PAPER" ? "bg-mode-paper" : "bg-mode-research"}`} />}
      {MODE_LABEL[mode]}
    </span>
  );
}
