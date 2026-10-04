"""Bedienbefehle des Lernlabors (Tabelle `command`). Der Engine-Befehlsprozessor ruft `handle` für Typen mit
Präfix `LAB_` auf, quittiert und auditiert. Keiner dieser Befehle gibt etwas frei oder erteilt Orders.

- LAB_RUN    params {kind, strategy, config?}          → DONE {experiment_id} oder REJECTED {reason}
- LAB_PAUSE  target = Experiment-ID
- LAB_RESUME target = Experiment-ID
- LAB_ABORT  target = Experiment-ID                     → Ergebnis ABGEBROCHEN, Varianten bleiben gezählt
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..ports import Command
from . import runner
from .data import Conn

TYPES = ("LAB_RUN", "LAB_PAUSE", "LAB_RESUME", "LAB_ABORT")


def _target(cmd: Command) -> int | None:
    raw = cmd.target if cmd.target is not None else cmd.params.get("experiment_id")
    try:
        return int(str(raw).removeprefix("experiment:"))
    except (TypeError, ValueError):
        return None


def handle(conn: Conn, cmd: Command, now: datetime) -> tuple[str, dict[str, Any]]:
    if cmd.type == "LAB_RUN":
        kind, strategy = cmd.params.get("kind"), cmd.params.get("strategy")
        config = cmd.params.get("config") or {}
        if not isinstance(kind, str) or not isinstance(strategy, str) or not isinstance(config, dict):
            return "REJECTED", {"reason": "LAB_RUN braucht params {kind, strategy, config?}"}
        exp_id, reason = runner.create_experiment(conn, kind, strategy, config, cmd.issued_by, now)
        if exp_id is None:
            return "REJECTED", {"reason": reason}
        return "DONE", {"experiment_id": exp_id}
    if cmd.type not in TYPES:
        return "REJECTED", {"reason": f"Unbekannter Befehl {cmd.type}"}
    exp_id = _target(cmd)
    if exp_id is None:
        return "REJECTED", {"reason": "Experiment-ID fehlt (target)"}
    if cmd.type == "LAB_PAUSE":
        err = runner.pause(conn, exp_id)
    elif cmd.type == "LAB_RESUME":
        err = runner.resume(conn, exp_id)
    else:
        err = runner.abort(conn, exp_id, now, f"durch {cmd.issued_by} abgebrochen")
    return ("REJECTED", {"reason": err}) if err else ("DONE", {"experiment_id": exp_id})
