"""Meldungen anlegen, aktualisieren, bestätigen und auflösen (docs/06, 4.2).

- Entdoppelung über `dedup_key`: ein offener (nicht aufgelöster) Zustand mit gleichem Schlüssel aktualisiert die
  bestehende Meldung (Zähler `occurrences`, Text, Zeitpunkt) statt eine neue anzulegen – keine Flut.
- Die Stufe kann nur steigen. Steigt sie, beginnt die Zustellung für die neue Stufe von vorn, und eine bereits
  bestätigte Meldung wird wieder offen.
- Bestätigen stoppt die Eskalation, genehmigt aber nichts (keine Order, keine Freigabe).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

Conn = psycopg.Connection[dict[str, Any]]

LEVELS = ("INFO", "SIGNAL", "WARNING", "CRITICAL")
MODES = ("PAPER", "LIVE", "RESEARCH", "SYSTEM")
PREFIX = {"PAPER": "[PAPER]", "LIVE": "[LIVE]", "RESEARCH": "[FORSCHUNG]", "SYSTEM": "[SYSTEM]"}
TEST_KIND = "TEST"


def _rank(expr: str) -> str:
    return f"array_position(array['INFO','SIGNAL','WARNING','CRITICAL']::text[], {expr})"


_RISES = f"{_rank('excluded.level')} > {_rank('alert.level')}"
_UPSERT = f"""
    insert into alert (created_at, updated_at, level, mode, kind, dedup_key, title, body, data, status, occurrences)
    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', 1)
    on conflict (dedup_key) where status <> 'RESOLVED' do update set
        occurrences = alert.occurrences + 1,
        updated_at = excluded.updated_at,
        title = excluded.title,
        body = excluded.body,
        data = excluded.data,
        level = case when {_RISES} then excluded.level else alert.level end,
        status = case when {_RISES} then 'OPEN' else alert.status end,
        acknowledged_at = case when {_RISES} then null else alert.acknowledged_at end,
        acknowledged_by = case when {_RISES} then null else alert.acknowledged_by end,
        escalation_level = case when {_RISES} then 0 else alert.escalation_level end,
        next_escalation_at = case when {_RISES} then null else alert.next_escalation_at end,
        last_sent_at = case when {_RISES} then null else alert.last_sent_at end
    returning id
"""


def prefixed(mode: str, title: str) -> str:
    """Titel mit Modus-Präfix in Grossbuchstaben – jede Meldung beginnt damit (docs/06, 4.2)."""
    prefix = PREFIX.get(mode, "[SYSTEM]")
    return title if title.startswith(prefix) else f"{prefix} {title}"


def raise_alert(conn: Conn, level: str, mode: str, kind: str, dedup_key: str, title: str, body: str,
                data: dict[str, Any] | None = None, now: datetime | None = None) -> int:
    """Legt eine Meldung an oder aktualisiert die offene mit gleichem Schlüssel. Rückgabe: id der Meldung."""
    if level not in LEVELS:
        raise ValueError(f"Unbekannte Stufe {level}")
    if mode not in MODES:
        raise ValueError(f"Unbekannter Modus {mode}")
    at = now or datetime.now(UTC)
    row = conn.execute(
        _UPSERT,
        (at, at, level, mode, kind, dedup_key[:300], title[:300], body[:4000], None if data is None else Jsonb(data)),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def resolve_alert(conn: Conn, dedup_key: str, now: datetime) -> bool:
    """Zustand ist verschwunden: Meldung auflösen (stoppt jede weitere Zustellung)."""
    cur = conn.execute(
        "update alert set status = 'RESOLVED', resolved_at = %s, updated_at = %s, next_escalation_at = null "
        "where dedup_key = %s and status <> 'RESOLVED'",
        (now, now, dedup_key),
    )
    return cur.rowcount > 0


def resolve_matching(conn: Conn, pattern: str, keep: set[str], now: datetime) -> int:
    """Löst offene Meldungen auf, deren Schlüssel auf `pattern` (SQL LIKE) passt, ausser denen in `keep`."""
    cur = conn.execute(
        "update alert set status = 'RESOLVED', resolved_at = %s, updated_at = %s, next_escalation_at = null "
        "where dedup_key like %s and status <> 'RESOLVED' and not (dedup_key = any(%s))",
        (now, now, pattern, list(keep)),
    )
    return cur.rowcount


def open_alert(conn: Conn, dedup_key: str) -> dict[str, Any] | None:
    return conn.execute("select * from alert where dedup_key = %s and status <> 'RESOLVED'", (dedup_key,)).fetchone()


def acknowledge(conn: Conn, alert_id: int, by: str, now: datetime) -> bool:
    """Bestätigt eine offene Meldung: stoppt die Eskalation. Genehmigt keinen Trade und keine Freigabe.

    Ein bestätigter Testalarm setzt `channel_status.last_test_ack_at`.
    """
    row = conn.execute(
        "update alert set status = 'ACKNOWLEDGED', acknowledged_at = %s, acknowledged_by = %s, next_escalation_at = null, updated_at = %s "
        "where id = %s and status = 'OPEN' returning kind",
        (now, by[:80], now, alert_id),
    ).fetchone()
    if row is None:
        return False
    if row["kind"] == TEST_KIND:
        conn.execute("update channel_status set last_test_ack_at = %s where last_test_sent_at is not null and last_test_sent_at <= %s", (now, now))
    return True
