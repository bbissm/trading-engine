"""Meldungen des Live-Prozesses in die Tabelle `alert` (Zustellung übernimmt der Benachrichtigungsdienst).

Entdoppelung wie in docs/06, 4.2: Schlüssel (Art, Konto, Objekt, Zustand). Eine offene Meldung (Status ≠ RESOLVED)
mit gleichem Schlüssel wird aktualisiert (Zähler +1), statt eine neue zu erzeugen. Titel beginnen mit «[LIVE]».
Texte enthalten nie Zugangsdaten oder vollständige Kontonummern.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

Conn = psycopg.Connection[dict[str, Any]]
LEVELS = ("INFO", "SIGNAL", "WARNING", "CRITICAL")


def dedup_key(kind: str, account_id: str, obj: str, state: str) -> str:
    return f"{kind}|{account_id}|{obj}|{state}"


def raise_alert(conn: Conn, level: str, kind: str, account_id: str, obj: str, state: str, title: str, body: str,
                now: datetime, data: dict[str, Any] | None = None) -> None:
    assert level in LEVELS
    key = dedup_key(kind, account_id, obj, state)
    shown = title if title.startswith("[LIVE]") else f"[LIVE] {title}"
    conn.execute(
        """
        insert into alert (created_at, updated_at, level, mode, kind, dedup_key, title, body, data, status)
        values (%s, %s, %s, 'LIVE', %s, %s, %s, %s, %s, 'OPEN')
        on conflict (dedup_key) where status <> 'RESOLVED'
        do update set updated_at = excluded.updated_at, occurrences = alert.occurrences + 1, title = excluded.title,
                      body = excluded.body, data = excluded.data,
                      level = case when excluded.level = 'CRITICAL' then 'CRITICAL' else alert.level end
        """,
        (now, now, level, kind, key, shown, body, Jsonb(data or {})),
    )


def resolve_alert(conn: Conn, kind: str, account_id: str, obj: str, state: str, now: datetime) -> None:
    conn.execute(
        "update alert set status = 'RESOLVED', resolved_at = %s, updated_at = %s where dedup_key = %s and status <> 'RESOLVED'",
        (now, now, dedup_key(kind, account_id, obj, state)),
    )
