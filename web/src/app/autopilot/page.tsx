import { ModeBadge } from "@/components/mode-badge";
import { Placeholder } from "@/components/placeholder";

export default function AutopilotPage() {
  return (
    <Placeholder
      title="Autopilot"
      subtitle="Wer darf was, und wie halte ich ihn an? Paper und Live haben getrennte Zustände, Prozesse und Schalter."
      items={[
        { stage: "E2", text: "Paper-Autopilot: Zustand, Mandat, Limits mit Auslastung; Start, Einstiege pausieren, geordnet stoppen, Positionen jetzt schliessen, Notfall." },
        { stage: "E4", text: "Live-Autopilot mit Mandaten und Live-Assistent. Jede Live-Aktion verlangt eine erneute Anmeldung (Step-up)." },
      ]}
    >
      <div className="mt-4 flex flex-wrap items-center gap-2 text-sm text-ink-2">
        <ModeBadge mode="PAPER" /> Nicht eingerichtet
        <span aria-hidden>·</span>
        <ModeBadge mode="LIVE" /> Kein Konto verbunden. Echtgeldhandel ist ausgeschaltet.
      </div>
    </Placeholder>
  );
}
