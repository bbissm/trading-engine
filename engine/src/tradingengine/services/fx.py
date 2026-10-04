"""Tageskurse USD→CHF (EZB-Referenzkurse über api.frankfurter.dev) für die Bewertung in CHF (docs/06, 2.2).

- Beim ersten Lauf werden die letzten 400 Tage in einer Anfrage nachgeladen, danach höchstens alle paar Stunden,
  solange das heutige Fixing fehlt.
- Wochenenden und Feiertage haben kein Fixing: es wird nichts erfunden. `fx_rate_for` liefert das letzte Fixing
  am oder vor dem Datum, zusammen mit dessen Datum (sichtbare Quelle und Zeit).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import psycopg

log = logging.getLogger(__name__)
Conn = psycopg.Connection[dict[str, Any]]

ZURICH = ZoneInfo("Europe/Zurich")
SOURCE = "ecb-frankfurter"
API = "https://api.frankfurter.dev/v1"
BACKFILL_DAYS = 400
REFETCH_AFTER = timedelta(hours=3)
PAIRS = (("USD", "CHF"),)
TIMEOUT = httpx.Timeout(8.0, connect=3.0)


def _due(conn: Conn, base: str, quote: str, today: date, now: datetime) -> date | None:
    """Ab welchem Datum abzurufen ist, oder None (nichts fällig)."""
    row = conn.execute("select max(date) as d, max(fetched_at) as f from fx_rate where base = %s and quote = %s", (base, quote)).fetchone()
    latest = None if row is None or row["d"] is None else date.fromisoformat(row["d"])
    if latest is None:
        return today - timedelta(days=BACKFILL_DAYS)
    if latest >= today:
        return None
    fetched: datetime | None = None if row is None else row["f"]
    if fetched is not None and now - fetched < REFETCH_AFTER:
        return None
    return latest  # inklusive des letzten bekannten Tages: die Antwort ist nie leer und frischt fetched_at auf


def refresh_fx(conn: Conn, now: datetime, client: httpx.Client | None = None) -> dict[str, Any]:
    """Höchstens eine Anfrage je Währungspaar und Lauf; Fehler werden geloggt, nie geraten."""
    today = now.astimezone(ZURICH).date()
    out: dict[str, Any] = {}
    http = client or httpx.Client(timeout=TIMEOUT)
    try:
        _refresh(conn, now, today, http, out)
    finally:
        if client is None:
            http.close()
    return out


def _refresh(conn: Conn, now: datetime, today: date, http: httpx.Client, out: dict[str, Any]) -> None:
    for base, quote in PAIRS:
        start = _due(conn, base, quote, today, now)
        if start is None:
            continue
        try:
            resp = http.get(f"{API}/{start.isoformat()}..{today.isoformat()}", params={"base": base, "symbols": quote}, timeout=TIMEOUT)
            resp.raise_for_status()
            body = resp.json(parse_float=Decimal)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("FX %s/%s nicht abrufbar: %s", base, quote, type(exc).__name__)
            out[f"{base}/{quote}"] = "error"
            continue
        rates = body.get("rates") if isinstance(body, dict) else None
        rows: list[tuple[str, str, str, Decimal, str, datetime]] = []
        for day, values in (rates or {}).items():
            value = values.get(quote) if isinstance(values, dict) else None
            if value is None:
                continue
            rate = Decimal(str(value))
            if rate.is_finite() and rate > 0:
                rows.append((base, quote, date.fromisoformat(day).isoformat(), rate, SOURCE, now))
        if rows:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    insert into fx_rate (base, quote, date, rate, source, fetched_at) values (%s, %s, %s, %s, %s, %s)
                    on conflict (base, quote, date) do update set rate = excluded.rate, source = excluded.source, fetched_at = excluded.fetched_at
                    """,
                    rows,
                )
        out[f"{base}/{quote}"] = len(rows)


def fx_rate_for(conn: Conn, base: str, quote: str, day: date) -> tuple[Decimal, date] | None:
    """Letztes Fixing am oder vor `day` und dessen Datum; None, wenn keines bekannt ist."""
    if base == quote:
        return Decimal(1), day
    row = conn.execute(
        "select rate, date from fx_rate where base = %s and quote = %s and date <= %s order by date desc limit 1",
        (base, quote, day.isoformat()),
    ).fetchone()
    if row is None:
        return None
    return Decimal(row["rate"]), date.fromisoformat(row["date"])
