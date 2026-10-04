"""Welche Strategieversionen erzeugen Signale? Die drei Standardversionen und jede Lab-Version, die der Nutzer
für den Shadow-Betrieb (oder später für Live) freigegeben hat. Eine Version ändert sich nie; freigegeben wird
immer eine bestimmte Version."""

from __future__ import annotations

from typing import Any

from ..core.signals import StrategyVersion
from ..core.strategies import ACTIVE, StrategyDef, from_version

RELEASED = ("SHADOW", "APPROVED_LIVE")


def released_versions(conn: Any) -> list[StrategyDef]:
    rows = conn.execute(
        """
        select distinct v.id, v.strategy, v.version, v.params, v.regime_rule_version
        from strategy_version v join approval a on a.strategy_version_id = v.id
        where a.decision = any(%s) order by v.id
        """,
        (list(RELEASED),),
    ).fetchall()
    known = {s.version.id for s in ACTIVE}
    out: list[StrategyDef] = []
    for r in rows:
        if r["id"] in known:
            continue
        sd = from_version(StrategyVersion(r["id"], r["strategy"], int(r["version"]), r["params"], r["regime_rule_version"]))
        if sd is not None:
            out.append(sd)
    return out


def all_strategies(conn: Any) -> list[StrategyDef]:
    return [*ACTIVE, *released_versions(conn)]
