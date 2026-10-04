import { Placeholder } from "@/components/placeholder";

export default function PortfolioPage() {
  return (
    <Placeholder
      title="Portfolio & Orders"
      subtitle="Was halte ich wirklich? Bestände und Orders je Modus und Konto – Paper und Live werden nie addiert."
      items={[
        { stage: "E2", text: "Paper-Konten: Positionen, offene Orders, Teilfüllungen und Schutzorder je Position (simuliert)." },
        { stage: "E4", text: "Live: Anbieterbestand gegen lokalen Bestand, Schutzorders, fremde Positionen (nicht verwaltet), letzter Abgleich." },
      ]}
    >
      <p className="mt-4 text-sm text-ink-2">Es ist kein Konto verbunden und es existieren keine Positionen oder Orders.</p>
    </Placeholder>
  );
}
