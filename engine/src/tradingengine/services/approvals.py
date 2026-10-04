"""Entscheid über einen Freigabe-Vorschlag des Lernlabors (docs/01, 3.5).

Über diesen Befehl sind nur zwei Entscheide möglich: ablehnen oder Shadow-Betrieb. Shadow legt ein eigenes
Paper-Konto an, das ausschliesslich diese Version handelt (Forward-Evidenz für G3) und startet es. Die
Echtgeld-Freigabe (APPROVED_LIVE) verlangt Step-up-Anmeldung und ein Live-Mandat und läuft nicht hier.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from ..adapters.pg_paper import PaperRepo
from ..core.candles import floor_time
from ..core.costs import KRAKEN_SPOT_TIER1
from ..core.risk import RiskPolicy
from ..ports import Command

ALLOWED = ("REJECTED", "SHADOW")
SHADOW_CASH = Decimal(10000)


def decide(conn: Any, repo: PaperRepo, cmd: Command, now: datetime) -> tuple[str, dict[str, Any]]:
    decision = cmd.params.get("decision")
    if decision not in ALLOWED:
        return "REJECTED", {"reason": "Nur «abgelehnt» oder «Shadow» möglich; Live-Freigabe erfordert Step-up und Mandat"}
    try:
        approval_id = int(str(cmd.target).removeprefix("approval:"))
    except ValueError:
        return "REJECTED", {"reason": "Freigabe-ID fehlt (target)"}
    note = str(cmd.params.get("note") or "")[:500] or None
    row = conn.execute(
        "update approval set decision = %s, decided_at = %s, decided_by = %s, note = %s where id = %s and decision = 'PENDING' "
        "returning strategy_version_id",
        (decision, now, cmd.issued_by, note, approval_id),
    ).fetchone()
    if row is None:
        return "REJECTED", {"reason": f"Freigabe {approval_id} nicht gefunden oder bereits entschieden"}
    version_id = row["strategy_version_id"]
    if decision == "REJECTED":
        return "DONE", {"approval_id": approval_id, "decision": decision}

    account_id = "shadow-" + "".join(ch if ch.isalnum() else "-" for ch in version_id.lower()).strip("-")[:60]
    if not any(a["id"] == account_id for a in repo.accounts()):
        sim_through = floor_time(now, "4h")
        repo.create_account(account_id, f"Shadow {version_id}", "USD", SHADOW_CASH, RiskPolicy(), [version_id], KRAKEN_SPOT_TIER1.version, "sim@1",
                            now, sim_through)
        repo.set_autopilot(account_id, "ACTIVE", f"Shadow-Betrieb für {version_id} (Freigabe {approval_id})", now)
    return "DONE", {"approval_id": approval_id, "decision": decision, "shadow_account": account_id}
