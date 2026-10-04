import type { ReactNode } from "react";
import { Badge, Card, PageHeader } from "./ui";

export interface PlannedItem {
  /** e.g. "E2" */
  stage: string;
  text: string;
}

/** Honest placeholder: says what will be here and in which Etappe (docs/08-etappen-backlog.md). No sample data. */
export function Placeholder({ title, subtitle, items, children }: { title: string; subtitle: string; items: PlannedItem[]; children?: ReactNode }) {
  return (
    <>
      <PageHeader title={title} subtitle={subtitle} />
      <Card title="Noch nicht gebaut" subtitle="Diese Seite zeigt bewusst keine Beispieldaten und keine Kennzahlen.">
        <ul className="divide-y divide-[var(--grid)]">
          {items.map((i, n) => (
            <li key={n} className="flex items-start gap-3 py-2 first:pt-0 last:pb-0">
              <Badge title={`Etappe ${i.stage}`}>Etappe {i.stage}</Badge>
              <span className="min-w-0 text-sm">{i.text}</span>
            </li>
          ))}
        </ul>
        {children}
      </Card>
    </>
  );
}
