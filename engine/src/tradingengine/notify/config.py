"""Einstellungen der Benachrichtigung aus der Tabelle `setting` und Ruhezeiten (Europe/Zurich)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

Conn = psycopg.Connection[dict[str, Any]]
ZURICH = ZoneInfo("Europe/Zurich")

QUIET_KEY = "notify.quiet_hours"
CHANNELS_KEY = "notify.channels"
SMS_KEY = "notify.sms_enabled"
DEFAULT_QUIET: dict[str, Any] = {"start": "22:00", "end": "07:00", "critical_bypass": True}
CHANNELS = ("TELEGRAM", "PUSHOVER", "EMAIL")
HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def get_setting(conn: Conn, key: str) -> Any:
    row = conn.execute("select value from setting where key = %s", (key,)).fetchone()
    return None if row is None else row["value"]


def valid_quiet(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == {"start", "end", "critical_bypass"}
            and isinstance(value["start"], str) and HHMM.match(value["start"]) is not None
            and isinstance(value["end"], str) and HHMM.match(value["end"]) is not None
            and isinstance(value["critical_bypass"], bool))


def valid_channels(value: Any) -> bool:
    return isinstance(value, dict) and bool(value) and set(value) <= set(CHANNELS) and all(isinstance(v, bool) for v in value.values())


def quiet_hours(conn: Conn) -> dict[str, Any]:
    value = get_setting(conn, QUIET_KEY)
    return value if valid_quiet(value) else dict(DEFAULT_QUIET)


def enabled_channels(conn: Conn) -> dict[str, bool]:
    """Standard: alle Kanäle eingeschaltet (wirksam nur, wenn sie eingerichtet sind)."""
    value = get_setting(conn, CHANNELS_KEY)
    out = dict.fromkeys(CHANNELS, True)
    if valid_channels(value):
        out.update(value)
    return out


def _hm(text: str) -> time:
    h, m = text.split(":")
    return time(int(h), int(m))


def quiet_state(now: datetime, cfg: dict[str, Any]) -> tuple[bool, datetime | None]:
    """(in Ruhezeit?, Ende der laufenden Ruhezeit in UTC). Start = Ende bedeutet: keine Ruhezeit."""
    start, end = _hm(cfg["start"]), _hm(cfg["end"])
    if start == end:
        return False, None
    local = now.astimezone(ZURICH)
    t = local.time()
    today = local.date()

    def at(d: Any, hm: time) -> datetime:
        return datetime.combine(d, hm, tzinfo=ZURICH).astimezone(UTC)

    if start < end:
        return (start <= t < end), (at(today, end) if start <= t < end else None)
    if t >= start:
        return True, at(today + timedelta(days=1), end)
    if t < end:
        return True, at(today, end)
    return False, None
