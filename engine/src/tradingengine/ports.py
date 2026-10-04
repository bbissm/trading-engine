"""Schnittstellen zwischen Domänenlogik und Aussenwelt (Datenbank, Marktdaten)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from .core.candles import Candle
from .core.signals import FeatureSnapshot, Signal, StrategyVersion


@dataclass(frozen=True, slots=True)
class Instrument:
    id: str
    kind: str
    venue: str
    venue_symbol: str
    name: str
    base_asset: str | None
    quote_currency: str
    leader_id: str | None = None
    in_universe: bool = True
    tick_size: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Command:
    id: int
    type: str
    target: str | None
    params: dict[str, Any]
    issued_by: str


class MarketData(Protocol):
    source: str

    def fetch_closed_candles(self, instrument: Instrument, timeframe: str, now: datetime) -> list[Candle]:
        """Nur abgeschlossene Kerzen, aufsteigend sortiert."""
        ...


class Store(Protocol):
    def schema_version(self) -> int | None: ...
    def upsert_instruments(self, instruments: list[Instrument]) -> None: ...
    def universe(self) -> list[Instrument]: ...
    def insert_candles(self, candles: list[Candle], available_at: datetime) -> int:
        """Fügt neue Kerzen ein; bestehende bleiben unverändert. Rückgabe: Anzahl neuer Kerzen."""
        ...

    def load_candles(self, instrument_id: str, timeframe: str, limit: int | None = None) -> list[Candle]:
        """Aufsteigend sortiert; mit `limit` nur die jüngsten N Kerzen."""
        ...

    def last_candle_open(self, instrument_id: str, timeframe: str) -> datetime | None: ...
    def latest_snapshot_close(self, instrument_id: str, timeframe: str, regime_rule_version: str) -> datetime | None: ...
    def set_feed_status(
        self,
        feed: str,
        instrument_id: str,
        timeframe: str,
        status: str,
        detail: str | None,
        last_candle_close: datetime | None,
        ok_at: datetime | None,
    ) -> None: ...
    def ensure_strategy_version(self, version: StrategyVersion) -> None: ...
    def insert_feature_snapshots(self, snapshots: list[FeatureSnapshot]) -> int: ...
    def insert_signal(self, signal: Signal) -> bool:
        """Append-only und idempotent: False, wenn für (Version, Instrument, Zeitebene, Kerze) schon eines existiert."""
        ...

    def heartbeat(self, service: str, now: datetime, schema_version: int, detail: dict[str, Any]) -> None: ...
    def pending_commands(self) -> list[Command]: ...
    def complete_command(self, command_id: int, status: str, result: dict[str, Any], now: datetime) -> None: ...
    def audit(self, actor: str, kind: str, obj: str | None, data: dict[str, Any] | None) -> None: ...
