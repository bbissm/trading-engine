"""Bedienbefehle aus der Web-App. Jeder Befehl wird fachlich geprüft, quittiert und auditiert.
In dieser Etappe existiert nur PING; unbekannte Typen werden abgelehnt."""

from __future__ import annotations

from datetime import datetime

from ..ports import Store

ACTOR = "engine:commands"


def process_pending(store: Store, now: datetime) -> int:
    handled = 0
    for cmd in store.pending_commands():
        if cmd.type == "PING":
            store.complete_command(cmd.id, "DONE", {"pong": True, "engine_time": now.isoformat()}, now)
        else:
            store.complete_command(cmd.id, "REJECTED", {"reason": f"Unbekannter Befehl {cmd.type}"}, now)
            store.audit(ACTOR, "command.rejected", f"command:{cmd.id}", {"type": cmd.type, "issued_by": cmd.issued_by})
        handled += 1
    return handled
