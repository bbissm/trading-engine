import { AlertList, ChannelTable } from "@/components/alerts";
import { AutoRefresh } from "@/components/auto-refresh";
import { SetupHint } from "@/components/setup-hint";
import { Badge, Banner, Card, PageHeader, Stat } from "@/components/ui";
import { guard } from "@/lib/data/guard";
import { loadAlerts, type AlertsData } from "@/lib/data/alerts";
import { ago, dateTime } from "@/lib/format";
import { CHANNEL_LABEL, CHANNELS, ENGINE_ENV } from "@/lib/notify/env";
import { AckButton, ChannelsForm, QuietHoursForm, TestAlarmButton } from "./controls";

export const dynamic = "force-dynamic";

const CHANNEL_ROLE: Record<string, string> = {
  TELEGRAM: "Telegram – Hauptkanal: Signale, Warnungen, Schaltflächen",
  PUSHOVER: "Pushover – kritische Alarme, wiederholt bis zur Bestätigung",
  EMAIL: "E-Mail – Tagesbericht und Eskalationsstufe nach 15 min",
};

/** Which channels are not configured and which variables are missing (names only, never values). */
function Missing({ data }: { data: AlertsData }) {
  const engine = CHANNELS.map((ch) => {
    const row = data.channels.find((c) => c.channel === ch);
    return { ch, configured: row?.configured ?? false, known: !!row };
  }).filter((c) => !c.configured);
  const web = data.webMissing;
  const webLines = [
    web.webhook.length ? `Telegram-Webhook (Schaltflächen, /status, /pause): ${web.webhook.join(", ")}` : null,
    web.watchdogTelegram.length || web.watchdogPushover.length ? `Watchdog-Versand: ${[...new Set([...web.watchdogTelegram, ...web.watchdogPushover])].join(", ")}` : null,
    web.cron.length ? `Watchdog-Cron: ${web.cron.join(", ")}` : null,
  ].filter(Boolean);
  if (!engine.length && !webLines.length) return <p className="text-sm text-ink-2">Alle Kanäle sind eingerichtet.</p>;
  return (
    <div className="space-y-3 text-sm">
      {engine.length > 0 && (
        <div>
          <p className="font-medium">Engine (Projekt trading-engine-worker)</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-ink-2">
            {engine.map((c) => (
              <li key={c.ch} className="break-words">
                {CHANNEL_LABEL[c.ch]} nicht eingerichtet – fehlt: <code className="text-xs">{ENGINE_ENV[c.ch].join(", ")}</code>
                {!c.known && " (noch nicht geprüft)"}
              </li>
            ))}
          </ul>
        </div>
      )}
      {webLines.length > 0 && (
        <div>
          <p className="font-medium">Web-App (dieses Projekt)</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-ink-2">
            {webLines.map((l) => (
              <li key={l} className="break-words">
                {l}
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-xs text-ink-2">Ohne eingerichtete Kanäle erscheinen Meldungen nur hier in der App. Sobald die Variablen gesetzt sind, nutzt die Engine die Kanäle automatisch.</p>
    </div>
  );
}

export default async function AlertsPage() {
  const r = await guard(() => loadAlerts());
  return (
    <>
      <PageHeader title="Meldungen" subtitle="Alle Meldungen mit Zustellstatus je Kanal. «Vom Kanal angenommen» heisst nicht gelesen – nur «Bestätigen» stoppt die Eskalation. Bestätigen genehmigt keinen Trade." />
      {!r.ok ? (
        <SetupHint state={r} />
      ) : (
        <Body data={r.data} />
      )}
    </>
  );
}

function Body({ data }: { data: AlertsData }) {
  const critical = data.open.filter((a) => a.level === "CRITICAL").length;
  const warnings = data.open.filter((a) => a.level === "WARNING").length;
  const capable = data.channels.some((c) => (c.channel === "PUSHOVER" || c.channel === "TELEGRAM") && c.configured && c.ok && data.enabled[c.channel as "PUSHOVER" | "TELEGRAM"]);
  const testOpen = data.lastTestSentAt && (!data.lastTestAckAt || data.lastTestAckAt < data.lastTestSentAt);
  return (
    <div className="space-y-4">
      <AutoRefresh seconds={15} />
      {!capable && (
        <Banner>
          Kein kritischer Kanal (Pushover oder Telegram) ist eingerichtet und erreichbar. Kritische Alarme erscheinen nur in der App. Ein Live-Autopilot würde nach der Kanalausfall-Policy keine Einstiege machen; Paper läuft weiter.
        </Banner>
      )}
      <div className="grid grid-cols-2 gap-3 rounded-xl border border-line bg-surface p-4 sm:grid-cols-4">
        <Stat label="Offen kritisch" value={critical} tone={critical ? "critical" : undefined} />
        <Stat label="Offen Warnungen" value={warnings} />
        <Stat label="Offen gesamt" value={data.open.length} />
        <Stat label="Letzter Testalarm" value={data.lastTestSentAt ? ago(data.lastTestSentAt, data.now) : "—"} hint={data.lastTestSentAt ? (testOpen ? "noch nicht bestätigt" : `bestätigt ${dateTime(data.lastTestAckAt)}`) : "noch keiner gesendet"} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Offen" subtitle="Kritisch zuerst. Entdoppelt: ein anhaltender Zustand aktualisiert dieselbe Meldung." className="lg:col-span-2">
          <AlertList
            alerts={data.open}
            now={data.now}
            empty="Keine offenen Meldungen."
            action={(a) => (a.level === "INFO" || a.level === "SIGNAL") && a.kind !== "TEST" ? null : <AckButton alertId={a.id} waiting={data.pendingAcks.includes(a.id)} />}
          />
        </Card>
        <div className="space-y-4">
          <Card title="Testalarm" subtitle="Mit Bestätigungspflicht: vor jeder Mandatsaktivierung (höchstens 7 Tage alt) und monatlich.">
            <dl className="mb-3 space-y-1.5 text-sm">
              <div className="flex justify-between gap-3">
                <dt className="text-ink-2">Zuletzt gesendet</dt>
                <dd className="tabular">{dateTime(data.lastTestSentAt)}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-ink-2">Zuletzt bestätigt</dt>
                <dd className="tabular">{dateTime(data.lastTestAckAt)}</dd>
              </div>
            </dl>
            {testOpen && <p className="mb-2 text-xs text-ink-2">Bleibt ein Testalarm 24 h unbestätigt, gilt die Benachrichtigung als ausgefallen.</p>}
            <TestAlarmButton />
          </Card>
          <Card title="Nicht eingerichtet">
            <Missing data={data} />
          </Card>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Kanäle" subtitle="Technische Prüfung durch die Engine alle 15 Minuten" className="lg:col-span-2">
          <ChannelTable rows={data.channels} enabled={data.enabled} now={data.now} />
          <p className="mt-2 text-xs text-ink-2">SMS ist in V1 nicht umgesetzt. Ein zweiter, unabhängiger Weg (Vercel-Watchdog) meldet direkt, wenn die Engine sich länger als 5 Minuten nicht meldet.</p>
        </Card>
        <Card title="Einstellungen" subtitle="Ruhezeit (Europe/Zurich): Informationen und Signale werden zurückgehalten, Warnungen stumm zugestellt.">
          <div className="space-y-5">
            <QuietHoursForm start={data.quiet.start} end={data.quiet.end} criticalBypass={data.quiet.critical_bypass} />
            <div className="border-t border-line pt-4">
              <ChannelsForm enabled={data.enabled} labels={CHANNEL_ROLE} />
            </div>
            {data.pendingCommands.some((c) => c.type === "SETTINGS_SET") && (
              <p className="text-xs text-ink-2">
                <Badge tone="warning">Wartet auf Engine</Badge> Eine Änderung wird innert etwa einer Minute übernommen.
              </p>
            )}
          </div>
        </Card>
      </div>

      <Card title="Bestätigt" subtitle="Die letzten 30">
        <AlertList alerts={data.acknowledged} now={data.now} empty="Noch nichts bestätigt." />
      </Card>
      <Card title="Erledigt" subtitle="Zustand verschwunden oder im Tagesbericht gebündelt – die letzten 30">
        <AlertList alerts={data.resolved} now={data.now} empty="Noch nichts erledigt." />
      </Card>
    </div>
  );
}
