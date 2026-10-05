"""Welche Aufgaben ein Vercel-Projekt ausführt (docs/11, E-8).

Beide Engine-Projekte deployen dasselbe Verzeichnis `engine/` mit denselben Cron-Einträgen. Die Umgebungsvariable
`ENGINE_ROLE` legt fest, was tatsächlich läuft:

- `worker` (Standard, Projekt trading-engine-worker): Tick (Daten, Signale, Paper, Meldungen) und Lernlabor.
  Hier liegen nie Handelsschlüssel; Live ist ausgeschaltet.
- `live` (Projekt trading-engine-live): ausschliesslich der Live-Autopilot. Nur hier liegen die Kraken-Schlüssel
  und die Datenbank-Zugangsdaten der Rolle te_live.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

WORKER = "worker"
LIVE = "live"


def engine_role(env: Mapping[str, str] | None = None) -> str:
    e = env if env is not None else os.environ
    role = (e.get("ENGINE_ROLE") or WORKER).strip().lower()
    return role if role in (WORKER, LIVE) else "unknown"


def runs_worker(env: Mapping[str, str] | None = None) -> bool:
    return engine_role(env) == WORKER


def runs_live(env: Mapping[str, str] | None = None) -> bool:
    return engine_role(env) == LIVE
