"""Paper-Konto mit IBKR-Kostenmodell gegen echtes Postgres: Sitzungsschritte, ganze Aktien, Lücke über Nacht."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tradingengine.adapters.pg_paper import PaperRepo
from tradingengine.adapters.pg_store import PgStore
from tradingengine.calendar_us import default_calendar
from tradingengine.core.candles import Candle
from tradingengine.core.costs_stocks import IBKR_FIXED_US
from tradingengine.core.risk import RiskPolicy
from tradingengine.core.strategies import ACTIVE
from tradingengine.ports import Instrument
from tradingengine.services import stocks
from tradingengine.universe_stocks import stock_instruments

DSN = os.environ.get("TE_TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[2] / "web" / "drizzle"
pytestmark = [pytest.mark.pg, pytest.mark.skipif(not DSN, reason="TE_TEST_DATABASE_URL nicht gesetzt")]

CAL = default_calendar(datetime(2026, 10, 4, tzinfo=UTC))
AAPL, SPY = "ALPACA:AAPL", "ALPACA:SPY"
FRI, MON, TUE = date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)
S2 = "s2-volume-breakout@1"
FEEDS = {f"{AAPL}|1d": "OK", f"{SPY}|1d": "OK"}


def close_of(day: date) -> datetime:
    s = CAL.session(day)
    assert s is not None
    return s.close


def daily(iid: str, day: date, o: str, h: str, low: str, c: str, volume: str = "5000000") -> Candle:
    s = CAL.session(day)
    assert s is not None
    return Candle(iid, "1d", s.open, s.close, D(o), D(h), D(low), D(c), D(volume), 100, "test")


class Env:
    def __init__(self, store: PgStore) -> None:
        self.store = store
        self.repo = PaperRepo(store.connection())

    def sql(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cur = self.store.connection().execute(query, params)  # type: ignore[arg-type]
        return cur.fetchall() if cur.description else []

    def tick(self, now: datetime) -> dict[str, Any]:
        row = next(r for r in self.repo.accounts() if stocks.is_stock_account(r))
        return stocks.run_stock_account(self.repo, row, FEEDS, now, CAL)


@pytest.fixture()
def env() -> Iterator[Env]:
    assert DSN
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("drop schema if exists drizzle cascade; drop schema public cascade; create schema public;")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            for statement in path.read_text(encoding="utf-8").split("--> statement-breakpoint"):
                if statement.strip():
                    conn.execute(statement)  # type: ignore[arg-type]
    store = PgStore(DSN)
    btc = Instrument("KRAKEN:BTC/USD", "CRYPTO_SPOT", "KRAKEN", "XBTUSD", "BTC", "BTC", "USD", None, True, D("0.1"), D("0.00005"), D("0.5"))
    store.upsert_instruments([i for i in stock_instruments(True) if i.id in (AAPL, SPY)] + [btc])
    for s in ACTIVE:
        store.ensure_strategy_version(s.version)
    history = [daily(i, s.day, "100", "100.5", "99.5", "100") for i in (AAPL, SPY) for s in CAL.sessions_between(date(2026, 8, 1), FRI)]
    # Krypto-Tageskerzen schliessen um 00:00 UTC: sie dürfen für Aktienkonten keinen Schritt auslösen
    crypto = [Candle(btc.id, "1d", datetime(2026, 10, d, tzinfo=UTC), datetime(2026, 10, d + 1, tzinfo=UTC), D(1), D(1), D(1), D(1), D(1), 1, "t")
              for d in (2, 3, 4, 5)]
    store.insert_candles(history + crypto, close_of(FRI))
    repo = PaperRepo(store.connection())
    now = close_of(FRI) + timedelta(minutes=21)
    repo.create_account("paper-aktien", "Aktien", "USD", D(10_000), RiskPolicy(), [s.version.id for s in ACTIVE], IBKR_FIXED_US.version,
                        stocks.STOCK_SIM.version, now - timedelta(minutes=1), close_of(FRI))
    repo.set_autopilot("paper-aktien", "ACTIVE", "Test", now)
    e = Env(store)
    yield e
    store.reset()


def _signal(env: Env, created: datetime) -> None:
    env.sql(
        """
        insert into signal (instrument_id, timeframe, candle_close, strategy_version_id, action, regime, score, entry, stop, target,
                            max_hold_bars, valid_until, triggers, counter, data_source, data_age_s, created_at, ref_level)
        values (%s, '1d', %s, %s, 'BUY', 'UP', 70, 100, 96, null, 30, %s, %s, %s, 'alpaca-sip', 1500, %s, 99)
        """,
        (AAPL, close_of(FRI), S2, close_of(MON), Jsonb(["test"]), Jsonb([]), created),
    )


def test_stock_paper_account_trades_whole_shares_over_sessions(env: Env) -> None:
    _signal(env, close_of(FRI) + timedelta(minutes=22))
    out = env.tick(close_of(FRI) + timedelta(minutes=23))
    assert out["orders"] == 1
    order = env.sql("select * from trade_order")[0]
    assert order["qty"] == order["qty"].to_integral_value() and order["timeframe"] == "1d"
    res = env.sql("select * from reservation")[0]
    assert res["risk"] <= D(50)  # 0.5 % von 10 000, inkl. Mindestgebühren

    # Wochenende: Krypto-Schlüsse um 00:00 UTC lösen keinen Schritt aus
    assert env.tick(datetime(2026, 10, 4, 12, tzinfo=UTC))["steps"] == 0

    # Montag: Fill zum Limit in ganzen Stücken mit IBKR-Gebühr
    env.store.insert_candles([daily(AAPL, MON, "100.8", "101.2", "99.5", "100.5"), daily(SPY, MON, "100", "100.5", "99.5", "100")], close_of(MON))
    assert env.tick(close_of(MON) + timedelta(minutes=21))["steps"] == 1
    fill = env.sql("select * from fill")[0]
    assert fill["price"] == D(100) and fill["qty"] == order["qty"] and fill["fee"] == IBKR_FIXED_US.order_fee(order["qty"], D(100), False)
    assert env.sql("select * from trade")[0]["status"] == "OPEN"
    snap = env.sql("select * from equity_snapshot order by ts desc limit 1")[0]
    assert snap["ts"] == close_of(MON)

    # Dienstag: Lücke unter den Stop → Ausführung zur Eröffnung
    env.store.insert_candles([daily(AAPL, TUE, "94.2", "95", "92", "93"), daily(SPY, TUE, "100", "100.5", "99.5", "100")], close_of(TUE))
    env.tick(close_of(TUE) + timedelta(minutes=21))
    trade = env.sql("select * from trade")[0]
    exit_fill = env.sql("select * from fill order by time desc limit 1")[0]
    assert trade["status"] == "CLOSED" and trade["exit_reason"] == "Stop"
    assert abs(exit_fill["price"] - D("94.2") * (1 - D("0.0005"))) < D("0.000001")
    ep = env.sql("select * from episode")[0]
    assert ep["sim_through"] == close_of(TUE)


def test_missing_candle_of_involved_stock_delays_the_step(env: Env) -> None:
    _signal(env, close_of(FRI) + timedelta(minutes=22))
    env.tick(close_of(FRI) + timedelta(minutes=23))
    env.store.insert_candles([daily(SPY, MON, "100", "100.5", "99.5", "100")], close_of(MON))  # AAPL fehlt noch
    assert env.tick(close_of(MON) + timedelta(minutes=21))["steps"] == 0
    env.store.insert_candles([daily(AAPL, MON, "100.8", "101.2", "99.5", "100.5")], close_of(MON))
    assert env.tick(close_of(MON) + timedelta(minutes=30))["steps"] == 1
    assert len(env.sql("select 1 from fill")) == 1
