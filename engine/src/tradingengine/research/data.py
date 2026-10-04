"""Forschungsdaten, Datensatz-Snapshots und Endprüfungs-Holdout (docs/04, 4.1 und 4.2).

**Quelle.** Lange Historien stammen von Bitstamp (öffentliche OHLC, `adapters/bitstamp_public.py`) und liegen als
eigene Instrumente `BITSTAMP:<BASE>/USD` (venue BITSTAMP, `in_universe = false`, Quelle `bitstamp`) in `candle`.
Bitstamp ist ein **Stellvertreter für den Kursverlauf**; Handel und Kostenmodell gehen von Kraken aus
(`KRAKEN_SPOT_TIER1`). Die Paare sind die heute liquiden (**Survivorship-Bias**: für BTC/ETH klein, für
Altcoins erheblich). `available_at` ist der Abrufzeitpunkt.

**Abruf.** Höflich und inkrementell: höchstens `max_requests` Abrufe je Aufruf, mindestens 1 s Abstand. Ein
Cursor für Paare, deren Historie später beginnt als der Startwunsch, steht in `feed_status`
(feed `bitstamp-research`, `detail = "cursor=<unix>"`).

**Snapshot.** Ein Experiment fixiert (Instrumente, Zeitebene, von, bis, Stand `as_of`) und speichert einen
SHA-256 über die kanonischen Kerzenzeilen (inkl. der Tageskerzen für das Regime und des Leitinstruments).
Gleicher Hash + gleicher Seed ⇒ identische Ergebnisse.

**Holdout.** Die jüngsten 12 Monate je Anlageklasse sind bei Projektstart eingefroren (`HOLDOUT_START`,
Version `holdout@1`): Entwicklungsdaten liegen davor, der Holdout wird je Strategiefamilie genau einmal für G2
geöffnet. Jeder Zugriff landet in `holdout_access`; ab dem zweiten gilt «Holdout verbraucht – nur noch
Forward-Evidenz zählt». Daten nach dem Holdout-Ende sind Forward-Zeit und werden hier nicht verwendet.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg

from ..adapters.bitstamp_public import PAGE_LIMIT, SOURCE, BitstampPublic
from ..core.candles import Candle, timeframe_delta

log = logging.getLogger(__name__)

Conn = psycopg.Connection[dict[str, Any]]

FEED = "bitstamp-research"
EARLIEST = datetime(2015, 1, 1, tzinfo=UTC)
LEADER = "BITSTAMP:BTC/USD"
REGIME_TF = "1d"

# (Instrument-ID, Bitstamp-Paar, Basis, Name)
RESEARCH_INSTRUMENTS: list[tuple[str, str, str, str]] = [
    ("BITSTAMP:BTC/USD", "btcusd", "BTC", "Bitcoin / US-Dollar (Bitstamp, Forschung)"),
    ("BITSTAMP:ETH/USD", "ethusd", "ETH", "Ether / US-Dollar (Bitstamp, Forschung)"),
    ("BITSTAMP:XRP/USD", "xrpusd", "XRP", "XRP / US-Dollar (Bitstamp, Forschung)"),
    ("BITSTAMP:SOL/USD", "solusd", "SOL", "Solana / US-Dollar (Bitstamp, Forschung)"),
    ("BITSTAMP:LINK/USD", "linkusd", "LINK", "Chainlink / US-Dollar (Bitstamp, Forschung)"),
]
RESEARCH_TIMEFRAMES = ["1d", "4h"]

HOLDOUT_VERSION = "holdout@1"
# Eingefroren bei Projektstart (4.10.2026): die letzten 12 Monate vor dem 1.10.2026.
HOLDOUT_START: dict[str, datetime] = {"CRYPTO_SPOT": datetime(2025, 10, 1, tzinfo=UTC)}
HOLDOUT_END: dict[str, datetime] = {"CRYPTO_SPOT": datetime(2026, 10, 1, tzinfo=UTC)}
HOLDOUT_CONSUMED_TEXT = "Holdout verbraucht – nur noch Forward-Evidenz zählt"


def holdout_window(asset_class: str = "CRYPTO_SPOT") -> tuple[datetime, datetime]:
    return HOLDOUT_START[asset_class], HOLDOUT_END[asset_class]


def development_end(asset_class: str = "CRYPTO_SPOT") -> datetime:
    return HOLDOUT_START[asset_class]


# ───────────────────────── Abruf ─────────────────────────


def ensure_research_instruments(conn: Conn) -> None:
    with conn.cursor() as cur:
        for inst_id, pair, base, name in RESEARCH_INSTRUMENTS:
            cur.execute(
                """
                insert into instrument (id, kind, venue, venue_symbol, name, base_asset, quote_currency, leader_id, in_universe)
                values (%s, 'CRYPTO_SPOT', 'BITSTAMP', %s, %s, %s, 'USD', %s, false)
                on conflict (id) do nothing
                """,
                (inst_id, pair, name, base, None if inst_id == LEADER else LEADER),
            )


@dataclass
class SyncResult:
    requests: int = 0
    inserted: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    up_to_date: list[str] = field(default_factory=list)


def _cursor(conn: Conn, inst_id: str, tf: str) -> datetime | None:
    row = conn.execute(
        "select detail from feed_status where feed = %s and instrument_id = %s and timeframe = %s", (FEED, inst_id, tf)
    ).fetchone()
    if row and row["detail"] and str(row["detail"]).startswith("cursor="):
        return datetime.fromtimestamp(int(str(row["detail"])[7:]), tz=UTC)
    return None


def _set_status(conn: Conn, inst_id: str, tf: str, status: str, detail: str | None, last_close: datetime | None, now: datetime) -> None:
    conn.execute(
        """
        insert into feed_status (feed, instrument_id, timeframe, status, detail, last_candle_close, last_ok_at, updated_at)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (feed, instrument_id, timeframe) do update set status = excluded.status, detail = excluded.detail,
            last_candle_close = coalesce(excluded.last_candle_close, feed_status.last_candle_close),
            last_ok_at = coalesce(excluded.last_ok_at, feed_status.last_ok_at), updated_at = excluded.updated_at
        """,
        (FEED, inst_id, tf, status, detail, last_close, now if status == "OK" else None, now),
    )


def insert_candles(conn: Conn, candles: list[Candle], available_at: datetime) -> int:
    if not candles:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into candle (instrument_id, timeframe, open_time, close_time, open, high, low, close, volume, trades, source, available_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict do nothing
            """,
            [(c.instrument_id, c.timeframe, c.open_time, c.close_time, c.open, c.high, c.low, c.close, c.volume, c.trades, c.source, available_at)
             for c in candles],
        )
        return max(cur.rowcount, 0)


def sync_research_data(
    conn: Conn,
    client: BitstampPublic,
    now: datetime,
    max_requests: int = 4,
    sleep: Callable[[float], None] = time.sleep,
    instruments: list[tuple[str, str, str, str]] | None = None,
) -> SyncResult:
    """Holt fehlende abgeschlossene Kerzen nach (Tageskerzen zuerst). Fehler eines Paars stoppen die anderen nicht."""
    ensure_research_instruments(conn)
    out = SyncResult()
    for tf in RESEARCH_TIMEFRAMES:
        step = timeframe_delta(tf)
        for inst_id, pair, _, _ in instruments or RESEARCH_INSTRUMENTS:
            key = f"{inst_id}|{tf}"
            row = conn.execute(
                "select max(open_time) as t from candle where instrument_id = %s and timeframe = %s and source = %s", (inst_id, tf, SOURCE)
            ).fetchone()
            last: datetime | None = row["t"] if row else None
            if last is not None and last + 2 * step > now:
                out.up_to_date.append(key)  # die nächste Kerze ist noch nicht abgeschlossen
                continue
            if out.requests >= max_requests:
                return out
            if last is not None:
                start = last
            else:
                start = _cursor(conn, inst_id, tf) or EARLIEST
                if tf != REGIME_TF:  # Feinere Ebene beginnt frühestens mit der ersten Tageskerze
                    first = conn.execute(
                        "select min(open_time) as t from candle where instrument_id = %s and timeframe = %s and source = %s", (inst_id, REGIME_TF, SOURCE)
                    ).fetchone()
                    if first and first["t"] is not None and first["t"] > start:
                        start = first["t"]
            if out.requests:
                sleep(1.0)  # höchstens ein Abruf pro Sekunde
            out.requests += 1
            try:
                candles = client.fetch_page(pair, inst_id, tf, start, now)
            except Exception as exc:  # Netz-/Antwortfehler: Status setzen, nächstes Paar
                log.warning("Bitstamp %s %s: %s", pair, tf, exc)
                out.errors[key] = type(exc).__name__
                _set_status(conn, inst_id, tf, "ERROR", f"{type(exc).__name__}", None, now)
                continue
            if not candles:
                if last is None:  # Paar existiert für diesen Zeitraum noch nicht: Cursor weiterschieben
                    nxt = start + PAGE_LIMIT * step
                    _set_status(conn, inst_id, tf, "GAP", f"cursor={int(min(nxt, now).timestamp())}", None, now)
                else:
                    out.up_to_date.append(key)
                continue
            n = insert_candles(conn, candles, now)
            out.inserted[key] = n
            _set_status(conn, inst_id, tf, "OK", None, candles[-1].close_time, now)
    return out


# ───────────────────────── Datensatz ─────────────────────────


def _dec(d: Decimal) -> str:
    s = format(d.normalize(), "f")
    return "0" if s in ("-0", "") else s


def candle_row(c: Candle) -> str:
    return "|".join((c.instrument_id, c.timeframe, str(int(c.open_time.timestamp())), _dec(c.open), _dec(c.high), _dec(c.low), _dec(c.close), _dec(c.volume)))


def dataset_hash(series: dict[str, list[Candle]]) -> str:
    """SHA-256 über die kanonischen Zeilen aller Reihen (sortiert nach Schlüssel, dann Zeit)."""
    h = hashlib.sha256()
    for key in sorted(series):
        h.update(f"#{key}\n".encode())
        for c in series[key]:
            h.update(candle_row(c).encode())
            h.update(b"\n")
    return h.hexdigest()


@dataclass
class Dataset:
    instruments: list[str]
    timeframe: str
    start: datetime
    end: datetime  # exklusiv
    as_of: datetime
    candles: dict[str, list[Candle]]  # Signal-Zeitebene je Instrument
    daily: dict[str, list[Candle]]  # Tageskerzen je Instrument und Leitinstrument
    leaders: dict[str, str]
    hash: str

    def series(self) -> dict[str, list[Candle]]:
        out = {f"{k}|{self.timeframe}": v for k, v in self.candles.items()}
        out.update({f"{k}|{REGIME_TF}": v for k, v in self.daily.items()})
        return out


def _candle(r: dict[str, Any]) -> Candle:
    return Candle(r["instrument_id"], r["timeframe"], r["open_time"], r["close_time"], r["open"], r["high"], r["low"], r["close"],
                  r["volume"], r["trades"], r["source"])


def load_range(conn: Conn, instrument_id: str, timeframe: str, start: datetime, end: datetime, as_of: datetime) -> list[Candle]:
    rows = conn.execute(
        "select * from candle where instrument_id = %s and timeframe = %s and open_time >= %s and close_time <= %s "
        "and available_at <= %s order by open_time",
        (instrument_id, timeframe, start, end, as_of),
    ).fetchall()
    return [_candle(r) for r in rows]


def leader_of(conn: Conn, instrument_id: str) -> str:
    row = conn.execute("select leader_id from instrument where id = %s", (instrument_id,)).fetchone()
    return (row["leader_id"] if row and row["leader_id"] else None) or instrument_id


def build_dataset(candles: dict[str, list[Candle]], daily: dict[str, list[Candle]], leaders: dict[str, str], timeframe: str,
                  start: datetime, end: datetime, as_of: datetime) -> Dataset:
    ds = Dataset(sorted(candles), timeframe, start, end, as_of, candles, daily, leaders, "")
    ds.hash = dataset_hash(ds.series())
    return ds


def load_dataset(conn: Conn, instruments: list[str], timeframe: str, start: datetime, end: datetime, as_of: datetime) -> Dataset:
    leaders = {i: leader_of(conn, i) for i in instruments}
    candles = {i: load_range(conn, i, timeframe, start, end, as_of) for i in instruments}
    daily_ids = sorted(set(instruments) | set(leaders.values()))
    daily = {i: (candles[i] if timeframe == REGIME_TF and i in candles else load_range(conn, i, REGIME_TF, start, end, as_of)) for i in daily_ids}
    return build_dataset(candles, daily, leaders, timeframe, start, end, as_of)


def data_start(conn: Conn, instruments: list[str], timeframe: str) -> datetime | None:
    row = conn.execute(
        "select min(open_time) as t from candle where instrument_id = any(%s) and timeframe = %s", (instruments, timeframe)
    ).fetchone()
    return row["t"] if row else None


# ───────────────────────── Holdout-Protokoll ─────────────────────────


def holdout_access_count(conn: Conn, strategy: str) -> int:
    row = conn.execute("select count(*) as n from holdout_access where strategy = %s", (strategy,)).fetchone()
    return int(row["n"]) if row else 0


def record_holdout_access(conn: Conn, strategy: str, experiment_id: int, now: datetime) -> int:
    """Protokolliert einen Zugriff; Rückgabe = laufende Nummer dieses Zugriffs (1 = erster)."""
    conn.execute("insert into holdout_access (strategy, experiment_id, accessed_at) values (%s, %s, %s)", (strategy, experiment_id, now))
    return holdout_access_count(conn, strategy)


def days(n: int) -> timedelta:
    return timedelta(days=n)
