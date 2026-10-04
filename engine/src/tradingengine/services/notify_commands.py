"""Bedienbefehle der Benachrichtigung: ALERT_ACK, NOTIFY_TEST, SETTINGS_SET, TELEGRAM_CALLBACK.

Keiner dieser Befehle erhöht Risiko oder berührt Live: Bestätigen stoppt nur die Eskalation; die einzige Handlung
über Telegram ist «Einstiege pausieren» für ein **Paper**-Konto, und auch die läuft als gewöhnlicher
PAPER_PAUSE-Befehl über den Paper-Handler (Tabelle `command`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ..notify import config
from ..notify.alerts import acknowledge
from ..notify.health import send_test_alert
from ..ports import Command

Conn = psycopg.Connection[dict[str, Any]]

TYPES = ("ALERT_ACK", "NOTIFY_TEST", "SETTINGS_SET", "TELEGRAM_CALLBACK")
ACK_NOTE = "Bestätigt – genehmigt keinen Trade"


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _ack(conn: Conn, alert_id: Any, by: str, now: datetime) -> tuple[str, dict[str, Any]]:
    aid = _int(alert_id)
    if aid is None:
        return "REJECTED", {"reason": "alert_id fehlt oder ungültig"}
    if not acknowledge(conn, aid, by, now):
        return "REJECTED", {"reason": f"Meldung {aid} ist nicht offen"}
    return "DONE", {"alert_id": aid, "acknowledged": True, "note": ACK_NOTE}


def _settings(conn: Conn, params: dict[str, Any], by: str, now: datetime) -> tuple[str, dict[str, Any]]:
    key, value = params.get("key"), params.get("value")
    if key == config.QUIET_KEY:
        ok = config.valid_quiet(value)
        hint = '{"start":"HH:MM","end":"HH:MM","critical_bypass":true|false}'
    elif key == config.CHANNELS_KEY:
        ok = config.valid_channels(value)
        hint = '{"TELEGRAM":bool,"PUSHOVER":bool,"EMAIL":bool}'
    elif key == config.SMS_KEY:
        ok = isinstance(value, bool)
        hint = "true|false"
    else:
        return "REJECTED", {"reason": f"Einstellung {key!r} ist nicht änderbar"}
    if not ok:
        return "REJECTED", {"reason": f"Ungültiger Wert für {key}, erwartet {hint}"}
    conn.execute(
        """
        insert into setting (key, value, updated_at, updated_by) values (%s, %s, %s, %s)
        on conflict (key) do update set value = excluded.value, updated_at = excluded.updated_at, updated_by = excluded.updated_by
        """,
        (key, Jsonb(value), now, by[:80]),
    )
    return "DONE", {"key": key, "value": value}


def _pause(conn: Conn, params: dict[str, Any], now: datetime) -> tuple[str, dict[str, Any]]:
    account_id = params.get("account_id")
    aid = _int(params.get("alert_id"))
    if not account_id and aid is not None:
        row = conn.execute("select mode, data from alert where id = %s", (aid,)).fetchone()
        if row is None:
            return "REJECTED", {"reason": f"Meldung {aid} unbekannt"}
        if row["mode"] != "PAPER":
            return "REJECTED", {"reason": "Pausieren über Telegram ist nur für Paper-Konten möglich"}
        account_id = (row["data"] or {}).get("account_id")
    if not isinstance(account_id, str) or not account_id:
        return "REJECTED", {"reason": "Kein Konto angegeben"}
    acc = conn.execute("select mode from account where id = %s", (account_id,)).fetchone()
    if acc is None:
        return "REJECTED", {"reason": f"Konto {account_id} unbekannt"}
    if acc["mode"] != "PAPER":
        return "REJECTED", {"reason": "Live-Konten lassen sich über Telegram nicht bedienen – bitte die Web-App nutzen"}
    row = conn.execute(
        "insert into command (type, target, params, issued_by, issued_at) values ('PAPER_PAUSE', %s, %s, 'telegram', %s) returning id",
        (account_id, Jsonb({"via": "telegram"}), now),
    ).fetchone()
    assert row is not None
    return "DONE", {"paper_pause_command_id": int(row["id"]), "account_id": account_id, "note": "Pause angefordert"}


def handle(conn: Conn, cmd: Command, now: datetime) -> tuple[str, dict[str, Any]]:
    """Rückgabe: (DONE | REJECTED, Ergebnis). Unbekannte Typen und Aktionen werden abgelehnt."""
    p = cmd.params or {}
    if cmd.type == "ALERT_ACK":
        return _ack(conn, p.get("alert_id"), cmd.issued_by, now)
    if cmd.type == "NOTIFY_TEST":
        return "DONE", {"alert_id": send_test_alert(conn, now)}
    if cmd.type == "SETTINGS_SET":
        return _settings(conn, p, cmd.issued_by, now)
    if cmd.type == "TELEGRAM_CALLBACK":
        action = p.get("action")
        if action == "ack":
            return _ack(conn, p.get("alert_id"), "telegram", now)
        if action == "pause":
            return _pause(conn, p, now)
        return "REJECTED", {"reason": "Über Telegram sind nur «Bestätigen» und «Einstiege pausieren» (Paper) möglich"}
    return "REJECTED", {"reason": f"Unbekannter Befehl {cmd.type}"}
