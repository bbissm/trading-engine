"""Postgres-Store (psycopg 3). Tabellen gehören dem Drizzle-Schema in web/src/db/schema.ts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..core.candles import Candle
from ..core.signals import FeatureSnapshot, Signal, StrategyVersion
from ..ports import Command, Instrument


class PgStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: psycopg.Connection[dict[str, Any]] | None = None

    def _c(self) -> psycopg.Connection[dict[str, Any]]:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._dsn, autocommit=True, row_factory=dict_row)
        return self._conn

    def reset(self) -> None:
        """Verbindung verwerfen (nach einem Fehler); der nächste Zugriff verbindet neu."""
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def schema_version(self) -> int | None:
        try:
            row = self._c().execute("select version from schema_meta where id = 1").fetchone()
        except psycopg.errors.UndefinedTable:
            return None
        return None if row is None else int(row["version"])

    def upsert_instruments(self, instruments: list[Instrument]) -> None:
        with self._c().cursor() as cur:
            for i in instruments:
                # in_universe wird nur beim ersten Anlegen gesetzt: spätere Freigaben/Sperren sind Bedienhandlungen.
                cur.execute(
                    """
                    insert into instrument (id, kind, venue, venue_symbol, name, base_asset, quote_currency, leader_id, in_universe, tick_size)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (id) do update set venue_symbol = excluded.venue_symbol, name = excluded.name,
                        leader_id = excluded.leader_id, tick_size = coalesce(excluded.tick_size, instrument.tick_size)
                    """,
                    (i.id, i.kind, i.venue, i.venue_symbol, i.name, i.base_asset, i.quote_currency, i.leader_id, i.in_universe, i.tick_size),
                )

    def universe(self) -> list[Instrument]:
        rows = self._c().execute(
            "select * from instrument where in_universe and status = 'ACTIVE' order by id"
        ).fetchall()
        return [
            Instrument(
                id=r["id"], kind=r["kind"], venue=r["venue"], venue_symbol=r["venue_symbol"], name=r["name"],
                base_asset=r["base_asset"], quote_currency=r["quote_currency"], leader_id=r["leader_id"],
                in_universe=r["in_universe"], tick_size=r["tick_size"],
            )
            for r in rows
        ]

    def insert_candles(self, candles: list[Candle], available_at: datetime) -> int:
        if not candles:
            return 0
        with self._c().cursor() as cur:
            # executemany nutzt den Pipeline-Modus: ein Netzwerk-Rundlauf statt einem pro Kerze
            cur.executemany(
                """
                insert into candle (instrument_id, timeframe, open_time, close_time, open, high, low, close,
                                    volume, trades, source, available_at)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict do nothing
                """,
                [
                    (c.instrument_id, c.timeframe, c.open_time, c.close_time, c.open, c.high, c.low, c.close,
                     c.volume, c.trades, c.source, available_at)
                    for c in candles
                ],
            )
            return cur.rowcount

    def load_candles(self, instrument_id: str, timeframe: str, limit: int | None = None) -> list[Candle]:
        if limit is None:
            query = "select * from candle where instrument_id = %s and timeframe = %s order by open_time"
            params: tuple[Any, ...] = (instrument_id, timeframe)
        else:
            query = (
                "select * from (select * from candle where instrument_id = %s and timeframe = %s "
                "order by open_time desc limit %s) t order by open_time"
            )
            params = (instrument_id, timeframe, limit)
        rows = self._c().execute(query, params).fetchall()
        return [
            Candle(
                instrument_id=r["instrument_id"], timeframe=r["timeframe"], open_time=r["open_time"],
                close_time=r["close_time"], open=r["open"], high=r["high"], low=r["low"], close=r["close"],
                volume=r["volume"], trades=r["trades"], source=r["source"],
            )
            for r in rows
        ]

    def last_candle_open(self, instrument_id: str, timeframe: str) -> datetime | None:
        row = self._c().execute(
            "select max(open_time) as t from candle where instrument_id = %s and timeframe = %s",
            (instrument_id, timeframe),
        ).fetchone()
        return None if row is None else row["t"]

    def latest_snapshot_close(self, instrument_id: str, timeframe: str, regime_rule_version: str) -> datetime | None:
        row = self._c().execute(
            "select max(candle_close) as t from feature_snapshot "
            "where instrument_id = %s and timeframe = %s and regime_rule_version = %s",
            (instrument_id, timeframe, regime_rule_version),
        ).fetchone()
        return None if row is None else row["t"]

    def set_feed_status(
        self,
        feed: str,
        instrument_id: str,
        timeframe: str,
        status: str,
        detail: str | None,
        last_candle_close: datetime | None,
        ok_at: datetime | None,
    ) -> None:
        self._c().execute(
            """
            insert into feed_status (feed, instrument_id, timeframe, status, detail, last_candle_close, last_ok_at, updated_at)
            values (%s, %s, %s, %s, %s, %s, %s, now())
            on conflict (feed, instrument_id, timeframe) do update set
                status = excluded.status, detail = excluded.detail,
                last_candle_close = coalesce(excluded.last_candle_close, feed_status.last_candle_close),
                last_ok_at = coalesce(excluded.last_ok_at, feed_status.last_ok_at), updated_at = now()
            """,
            (feed, instrument_id, timeframe, status, detail, last_candle_close, ok_at),
        )

    def feed_statuses(self, feed: str) -> dict[str, str]:
        rows = self._c().execute("select instrument_id, timeframe, status from feed_status where feed = %s", (feed,)).fetchall()
        return {f"{r['instrument_id']}|{r['timeframe']}": r["status"] for r in rows}

    def latest_signal_counts(self) -> dict[str, tuple[datetime, int]]:
        rows = self._c().execute(
            """
            select l.instrument_id, l.timeframe, l.last_close,
                   (select count(*) from signal s where s.instrument_id = l.instrument_id
                      and s.timeframe = l.timeframe and s.candle_close = l.last_close) as n
            from (select instrument_id, timeframe, max(close_time) as last_close from candle group by 1, 2) l
            """
        ).fetchall()
        return {f"{r['instrument_id']}|{r['timeframe']}": (r["last_close"], int(r["n"])) for r in rows}

    def ensure_strategy_version(self, version: StrategyVersion) -> None:
        self._c().execute(
            """
            insert into strategy_version (id, strategy, version, params, regime_rule_version)
            values (%s, %s, %s, %s, %s) on conflict (id) do nothing
            """,
            (version.id, version.strategy, version.version, Jsonb(version.params), version.regime_rule_version),
        )
        row = self._c().execute("select params, regime_rule_version from strategy_version where id = %s", (version.id,)).fetchone()
        assert row is not None
        if row["params"] != version.params or row["regime_rule_version"] != version.regime_rule_version:
            raise ValueError(
                f"Strategieversion {version.id} existiert mit anderen Parametern – Änderungen brauchen eine neue Version"
            )

    def insert_feature_snapshots(self, snapshots: list[FeatureSnapshot]) -> int:
        if not snapshots:
            return 0
        with self._c().cursor() as cur:
            cur.executemany(
                """
                insert into feature_snapshot (instrument_id, timeframe, candle_close, regime_rule_version, regime, features)
                values (%s, %s, %s, %s, %s, %s) on conflict do nothing
                """,
                [
                    (s.instrument_id, s.timeframe, s.candle_close, s.regime_rule_version, s.regime.value, Jsonb(s.features))
                    for s in snapshots
                ],
            )
            return cur.rowcount

    def insert_signal(self, signal: Signal) -> bool:
        d = signal.decision
        cur = self._c().execute(
            """
            insert into signal (instrument_id, timeframe, candle_close, strategy_version_id, action, regime, score,
                                entry, stop, target, max_hold_bars, valid_until, triggers, counter, data_source,
                                data_age_s, created_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (strategy_version_id, instrument_id, timeframe, candle_close) do nothing
            """,
            (signal.instrument_id, signal.timeframe, d.candle_close, signal.strategy_version_id, d.action.value,
             d.regime.value, d.score, d.entry, d.stop, d.target, d.max_hold_bars, signal.valid_until,
             Jsonb(d.triggers), Jsonb(d.counter), signal.data_source, signal.data_age_s, signal.created_at),
        )
        return cur.rowcount == 1

    def heartbeat(self, service: str, now: datetime, schema_version: int, detail: dict[str, Any]) -> None:
        self._c().execute(
            """
            insert into heartbeat (service, last_seen, schema_version, detail) values (%s, %s, %s, %s)
            on conflict (service) do update set last_seen = excluded.last_seen,
                schema_version = excluded.schema_version, detail = excluded.detail
            """,
            (service, now, schema_version, Jsonb(detail)),
        )

    def pending_commands(self) -> list[Command]:
        rows = self._c().execute(
            "select id, type, target, params, issued_by from command where status = 'PENDING' order by id limit 50"
        ).fetchall()
        return [Command(r["id"], r["type"], r["target"], r["params"] or {}, r["issued_by"]) for r in rows]

    def complete_command(self, command_id: int, status: str, result: dict[str, Any], now: datetime) -> None:
        self._c().execute(
            "update command set status = %s, result = %s, handled_at = %s where id = %s and status = 'PENDING'",
            (status, Jsonb(result), now, command_id),
        )

    def audit(self, actor: str, kind: str, obj: str | None, data: dict[str, Any] | None) -> None:
        self._c().execute(
            "insert into audit_event (actor, kind, object, data) values (%s, %s, %s, %s)",
            (actor, kind, obj, None if data is None else Jsonb(data)),
        )
