import Link from "next/link";
import { SetupHint } from "@/components/setup-hint";
import { SignalList, UnverifiedBanner } from "@/components/signal-list";
import { Empty, PageHeader } from "@/components/ui";
import { guard } from "@/lib/data/guard";
import { listSignals } from "@/lib/data/signals";

export const dynamic = "force-dynamic";

export default async function SignalsPage({ searchParams }: { searchParams: Promise<{ all?: string }> }) {
  const all = (await searchParams).all === "1";
  const r = await guard(() => listSignals({ includeNoTrade: all }));
  const tab = (on: boolean) => `rounded-md px-2.5 py-1 text-xs ${on ? "bg-surface-2 font-medium text-ink" : "text-muted hover:text-ink"}`;

  return (
    <>
      <PageHeader
        title="Signale"
        subtitle="Gespeicherte Strategieausgaben je abgeschlossener Kerze, neueste zuerst. Zeiten in Europe/Zurich."
        actions={
          <div className="inline-flex rounded-lg border border-line bg-surface p-0.5" role="group" aria-label="Filter">
            <Link href="/signals" aria-current={!all ? "true" : undefined} className={tab(!all)}>
              Nur Signale
            </Link>
            <Link href="/signals?all=1" aria-current={all ? "true" : undefined} className={tab(all)}>
              Inkl. NO TRADE
            </Link>
          </div>
        }
      />
      <UnverifiedBanner />
      {!r.ok ? (
        <SetupHint state={r} />
      ) : r.data.length ? (
        <SignalList rows={r.data} />
      ) : (
        <Empty>
          {all ? "Noch keine Entscheide gespeichert." : "Kein gültiges Setup."}{" "}
          {!all && (
            <Link href="/signals?all=1" className="text-accent">
              NO-TRADE-Entscheide anzeigen →
            </Link>
          )}
        </Empty>
      )}
    </>
  );
}
