"""Bedienbefehle aus der Web-App. Jeder Befehl wird fachlich geprüft, quittiert und auditiert.
Unbekannte Typen werden abgelehnt. Es gibt keinen Befehl, der eine Live-Order auslöst."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..adapters.pg_paper import PaperRepo
from ..ports import Store
from ..research import commands as lab_commands
from . import approvals, notify_commands, paper

log = logging.getLogger(__name__)
ACTOR = "engine:commands"
# Live-Befehle verarbeitet nur der Live-Prozess. Bewusst als Liste hier statt als Import aus tradingengine.live,
# damit der Paper-/Signalprozess das Live-Paket nie lädt (Strukturtest T3).
LIVE_COMMAND_TYPES = frozenset({"LIVE_ACCOUNT_REGISTER", "LIVE_PAUSE", "LIVE_RESUME", "LIVE_STOP", "LIVE_CLOSE_ALL", "LIVE_EMERGENCY",
                                "ORDER_APPROVAL_DECIDE", "MANDATE_SUSPEND", "LIVE_ASSIGN_POSITION"})


def process_pending(store: Store, now: datetime, paper_repo: PaperRepo | None = None, conn: Any = None) -> int:
    """`conn`: Datenbankverbindung für Befehle ausserhalb des Paper-Handels (Meldungen, Lernlabor)."""
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
        elif cmd.type.startswith("LAB_") and conn is not None:
            status, result = _guarded(cmd, lambda c=cmd: lab_commands.handle(conn, c, now))  # type: ignore[misc]
        elif cmd.type == "APPROVAL_DECIDE" and conn is not None and paper_repo is not None:
            status, result = _guarded(cmd, lambda c=cmd: approvals.decide(conn, paper_repo, c, now))  # type: ignore[misc]
        elif cmd.type in notify_commands.TYPES and conn is not None:
            status, result = _guarded(cmd, lambda c=cmd: notify_commands.handle(conn, c, now))  # type: ignore[misc]
        else:
            status, result = "REJECTED", {"reason": f"Unbekannter Befehl {cmd.type}"}
        store.complete_command(cmd.id, status, result, now)
        kind = "command.done" if status == "DONE" else "command.rejected"
        if cmd.type != "PING" or status != "DONE":
            store.audit(ACTOR, kind, f"command:{cmd.id}", {"type": cmd.type, "target": cmd.target, "issued_by": cmd.issued_by, "result": result})
        handled += 1
    return handled


def _guarded(cmd: Any, fn: Callable[[], tuple[str, dict[str, Any]]]) -> tuple[str, dict[str, Any]]:
    """Ein fehlerhafter Befehl darf den Tick nicht blockieren."""
    try:
        return fn()
    except Exception as exc:
        log.exception("Befehl %s fehlgeschlagen", cmd.id)
        return "REJECTED", {"reason": f"Interner Fehler: {type(exc).__name__}"}
