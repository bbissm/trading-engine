import Link from "next/link";
import { Placeholder } from "@/components/placeholder";

export default function JournalPage() {
  return (
    <Placeholder
      title="Journal & Performance"
      subtitle="Warum hat er das gemacht, und was kam heraus?"
      items={[
        { stage: "E1", text: "Signaljournal: alle Entscheide inklusive NO TRADE mit Auslösern und Gegenfaktoren." },
        { stage: "E2", text: "Trade-Detail (Entscheidungskette, Plan gegen Ausführung, Kosten), Kennzahlen mit Unsicherheit, Benchmarks, Exporte – zuerst nur Paper, als «simuliert» gekennzeichnet." },
        { stage: "E4", text: "Echte Live-Ergebnisse, getrennt von Backtest- und Paper-Zahlen." },
      ]}
    >
      <p className="mt-4 text-sm text-ink-2">
        Es gibt noch keine Trades und keine Ergebnisse. Die gespeicherten Entscheide stehen bereits unter{" "}
        <Link href="/signals?all=1" className="text-accent">
          Signale (inkl. NO TRADE)
        </Link>
        .
      </p>
    </Placeholder>
  );
}
