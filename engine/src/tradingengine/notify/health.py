"""Überwachung der Kanäle (docs/06, 4.3): technische Prüfung alle 15 min, Testalarm, Kanalausfall-Policy.

Kanalausfall-Policy (Standard): ist kein kritischer Kanal (Pushover oder Telegram) eingerichtet, eingeschaltet und
erreichbar, oder bleibt ein Testalarm 24 h unbestätigt → SYSTEM-Warnung und `live_entries_blocked = True`.
Live gibt es noch nicht: die Funktion liefert das Kennzeichen nur zurück; der künftige Live-Autopilot muss bei
`True` Einstiege pausieren (Schutz und Exits bleiben aktiv). Paper läuft in jedem Fall weiter.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import psycopg

from . import config
from .alerts import TEST_KIND, raise_alert, resolve_alert
from .channels import Channels

Conn = psycopg.Connection[dict[str, Any]]

CHECK_INTERVAL = timedelta(minutes=15)
TEST_ACK_WINDOW = timedelta(hours=24)
CRITICAL_CHANNELS = ("PUSHOVER", "TELEGRAM")
ALL_CHANNELS = ("TELEGRAM", "PUSHOVER", "EMAIL")
OUTAGE_KEY = "notify:channel-outage"


def _upsert(conn: Conn, channel: str, configured: bool, ok: bool, detail: str, now: datetime) -> None:
    conn.execute(
        """
        insert into channel_status (channel, configured, ok, detail, last_check_at) values (%s, %s, %s, %s, %s)
        on conflict (channel) do update set configured = excluded.configured, ok = excluded.ok, detail = excluded.detail,
            last_check_at = excluded.last_check_at
        """,
        (channel, configured, ok, detail[:300], now),
    )


def check_channels(conn: Conn, now: datetime, channels: Channels | None = None) -> dict[str, Any]:
    """Prüft fällige Kanäle (höchstens ein HTTP-Aufruf je Kanal und 15 min) und wertet die Ausfall-Policy aus."""
    if channels is None:
        own = Channels.from_env()
        try:
            return check_channels(conn, now, own)
        finally:
            own.close()
    ch = channels
    enabled = config.enabled_channels(conn)
    rows = {r["channel"]: r for r in conn.execute("select * from channel_status").fetchall()}
    checked: list[str] = []
    for name, sender in ch.by_name().items():
        row = rows.get(name)
        if row is not None and row["last_check_at"] is not None and now - row["last_check_at"] < CHECK_INTERVAL \
                and row["configured"] == sender.configured:
            continue
        if not sender.configured:
            _upsert(conn, name, False, False, "Nicht eingerichtet – fehlt: " + ", ".join(sender.missing()), now)
        else:
            result = sender.check()
            detail = "Erreichbar" if result.ok else f"Fehler: {result.error}"
            if not enabled.get(name, True):
                detail += " (in den Einstellungen abgeschaltet)"
            _upsert(conn, name, True, result.ok, detail, now)
        checked.append(name)
    if "IN_APP" not in rows:
        _upsert(conn, "IN_APP", True, True, "Immer verfügbar", now)

    rows = {r["channel"]: r for r in conn.execute("select * from channel_status").fetchall()}
    reasons: list[str] = []
    capable = [c for c in CRITICAL_CHANNELS if c in rows and rows[c]["configured"] and rows[c]["ok"] and enabled.get(c, True)]
    if not capable:
        reasons.append("Kein kritischer Kanal (Pushover oder Telegram) eingerichtet, eingeschaltet und erreichbar")
    for r in rows.values():
        sent, ack = r["last_test_sent_at"], r["last_test_ack_at"]
        if sent is not None and now - sent >= TEST_ACK_WINDOW and (ack is None or ack < sent):
            reasons.append(f"Testalarm vom {sent.astimezone(config.ZURICH):%d.%m.%Y %H:%M} seit über 24 h unbestätigt")
            break
    if reasons:
        raise_alert(conn, "WARNING", "SYSTEM", "CHANNEL_OUTAGE", OUTAGE_KEY, "Benachrichtigung eingeschränkt",
                    "; ".join(reasons) + ". Live-Einstiege wären pausiert (Schutz und Exits bleiben aktiv). Paper läuft weiter. "
                    "Kanäle unter «Meldungen» prüfen.", {"reasons": reasons}, now)
    else:
        resolve_alert(conn, OUTAGE_KEY, now)
    return {"checked": checked, "live_entries_blocked": bool(reasons), "reasons": reasons}


def send_test_alert(conn: Conn, now: datetime) -> int:
    """Testalarm mit Bestätigungspflicht: geht sofort an alle eingerichteten Kanäle (auch in der Ruhezeit)."""
    conn.execute(
        "update alert set status = 'RESOLVED', resolved_at = %s, updated_at = %s where kind = %s and status = 'OPEN'",
        (now, now, TEST_KIND),
    )
    alert_id = raise_alert(
        conn, "INFO", "SYSTEM", TEST_KIND, f"test:{now.isoformat()}", "Testalarm – bitte bestätigen",
        "Dies ist ein Testalarm. Bitte bestätigen (Schaltfläche, Pushover-Quittung oder in der App), damit die Zustellung als geprüft gilt. "
        "Er löst keine Handlung aus.", None, now,
    )
    for name in (*ALL_CHANNELS, "IN_APP"):
        conn.execute(
            "insert into channel_status (channel, last_test_sent_at) values (%s, %s) on conflict (channel) do update set last_test_sent_at = excluded.last_test_sent_at",
            (name, now),
        )
    return alert_id
