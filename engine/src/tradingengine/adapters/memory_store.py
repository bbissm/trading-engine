"""In-Memory-Store für Tests. Bildet die Eindeutigkeits- und Append-only-Regeln der Datenbank nach."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..core.candles import Candle
from ..core.signals import FeatureSnapshot, Signal, StrategyVersion
from ..ports import Command, Instrument


class MemoryStore:
    def __init__(self, schema_version: int | None = None) -> None:
        self._schema_version = schema_version
        self.instruments: dict[str, Instrument] = {}
        self.candles: dict[tuple[str, str, datetime], Candle] = {}
        self.feed: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.strategy_versions: dict[str, StrategyVersion] = {}
        self.snapshots: dict[tuple[str, str, datetime, str], FeatureSnapshot] = {}
        self.signals: dict[tuple[str, str, str, datetime], Signal] = {}
        self.heartbeats: dict[str, dict[str, Any]] = {}
        self.commands: list[Command] = []
        self.command_results: dict[int, dict[str, Any]] = {}
        self.audits: list[dict[str, Any]] = []

    def schema_version(self) -> int | None:
        return self._schema_version

    def upsert_instruments(self, instruments: list[Instrument]) -> None:
        for inst in instruments:
            self.instruments[inst.id] = inst

    def universe(self) -> list[Instrument]:
        return [i for i in self.instruments.values() if i.in_universe]

    def insert_candles(self, candles: list[Candle], available_at: datetime) -> int:
        new = 0
        for c in candles:
            key = (c.instrument_id, c.timeframe, c.open_time)
            if key not in self.candles:
                self.candles[key] = c
                new += 1
        return new

    def load_candles(self, instrument_id: str, timeframe: str, limit: int | None = None) -> list[Candle]:
        rows = [c for (i, tf, _), c in self.candles.items() if i == instrument_id and tf == timeframe]
        rows.sort(key=lambda c: c.open_time)
        return rows if limit is None else rows[-limit:]

    def last_candle_open(self, instrument_id: str, timeframe: str) -> datetime | None:
        rows = self.load_candles(instrument_id, timeframe, 1)
        return rows[-1].open_time if rows else None

    def latest_snapshot_close(self, instrument_id: str, timeframe: str, regime_rule_version: str) -> datetime | None:
        times = [
            k[2] for k in self.snapshots if k[0] == instrument_id and k[1] == timeframe and k[3] == regime_rule_version
        ]
        return max(times) if times else None

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
        self.feed[(feed, instrument_id, timeframe)] = {
            "status": status, "detail": detail, "last_candle_close": last_candle_close, "ok_at": ok_at,
        }

    def ensure_strategy_version(self, version: StrategyVersion) -> None:
        existing = self.strategy_versions.get(version.id)
        if existing is not None and existing != version:
            raise ValueError(f"Strategieversion {version.id} existiert mit anderen Parametern")
        self.strategy_versions[version.id] = version

    def insert_feature_snapshots(self, snapshots: list[FeatureSnapshot]) -> int:
        new = 0
        for s in snapshots:
            key = (s.instrument_id, s.timeframe, s.candle_close, s.regime_rule_version)
            if key not in self.snapshots:
                self.snapshots[key] = s
                new += 1
        return new

    def insert_signal(self, signal: Signal) -> bool:
        key = (signal.strategy_version_id, signal.instrument_id, signal.timeframe, signal.decision.candle_close)
        if key in self.signals:
            return False
        self.signals[key] = signal
        return True

    def heartbeat(self, service: str, now: datetime, schema_version: int, detail: dict[str, Any]) -> None:
        self.heartbeats[service] = {"last_seen": now, "schema_version": schema_version, "detail": detail}

    def pending_commands(self) -> list[Command]:
        return [c for c in self.commands if c.id not in self.command_results]

    def complete_command(self, command_id: int, status: str, result: dict[str, Any], now: datetime) -> None:
        self.command_results[command_id] = {"status": status, "result": result, "handled_at": now}

    def audit(self, actor: str, kind: str, obj: str | None, data: dict[str, Any] | None) -> None:
        self.audits.append({"actor": actor, "kind": kind, "object": obj, "data": data})
