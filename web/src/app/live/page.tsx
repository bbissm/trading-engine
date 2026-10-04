import { AutoRefresh } from "@/components/auto-refresh";
import { KeyInstructions, LiveFills, LiveOrders, LivePositions, LiveStateBadge, PreconditionList, ReconciliationList } from "@/components/live";
import { ModeBadge } from "@/components/mode-badge";
import { CommandStatusBadge } from "@/components/paper";
import { SetupHint } from "@/components/setup-hint";
import { Badge, Banner, Card, Empty, PageHeader, Stat } from "@/components/ui";
import { guard } from "@/lib/data/guard";
import { loadLiveOverview } from "@/lib/data/live";
import { ago, dateTime, decimal, price } from "@/lib/format";
import { rejectionReason } from "@/lib/paper";
import { LiveApprovalButton, LiveControls, MandateForm, OrderApprovalButtons, RegisterAccount, SuspendMandate } from "./live-controls";
import { canActivate, controlEffects, eligibleStrategies, gatesPassed, LIVE_COMMAND_LABEL, liveState, preconditions } from "./model";

export const dynamic = "force-dynamic";

async function load(now = Date.now()) {
  return { now, overview: await loadLiveOverview() };
}

export default async function LivePage() {
  const loaded = await guard(() => load());
  const r = loaded.ok ? { ok: true as const, data: loaded.data.overview } : loaded;
  const now = loaded.ok ? loaded.data.now : 0;

  return (
    <>
      <PageHeader
        title="Live-Assistent"
        subtitle="Echtgeld über Kraken Spot. Gesperrt, bis jede Voraussetzung erfüllt ist und du das Mandat mit Step-up aktivierst. Die Live-Funktion prüft alle Sperren vor jeder Order erneut."
        actions={<ModeBadge mode="LIVE" />}
      />
      {!r.ok ? (
        <SetupHint state={r} />
      ) : (
        (() => {
          const o = r.data;
          const checks = preconditions(o.facts, now);
          const ready = canActivate(checks);
          const blockers = checks.filter((c) => c.blocking && c.status !== "ok").map((c) => c.label);
          const eligible = eligibleStrategies(o.facts).map((s) => s.id);
          const state = o.account ? (o.autopilot?.state ?? "SETUP") : null;
          const effects = controlEffects({
            positions: o.positions.length,
            entryOrders: o.orders.filter((x) => x.role === "ENTRY").length,
            notional: o.exposure.costBasis ? decimal(o.exposure.costBasis) : null,
            estCost: o.exposure.estTakerFee ? decimal(o.exposure.estTakerFee) : null,
            emergency: String(o.mandate?.policy?.emergency ?? "HOLD_PROTECTED"),
          });
          const waiting = o.commands.some((c) => c.status === "PENDING" && c.type !== "LIVE_ACCOUNT_REGISTER");
          return (
            <div className="space-y-4">
              <AutoRefresh seconds={10} />
              {!o.facts.flag && (
                <Banner tone="info">
                  Echtgeldhandel ist ausgeschaltet: <span className="font-mono text-xs">LIVE_TRADING_ENABLED</span> = false. Ohne diesen Schalter sendet die Engine keine Order an Kraken.
                </Banner>
              )}

              <section className="rounded-xl border-2 border-mode-live bg-surface p-4" aria-label="Zustand Live-Autopilot">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold">Live-Autopilot</h2>
                  <ModeBadge mode="LIVE" />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <LiveStateBadge state={state} />
                  <Badge tone={o.facts.flag ? "critical" : "neutral"}>LIVE_TRADING_ENABLED: {o.facts.flag ? "true" : "false"}</Badge>
                  {o.autopilot && <span className="text-xs text-ink-2">seit {dateTime(o.autopilot.updatedAt)}</span>}
                </div>
                <p className="mt-2 text-sm">{liveState(state).meaning}</p>
                {o.autopilot?.reason && <p className="mt-1 break-words text-xs text-ink-2">Letzte Zustandsänderung: {o.autopilot.reason}</p>}
                {o.account && (
                  <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
                    <Stat label="Positionen (verwaltet)" value={o.positions.length} />
                    <Stat label="Offene Orders bei Kraken" value={o.orders.length} />
                    <Stat label="USD bei Kraken" value={price(o.snapshot?.balances.ZUSD ?? o.snapshot?.balances.USD ?? null, "USD")} hint="aus dem letzten Abgleich" />
                    <Stat label="Mandatsbudget" value={o.mandate?.status === "ACTIVE" ? price(o.mandate.budget, "USD") : "—"} />
                  </div>
                )}
                {o.account && (
                  <div className="mt-4 border-t border-[var(--grid)] pt-4">
                    <LiveControls state={state} effects={effects} waiting={waiting} positions={o.positions.length} />
                  </div>
                )}
              </section>

              <Card title="Voraussetzungen" subtitle="Jeder Punkt mit seinem aktuellen Stand. «Mandat aktivieren» erscheint erst, wenn alles grün ist.">
                <PreconditionList checks={checks} />
              </Card>

              <Card title="Kraken-Schlüssel hinterlegen" subtitle="Der Schlüssel wird nie im Browser eingegeben und nie an diese App gesendet.">
                <KeyInstructions />
                <div className="mt-4 border-t border-[var(--grid)] pt-4">
                  <RegisterAccount connected={!!o.account} />
                </div>
              </Card>

              <Card title="Freigabe für Live je Strategieversion" subtitle="Nur Versionen mit G1–G3 bestanden sind anwählbar. Freigeben verlangt Step-up und wirkt nur auf neue Entscheidungen.">
                {o.strategies.length ? (
                  <ul className="divide-y divide-[var(--grid)]">
                    {o.strategies.map((s) => (
                      <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 py-2 first:pt-0 last:pb-0">
                        <div className="min-w-0">
                          <div className="break-all font-mono text-xs">{s.id}</div>
                          <div className="mt-1 flex flex-wrap gap-1">
                            {["G1", "G2", "G3"].map((g) => (
                              <Badge key={g} tone={s.gates[g] === "PASSED" ? "good" : s.gates[g] ? "critical" : "neutral"}>
                                {g}: {s.gates[g] ?? "offen"}
                              </Badge>
                            ))}
                            {s.approvedLive && <Badge tone="critical">APPROVED_LIVE</Badge>}
                          </div>
                        </div>
                        <LiveApprovalButton strategyVersionId={s.id} approved={s.approvedLive} selectable={gatesPassed(s)} />
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty>Keine Strategieversion gespeichert.</Empty>
                )}
              </Card>

              <Card title="Mandat" subtitle="Handlungsvollmacht für das Live-Konto. Ohne aktives Mandat entsteht keine Live-Order." actions={<ModeBadge mode="LIVE" />}>
                {o.mandate?.status === "ACTIVE" ? (
                  <div className="space-y-2 text-sm">
                    <p>
                      Mandat {o.mandate.id}: Stufe {o.mandate.autonomyLevel}, Budget {price(o.mandate.budget, "USD")}, aktiviert {dateTime(o.mandate.activatedAt)} durch {o.mandate.activatedBy}.
                    </p>
                    <p className="break-all text-xs text-ink-2">
                      Versionen: {o.mandate.strategyVersionIds.join(", ")} · Instrumente: {o.mandate.instrumentIds.join(", ")}
                    </p>
                    <SuspendMandate />
                  </div>
                ) : o.account ? (
                  <MandateForm eligible={eligible} instruments={o.instruments} ready={ready} blockers={blockers} />
                ) : (
                  <Empty>Zuerst das Kraken-Konto verbinden.</Empty>
                )}
              </Card>

              {o.pendingApprovals.length > 0 && (
                <Card title="Vorbereitete Orders (Stufe 2)" subtitle="Ohne Antwort verfallen sie – Schweigen ist keine Erlaubnis. Vor dem Senden wird erneut geprüft." actions={<ModeBadge mode="LIVE" />}>
                  <ul className="divide-y divide-[var(--grid)]">
                    {o.pendingApprovals.map((a) => (
                      <li key={a.id} className="space-y-2 py-2 first:pt-0 last:pb-0">
                        <div className="text-sm">
                          Kauf {decimal(String(a.intent.qty ?? ""), 0)} {String(a.intent.instrument_id ?? "")} limit {price(String(a.intent.limit ?? ""), "USD")}, Stop {price(String(a.intent.stop ?? ""), "USD")}
                        </div>
                        <div className="text-xs text-ink-2">Wartet auf deine Freigabe bis {dateTime(a.expiresAt)}</div>
                        <OrderApprovalButtons approvalId={a.id} />
                      </li>
                    ))}
                  </ul>
                </Card>
              )}

              <Card title="Positionen" actions={<ModeBadge mode="LIVE" />}>
                <LivePositions rows={o.positions} snapshot={o.snapshot} />
              </Card>
              <Card title="Offene Orders bei Kraken" actions={<ModeBadge mode="LIVE" />}>
                <LiveOrders rows={o.orders} />
              </Card>
              <Card title="Ausführungen" actions={<ModeBadge mode="LIVE" />}>
                <LiveFills rows={o.fills} />
              </Card>
              <Card title="Abgleich mit Kraken" subtitle="Bestände, offene Orders und Fills; fremde Positionen werden angezeigt, nie gehandelt." actions={<ModeBadge mode="LIVE" />}>
                <ReconciliationList rows={o.reconciliations} now={now} />
              </Card>

              <Card title="Live-Befehle">
                {o.commands.length ? (
                  <ul className="divide-y divide-[var(--grid)]">
                    {o.commands.map((c) => (
                      <li key={c.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 first:pt-0 last:pb-0">
                        <div className="min-w-0 flex-1 basis-48">
                          <div className="text-sm font-medium">{LIVE_COMMAND_LABEL[c.type] ?? c.type}</div>
                          <div className="text-xs text-ink-2">
                            Nr. {c.id} · {ago(c.issuedAt, now)} · {c.issuedBy}
                          </div>
                          {rejectionReason(c.result) && <div className="break-words text-xs text-critical">Grund: {rejectionReason(c.result)}</div>}
                        </div>
                        <CommandStatusBadge status={c.status} />
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty>Noch kein Live-Befehl.</Empty>
                )}
              </Card>
            </div>
          );
        })()
      )}
    </>
  );
}
