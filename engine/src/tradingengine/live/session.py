"""Einziger Ort, an dem der echte Kraken-Handelsclient entsteht (Strukturtest T3 prüft das).

`open_live_exchange` liefert nur dann einen Client, wenn `LIVE_TRADING_ENABLED == "true"` und beide Schlüssel
vorhanden sind. Paper-, Signal- und Lernlabor-Code importiert dieses Paket nicht; `api/tick.py` liest keine
Kraken-Schlüssel.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ..adapters.pg_store import PgStore
from ..schema_version import SCHEMA_VERSION
from .kraken_private import KrakenPrivate
from .locks import ENV_KEY, ENV_SECRET, key_fingerprint, keys_present, live_enabled
from .manager import LiveConfig, run_tick


def open_live_exchange(env: Mapping[str, str] | None = None) -> KrakenPrivate | None:
    e = env if env is not None else os.environ
    if not live_enabled(e) or not keys_present(e):
        return None
    return KrakenPrivate(e[ENV_KEY], e[ENV_SECRET])


def config(env: Mapping[str, str], has_exchange: bool) -> LiveConfig:
    return LiveConfig(enabled=live_enabled(env), keys=has_exchange, fingerprint=key_fingerprint(env[ENV_KEY]) if has_exchange else None,
                      schema_version=SCHEMA_VERSION)


def run_live(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Ein Live-Durchlauf für den Vercel-Cron `api/live.py`."""
    e = env if env is not None else os.environ
    if not live_enabled(e):
        return {"live": "disabled"}
    dsn = e.get("LIVE_DATABASE_URL") or e.get("DATABASE_URL")
    if not dsn:
        return {"live": "error", "error": "DATABASE_URL fehlt"}
    store = PgStore(dsn)
    try:
        version = store.schema_version()
        if version != SCHEMA_VERSION:
            return {"live": "error", "error": f"Schema-Version {version}, erwartet {SCHEMA_VERSION}"}
        ex = open_live_exchange(e)
        return run_tick(store.connection(), ex, datetime.now(UTC), config(e, ex is not None))
    finally:
        store.reset()
