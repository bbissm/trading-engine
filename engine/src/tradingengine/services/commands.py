"""Bedienbefehle aus der Web-App. Jeder Befehl wird fachlich geprüft, quittiert und auditiert.
Unbekannte Typen werden abgelehnt. Es gibt keinen Befehl, der eine Live-Order auslöst."""

from __future__ import annotations

import logging
from datetime import datetime

from ..adapters.pg_paper import PaperRepo
from ..ports import Store
from . import paper

log = logging.getLogger(__name__)
ACTOR = "engine:commands"
# Live-Befehle verarbeitet nur der Live-Prozess. Bewusst als Liste hier statt als Import aus tradingengine.live,
# damit der Paper-/Signalprozess das Live-Paket nie lädt (Strukturtest T3).
LIVE_COMMAND_TYPES = frozenset({"LIVE_ACCOUNT_REGISTER", "LIVE_PAUSE", "LIVE_RESUME", "LIVE_STOP", "LIVE_CLOSE_ALL", "LIVE_EMERGENCY",
                                "ORDER_APPROVAL_DECIDE", "MANDATE_SUSPEND"})


def process_pending(store: Store, now: datetime, paper_repo: PaperRepo | None = None) -> int:
    handled = 0
    for cmd in store.pending_commands():
        if cmd.type in LIVE_COMMAND_TYPES:
            continue  # gehört dem Live-Prozess (api/live.py); bleibt PENDING, bis er läuft
        if cmd.type == "PING":
            status, result = "DONE", {"pong": True, "engine_time": now.isoformat()}
        elif cmd.type.startswith("PAPER_") and paper_repo is not None:
            try:
                status, result = paper.handle_command(paper_repo, cmd, now)
            except Exception as exc:  # ein fehlerhafter Befehl darf den Tick nicht blockieren
                log.exception("Befehl %s fehlgeschlagen", cmd.id)
                status, result = "REJECTED", {"reason": f"Interner Fehler: {type(exc).__name__}"}
        else:
            status, result = "REJECTED", {"reason": f"Unbekannter Befehl {cmd.type}"}
        store.complete_command(cmd.id, status, result, now)
        kind = "command.done" if status == "DONE" else "command.rejected"
        if cmd.type != "PING" or status != "DONE":
            store.audit(ACTOR, kind, f"command:{cmd.id}", {"type": cmd.type, "target": cmd.target, "issued_by": cmd.issued_by, "result": result})
        handled += 1
    return handled
