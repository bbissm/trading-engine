import { ModeBadge } from "@/components/mode-badge";
import { Placeholder } from "@/components/placeholder";

export default function StrategiesPage() {
  return (
    <Placeholder
      title="Strategien & Lernlabor"
      subtitle="Was wurde gelernt, vorgeschlagen, freigegeben? Kein Gate einer Ebene ersetzt ein Gate einer anderen."
      items={[
        { stage: "E1", text: "Drei regelbasierte Strategien als Signalgeber, als unveränderliche Versionen. Ihre Signale sind ausdrücklich ungeprüft." },
        { stage: "E3", text: "Versionenliste mit Statuskarte (Gates G1–G5, bestanden/nicht bestanden mit Grund), Experimentprotokoll inklusive gescheiterter Varianten, Budgets." },
        { stage: "E3", text: "Drei getrennte Spalten: «automatisch gelernt» · «zur Freigabe vorgeschlagen» · «live freigegeben»." },
      ]}
    >
      <div className="mt-4 flex flex-wrap items-center gap-2 text-sm text-ink-2">
        <ModeBadge mode="RESEARCH" /> Noch keine Experimente und kein Qualitätsnachweis für irgendeine Version.
      </div>
    </Placeholder>
  );
}
