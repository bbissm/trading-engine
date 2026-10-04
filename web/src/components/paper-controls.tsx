"use client";

import { useActionState, useState } from "react";
import { paperCommandAction, type CommandActionState } from "@/app/paper/actions";
import { allowedCommands, autopilotState, COMMAND_LABEL, COMMAND_STATUS, type AccountCommandType } from "@/lib/paper";

export interface ControlCommand {
  id: number;
  type: string;
  status: string;
  reason: string | null;
  /** formatted on the server (Europe/Zurich) */
  issued: string;
}

interface Props {
  accountId: string;
  state: string | null;
  openPositions: number;
  workingOrders: number;
  workingEntryOrders: number;
  episodeNumber: number;
  /** start capital of the current episode, plain decimal string for the reset field */
  startCash: string;
  /** latest PAPER_* commands of this account, newest first */
  commands: ControlCommand[];
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/**
 * Control buttons of one paper autopilot. Every button first shows its concrete effect; only the confirmation
 * writes the command (server action → table `command`). Buttons are enabled only in states where the engine accepts them.
 */
export function PaperControls({ accountId, state, openPositions, workingOrders, workingEntryOrders, episodeNumber, startCash, commands }: Props) {
  const [open, setOpen] = useState<AccountCommandType | null>(null);
  const [result, action, pending] = useActionState(async (prev: CommandActionState | undefined, form: FormData) => {
    const r = await paperCommandAction(prev, form);
    if (!r.error) setOpen(null);
    return r;
  }, undefined);

  const allowed = allowedCommands(state, { openPositions, workingOrders });
  const waiting = commands.some((c) => c.status === "PENDING");
  const stateLabel = autopilotState(state).label;
  const positions = plural(openPositions, "Position", "Positionen");
  const entries = plural(workingEntryOrders, "offene Einstiegsorder wird", "offene Einstiegsorders werden");

  const buttons: { type: AccountCommandType; label: string; effect: string; primary?: boolean }[] = [
    {
      type: "PAPER_START",
      label: state === "ENTRIES_PAUSED" ? "Fortsetzen" : "Starten",
      primary: true,
      effect:
        state === "ENTRIES_PAUSED"
          ? `Der Paper-Autopilot wird wieder aktiv: neue Signale können erneut zu simulierten Einstiegsorders führen. ${positions} werden unverändert weiter betreut. Kein Echtgeld.`
          : "Der Paper-Autopilot wird aktiv: neue Signale können im Rahmen der Risikogrenzen zu simulierten Einstiegsorders führen. Kein Echtgeld, keine Order an einen Handelsplatz.",
    },
    {
      type: "PAPER_PAUSE",
      label: "Einstiege pausieren",
      effect: `Keine neuen Einstiege. ${entries} storniert. ${positions} und ihre Schutz-Stops bleiben und werden weiter betreut.`,
    },
    {
      type: "PAPER_STOP",
      label: "Geordnet stoppen",
      effect: `Keine neuen Einstiege. ${entries} storniert. ${positions} laufen bis zu ihren Exits nach Strategie-Regeln (Stop, Ziel, Zeitlimit); erst ohne Bestand wechselt der Zustand auf «Gestoppt».`,
    },
    {
      type: "PAPER_CLOSE_ALL",
      label: "Positionen jetzt schliessen",
      effect: `Market-Exits für ${positions}; simuliert ausgeführt zur Eröffnung der nächsten 4h-Kerze – bis dahin bleibt das Kursrisiko. ${entries} storniert, keine neuen Einstiege.`,
    },
    {
      type: "PAPER_RESET",
      label: "Neue Episode (Reset)",
      effect: `Beendet Episode ${episodeNumber} und legt Episode ${episodeNumber + 1} mit dem unten gewählten virtuellen Startkapital an. Die bisherige Episode bleibt unverändert einsehbar. Die Engine führt den Reset nur ohne offene Positionen und Orders aus.`,
    },
  ];
  const current = buttons.find((b) => b.type === open);
  const sent = result?.commandId ? commands.find((c) => c.id === result.commandId) : undefined;
  const last = commands[0];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Bedienung Paper-Autopilot">
        {buttons.map((b) => {
          const enabled = allowed[b.type] && !waiting && !pending;
          return (
            <button
              key={b.type}
              type="button"
              disabled={!enabled}
              aria-expanded={open === b.type}
              title={allowed[b.type] ? undefined : `Im Zustand «${stateLabel}» nicht möglich`}
              onClick={() => setOpen(open === b.type ? null : b.type)}
              className={`${b.primary ? "btn" : "btn-ghost"} disabled:cursor-not-allowed disabled:opacity-45 ${open === b.type ? "ring-2 ring-accent" : ""}`}
            >
              {b.label}
            </button>
          );
        })}
      </div>

      {waiting && <p className="text-xs text-ink-2">Ein Befehl wartet auf die Engine. Weitere Befehle sind möglich, sobald er quittiert ist.</p>}
      {!waiting && !allowed.PAPER_RESET && (openPositions > 0 || workingOrders > 0) && (
        <p className="text-xs text-ink-2">Reset erst ohne Bestand möglich ({plural(openPositions, "Position", "Positionen")}, {plural(workingOrders, "offene Order", "offene Orders")}).</p>
      )}

      {current && (
        <form action={action} className="rounded-lg border border-line bg-surface-2 p-3">
          <input type="hidden" name="type" value={current.type} />
          <input type="hidden" name="accountId" value={accountId} />
          <div className="text-sm font-semibold">{current.label} – Wirkung</div>
          <p className="mt-1 text-sm">{current.effect}</p>
          {current.type === "PAPER_RESET" && (
            <label className="mt-3 block max-w-xs text-xs font-medium text-muted">
              Startkapital der neuen Episode in USD (virtuell)
              <input name="startCash" required defaultValue={startCash} inputMode="decimal" className="mt-1 block w-full tabular" autoComplete="off" />
            </label>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button type="submit" disabled={pending} className="btn disabled:opacity-60">
              {pending ? "Sende …" : `Bestätigen: ${current.label}`}
            </button>
            <button type="button" className="btn-ghost" onClick={() => setOpen(null)} disabled={pending}>
              Abbrechen
            </button>
          </div>
          {result?.error && (
            <p role="alert" className="mt-2 text-sm text-critical">
              {result.error}
            </p>
          )}
        </form>
      )}

      {result?.commandId && (!sent || sent.status === "PENDING") && (
        <p role="status" className="text-sm">
          Befehl gesendet – die Engine quittiert innert etwa einer Minute. <span className="text-ink-2">(Nr. {result.commandId})</span>
        </p>
      )}

      {last && (
        <p className="text-xs text-ink-2">
          Letzter Befehl: <span className="font-medium text-ink">{COMMAND_LABEL[last.type] ?? last.type}</span> (Nr. {last.id}, {last.issued}) –{" "}
          <span className="font-medium text-ink">{COMMAND_STATUS[last.status]?.label ?? last.status}</span>
          {last.reason && <span className="break-words">: {last.reason}</span>}
        </p>
      )}
    </div>
  );
}
