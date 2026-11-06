import { SetupHint } from "@/components/setup-hint";
import { Badge, Banner, Card, Empty, PageHeader, type Tone } from "@/components/ui";
import { guard } from "@/lib/data/guard";
import { ago, dateTime } from "@/lib/format";
import { bridgeConfigured } from "@/lib/news/bridge";
import { loadNews } from "@/lib/news/pipeline";

export const dynamic = "force-dynamic";

const DIRECTION: Record<number, { label: string; tone: Tone }> = {
  [-2]: { label: "stark negativ", tone: "critical" },
  [-1]: { label: "negativ", tone: "warning" },
  0: { label: "neutral", tone: "neutral" },
  1: { label: "positiv", tone: "good" },
  2: { label: "stark positiv", tone: "good" },
};
const SCOPE: Record<string, string> = { COIN: "Coin", MARKET: "Gesamtmarkt", MACRO: "Politik/Makro", NONE: "ohne Marktbezug" };
const CREDIBILITY: Record<string, string> = { OFFICIAL: "offiziell", REPORTED: "berichtet", RUMOR: "Gerücht" };
const HORIZON: Record<string, string> = { DAYS: "Tage", WEEKS: "Wochen", MONTHS: "Monate" };

const dir = (d: number) => DIRECTION[d] ?? DIRECTION[0];

export default async function NewsPage() {
  const r = await guard(() => loadNews());
  return (
    <>
      <PageHeader
        title="Nachrichten & Briefing"
        subtitle="RSS-Meldungen alle 4 Stunden, eingeordnet von Claude über die lokale Bridge. Einordnung ist keine Prognose und keine Empfehlung."
      />
      {!bridgeConfigured() && <Banner>Die Claude Code Bridge ist nicht verbunden (LLM_BRIDGE_TOKEN fehlt): Meldungen werden gesammelt, aber nicht eingeordnet.</Banner>}
      {!r.ok ? (
        <SetupHint state={r} />
      ) : (
        <div className="grid gap-4">
          <Card title={r.data.briefing ? `Briefing ${r.data.briefing.day}` : "Briefing"} subtitle={r.data.briefing ? `${r.data.briefing.itemCount} Meldungen · ${r.data.briefing.model}` : undefined}>
            {r.data.briefing ? (
              <div className="space-y-3 text-sm">
                <p className="font-medium">{r.data.briefing.headline}</p>
                <p className="text-ink-2">{r.data.briefing.summary}</p>
                {r.data.briefing.coins.length > 0 && (
                  <ul className="space-y-1">
                    {r.data.briefing.coins.map((c) => (
                      <li key={c.coin} className="flex flex-wrap items-baseline gap-2">
                        <Badge tone={dir(c.direction).tone}>{c.coin}</Badge>
                        <span className="text-ink-2">{c.note}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {r.data.briefing.watch.length > 0 && (
                  <div>
                    <div className="text-xs font-medium text-muted">Im Blick behalten</div>
                    <ul className="list-disc pl-5 text-ink-2">
                      {r.data.briefing.watch.map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ) : (
              <Empty>Noch kein Briefing. Es entsteht einmal am Morgen aus den eingeordneten Meldungen des Vortags.</Empty>
            )}
          </Card>

          <Card
            title="Eingeordnete Meldungen"
            subtitle={`Relevanz ab 30 · ${r.data.items} Meldungen gesammelt, ${r.data.unassessed} noch offen, ${r.data.pending} Anfragen bei der Bridge · letzter Abruf ${r.data.lastFetch ? ago(r.data.lastFetch) : "—"}`}
          >
            {r.data.rows.length ? (
              <ul className="divide-y divide-line">
                {r.data.rows.map(({ item, a }) => (
                  <li key={item.id} className="py-3 text-sm">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge tone={dir(a.direction).tone}>{dir(a.direction).label}</Badge>
                      <Badge>{`Relevanz ${a.relevance}`}</Badge>
                      {a.coins.map((c) => (
                        <Badge key={c} tone="accent">
                          {c}
                        </Badge>
                      ))}
                      <span className="text-xs text-muted">
                        {SCOPE[a.scope] ?? a.scope} · {a.category} · {CREDIBILITY[a.credibility] ?? a.credibility} · wirkt {HORIZON[a.horizon] ?? a.horizon}
                      </span>
                    </div>
                    <a href={item.url} target="_blank" rel="noreferrer" className="mt-1 block font-medium hover:text-accent">
                      {item.title}
                    </a>
                    <p className="mt-0.5 text-ink-2">{a.rationale}</p>
                    <p className="mt-0.5 text-xs text-muted">
                      {item.source} · {dateTime(item.publishedAt ?? item.fetchedAt)} · eingeordnet {dateTime(a.createdAt)}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>Noch keine eingeordneten Meldungen mit Relevanz ab 30.</Empty>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
