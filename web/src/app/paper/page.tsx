import { ModeBadge } from "@/components/mode-badge";
import { Placeholder } from "@/components/placeholder";

export default function PaperPage() {
  return (
    <Placeholder
      title="Paper-Lab"
      subtitle="Wie schlagen sich Strategien virtuell? Alle Zahlen hier werden simuliert sein und so gekennzeichnet."
      items={[
        { stage: "E2", text: "Virtuelle Konten und Episoden (Reset = neue Episode), Kostenmodell, Simulator-Einstellungen." },
        { stage: "E2", text: "Realismus-Karte: welche Komponenten simuliert, beobachtet oder fehlend sind." },
        { stage: "E3", text: "Vergleich von Strategien und Versionen (Champion/Challenger)." },
      ]}
    >
      <div className="mt-4 flex flex-wrap items-center gap-2 text-sm text-ink-2">
        <ModeBadge mode="PAPER" /> Kein virtuelles Konto vorhanden.
      </div>
    </Placeholder>
  );
}
