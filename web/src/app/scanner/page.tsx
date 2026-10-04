import Link from "next/link";
import { SetupHint } from "@/components/setup-hint";
import { instrumentHref } from "@/components/signal-list";
import { FeedBadge, RegimeBadge } from "@/components/status";
import { Card, Empty, PageHeader, TableWrap } from "@/components/ui";
import { guard } from "@/lib/data/guard";
import { loadScanner } from "@/lib/data/scanner";
import { dateTime, price } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function ScannerPage() {
  const r = await guard(loadScanner);
  return (
    <>
      <PageHeader title="Scanner" subtitle="Instrumente im freigegebenen Universum mit dem zuletzt gespeicherten Regime je Zeitebene. Der Scanner arbeitet nur innerhalb dieses Universums." />
      {!r.ok ? (
        <SetupHint state={r} />
      ) : !r.data.length ? (
        <Empty>Noch kein Instrument im Universum. Die Engine legt die Stammdaten beim ersten Lauf an.</Empty>
      ) : (
        <Card>
          <TableWrap>
            <table className="data">
              <thead>
                <tr>
                  <th>Instrument</th>
                  <th>Handelsplatz</th>
                  <th>Währung</th>
                  <th>Regime je Zeitebene</th>
                  <th>Datenstrom</th>
                  <th className="num">Letzter Schlusskurs</th>
                </tr>
              </thead>
              <tbody>
                {r.data.map((i) => (
                  <tr key={i.id}>
                    <td className="whitespace-nowrap">
                      <Link href={instrumentHref(i.id)} className="font-medium text-accent hover:underline">
                        {i.id}
                      </Link>
                      <div className="text-xs text-ink-2">{i.name}</div>
                    </td>
                    <td>{i.venue}</td>
                    <td>{i.quoteCurrency}</td>
                    <td>
                      <div className="flex flex-wrap gap-1">{i.regimes.length ? i.regimes.map((x) => <RegimeBadge key={x.timeframe} regime={x.regime} prefix={x.timeframe} />) : <span className="text-ink-2">—</span>}</div>
                    </td>
                    <td>
                      <div className="flex flex-wrap gap-1">{i.feeds.length ? i.feeds.map((f) => <FeedBadge key={`${f.feed}:${f.timeframe}`} status={f.status} prefix={f.timeframe} />) : <span className="text-ink-2">—</span>}</div>
                    </td>
                    <td className="num">
                      {price(i.lastClose, i.quoteCurrency)}
                      <div className="text-xs text-ink-2">{dateTime(i.lastCloseTime)}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Card>
      )}
    </>
  );
}
