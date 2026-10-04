"""Guardian (T9) und Tageskurse gegen echtes Postgres: Verlustgrenzen mit Zustandswechsel, Vorwarnung, FX-Effekt,
Datenströme, Autopilot-Fehler; FX-Nachladen ohne erfundene Wochenendkurse."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import httpx
import psycopg
import pytest
from notify_fakes import DSN, fresh_db
from psycopg.rows import dict_row

from tradingengine.adapters.pg_paper import PaperRepo
from tradingengine.adapters.pg_store import PgStore
from tradingengine.core.candles import Candle
from tradingengine.core.strategies import ACTIVE
from tradingengine.ports import Command, Instrument
from tradingengine.services import guardian, paper
from tradingengine.services.fx import fx_rate_for, refresh_fx

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not DSN, reason="TE_TEST_DATABASE_URL nicht gesetzt")]

T0 = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)  # Montag, 01:00 Zürich
H4 = timedelta(hours=4)
MIN = timedelta(minutes=1)
AAA, BBB = "TEST:AAA/USD", "TEST:BBB/USD"
S2 = "s2-volume-breakout@1"
FEEDS = {f"{AAA}|4h": "OK", f"{BBB}|4h": "OK"}
Conn = psycopg.Connection[dict[str, Any]]


def c4(instrument_id: str, start: datetime, o: str, h: str, low: str, c: str) -> Candle:
    return Candle(instrument_id, "4h", start, start + H4, D(o), D(h), D(low), D(c), D("100000"), 10, "test")


class Env:
    def __init__(self) -> None:
        fresh_db().close()
        assert DSN
        self.store = PgStore(DSN)
        self.conn = self.store.connection()
        self.repo = PaperRepo(self.conn)
        mk = lambda i: Instrument(i, "CRYPTO_SPOT", "TEST", i[5:].replace("/", ""), i, i[5:8], "USD", None, True, D("0.01"), D("0.0001"), D("5"))  # noqa: E731
        self.store.upsert_instruments([mk(AAA), mk(BBB)])
        for s in ACTIVE:
            self.store.ensure_strategy_version(s.version)
        self.store.insert_candles([c4(i, T0 - H4 * (40 - n), "100", "100.5", "99.5", "100") for i in (AAA, BBB) for n in range(40)], T0)

    def sql(self, q: str, p: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cur = self.conn.execute(q, p)  # type: ignore[arg-type]
        return cur.fetchall() if cur.description else []

    def command(self, type_: str, target: str | None, now: datetime, **params: Any) -> tuple[str, dict[str, Any]]:
        return paper.handle_command(self.repo, Command(1, type_, target, params, "user:test"), now)

    def signal(self, instrument_id: str, created: datetime) -> None:
        from psycopg.types.json import Jsonb
        cc = created.replace(minute=0, second=0, microsecond=0)
        self.sql(
            """
            insert into signal (instrument_id, timeframe, candle_close, strategy_version_id, action, regime, score, entry, stop,
                                max_hold_bars, valid_until, triggers, counter, data_source, data_age_s, created_at, ref_level)
            values (%s, '4h', %s, %s, 'BUY', 'UP', 70, 100, 96, 30, %s, %s, %s, 'test', 60, %s, 99)
            """,
            (instrument_id, cc, S2, cc + H4, Jsonb(["test"]), Jsonb([]), created),
        )

    def snapshot(self, account_id: str, ts: datetime, equity: str) -> None:
        ep = self.sql("select id from episode where account_id = %s and ended_at is null", (account_id,))[0]["id"]
        self.sql("insert into equity_snapshot (account_id, episode_id, ts, equity, cash, invested) values (%s, %s, %s, %s, %s, 0)",
                 (account_id, ep, ts, D(equity), D(equity)))

    def state(self, account_id: str) -> str:
        return str(self.sql("select state from autopilot where account_id = %s", (account_id,))[0]["state"])

    def alerts(self, like: str = "%") -> list[dict[str, Any]]:
        return self.sql("select * from alert where dedup_key like %s order by id", (like,))


@pytest.fixture()
def env() -> Iterator[Env]:
    e = Env()
    yield e
    e.store.reset()


def _open_position(env: Env) -> str:
    """Konto aktiv, eine Position AAA mit Schutz-Stop 96, dazu eine offene Einstiegsorder BBB."""
    status, result = env.command("PAPER_CREATE", None, T0 + MIN, name="Wächter", start_cash="10000")
    account_id = str(result["account_id"])
    assert env.command("PAPER_START", account_id, T0 + MIN)[0] == "DONE"
    env.signal(AAA, T0 + timedelta(seconds=90))
    paper.run_accounts(env.repo, FEEDS, T0 + 2 * MIN)
    env.store.insert_candles([c4(AAA, T0, "100.4", "101", "99.2", "100.8"), c4(BBB, T0, "100", "100.5", "99.5", "100")], T0 + H4)
    paper.run_accounts(env.repo, FEEDS, T0 + H4 + MIN)
    env.signal(BBB, T0 + H4 + timedelta(seconds=90))
    paper.run_accounts(env.repo, FEEDS, T0 + H4 + 2 * MIN)
    assert len(env.sql("select 1 from trade where status = 'OPEN'")) == 1
    assert len(env.sql("select 1 from trade_order where role = 'ENTRY' and state = 'ACCEPTED'")) == 1
    return account_id


def test_t9_daily_loss_breach_pauses_entries_and_keeps_protection(env: Env) -> None:
    account_id = _open_position(env)
    now = T0 + H4 + 3 * MIN
    env.snapshot(account_id, now - MIN, "9800")  # Tagesverlust 2 % > 1.5 % (realisiert + unrealisiert + Gebühren)
    out = guardian.run_guardian(env.conn, now)
    assert out[account_id]["breached"] == ["daily"] and out[account_id]["cancelled_entry_orders"] == 1
    assert env.state(account_id) == "ENTRIES_PAUSED"
    assert "Tagesverlust" in env.sql("select reason from autopilot")[0]["reason"]
    assert env.sql("select state from trade_order where role = 'ENTRY' and instrument_id = %s", (BBB,))[0]["state"] == "CANCELED"
    assert not env.sql("select 1 from reservation")
    assert env.sql("select state from trade_order where role = 'PROTECT'")[0]["state"] == "ACCEPTED"
    crit = env.alerts(f"guardian:{account_id}:daily:breach:%")
    assert len(crit) == 1 and crit[0]["level"] == "CRITICAL" and crit[0]["mode"] == "PAPER"
    assert crit[0]["title"] == "Tagesverlust-Grenze erreicht – Einstiege pausiert"
    assert "2.00%" in crit[0]["body"] and "Limit 1.50%" in crit[0]["body"] and "Ausstiege laufen weiter" in crit[0]["body"]
    assert crit[0]["data"]["account_id"] == account_id
    assert env.sql("select kind from audit_event where kind = 'guardian.entries_paused'")

    # weitere Läufe: keine Flut, kein zweiter Zustandswechsel
    guardian.run_guardian(env.conn, now + MIN)
    assert len(env.alerts(f"guardian:{account_id}:daily:breach:%")) == 1 and env.alerts("guardian:%breach%")[0]["occurrences"] == 2

    # Exits bleiben möglich: der Stop löst in der nächsten Kerze aus
    env.store.insert_candles([c4(AAA, T0 + H4, "100", "100.5", "95", "95.5"), c4(BBB, T0 + H4, "100", "100.5", "99.5", "100")], T0 + 2 * H4)
    paper.run_accounts(env.repo, FEEDS, T0 + 2 * H4 + MIN)
    assert env.sql("select status, exit_reason from trade")[0] == {"status": "CLOSED", "exit_reason": "Stop"}

    # Tageswechsel setzt den Tageszähler zurück, die Meldung bleibt bis zur Bestätigung
    tomorrow = datetime(2026, 3, 3, 6, 0, tzinfo=UTC)
    last = env.sql("select equity from equity_snapshot order by ts desc limit 1")[0]["equity"]
    env.snapshot(account_id, tomorrow - MIN, str(last - 50))  # 0.5 % unter dem Stand zum Tageswechsel
    out = guardian.run_guardian(env.conn, tomorrow)
    assert out[account_id]["breached"] == []
    assert not env.alerts(f"guardian:{account_id}:daily:breach:2026-03-03")
    assert env.alerts(f"guardian:{account_id}:daily:breach:2026-03-02")[0]["status"] == "OPEN"


def test_t9_pure_fx_move_counts_toward_the_limit(env: Env) -> None:
    status, result = env.command("PAPER_CREATE", None, T0 + MIN, name="FX", start_cash="10000")
    account_id = str(result["account_id"])
    env.command("PAPER_START", account_id, T0 + MIN)
    env.sql("insert into fx_rate (base, quote, date, rate, source) values ('USD','CHF','2026-02-27',0.9000,'ecb-frankfurter'), "
            "('USD','CHF','2026-03-02',0.8800,'ecb-frankfurter')")
    now = datetime(2026, 3, 2, 16, 0, tzinfo=UTC)
    env.snapshot(account_id, now - MIN, "10000")  # in USD unverändert
    out = guardian.run_guardian(env.conn, now)
    assert out[account_id]["unit"] == "CHF" and out[account_id]["breached"] == ["daily"]
    body = env.alerts("%daily:breach%")[0]["body"]
    assert "CHF" in body and "FX-Effekt -200.00 CHF" in body and "Handelsergebnis +0.00 USD" in body
    assert env.state(account_id) == "ENTRIES_PAUSED"


def test_warning_at_70_percent_resolves_when_recovered(env: Env) -> None:
    status, result = env.command("PAPER_CREATE", None, T0 + MIN, name="Warn", start_cash="10000")
    account_id = str(result["account_id"])
    env.command("PAPER_START", account_id, T0 + MIN)
    env.snapshot(account_id, T0 + 2 * MIN, "9890")  # 1.1 % = 73 % der Grenze
    out = guardian.run_guardian(env.conn, T0 + 3 * MIN)
    assert out[account_id]["warned"] == ["daily"] and out[account_id]["breached"] == []
    warn = env.alerts(f"guardian:{account_id}:daily:warn:%")
    assert len(warn) == 1 and warn[0]["level"] == "WARNING" and warn[0]["status"] == "OPEN"
    assert env.state(account_id) == "ACTIVE"
    env.snapshot(account_id, T0 + 4 * MIN, "9990")
    guardian.run_guardian(env.conn, T0 + 5 * MIN)
    assert env.alerts(f"guardian:{account_id}:daily:warn:%")[0]["status"] == "RESOLVED"


def test_drawdown_emergency_holds_with_protection(env: Env) -> None:
    status, result = env.command("PAPER_CREATE", None, T0 + MIN, name="DD", start_cash="10000")
    account_id = str(result["account_id"])
    env.command("PAPER_START", account_id, T0 + MIN)
    env.snapshot(account_id, T0 - timedelta(days=3), "10000")
    env.snapshot(account_id, T0 + 2 * MIN, "8700")  # 13 %
    out = guardian.run_guardian(env.conn, T0 + 3 * MIN)
    assert out[account_id]["emergency"] and set(out[account_id]["breached"]) == {"daily", "weekly", "drawdown"}
    emergency = env.alerts("%dd_emergency%")[0]
    assert emergency["level"] == "CRITICAL" and "halten mit Schutz" in emergency["body"]
    assert env.state(account_id) == "ENTRIES_PAUSED"


def test_feed_stale_warning_and_auto_resolve(env: Env) -> None:
    now = datetime(2026, 3, 2, 12, 20, tzinfo=UTC)
    env.sql("insert into feed_status (feed, instrument_id, timeframe, status, detail, last_candle_close, last_ok_at) values "
            "('kraken', %s, '4h', 'STALE', 'Letzte Kerze 04:00', %s, %s), ('kraken', %s, '4h', 'ERROR', 'Timeout', %s, %s)",
            (AAA, datetime(2026, 3, 2, 8, 0, tzinfo=UTC), datetime(2026, 3, 2, 8, 1, tzinfo=UTC),
             BBB, datetime(2026, 3, 2, 8, 0, tzinfo=UTC), datetime(2026, 3, 2, 12, 15, tzinfo=UTC)))
    out = guardian.run_guardian(env.conn, now)
    assert out["_feeds_stale"] == [f"feed:kraken:{AAA}|4h"]  # BBB erst seit 5 min nicht OK
    warn = env.alerts("feed:%")
    assert len(warn) == 1 and (warn[0]["level"], warn[0]["mode"]) == ("WARNING", "SYSTEM")
    guardian.run_guardian(env.conn, now + MIN)
    assert len(env.alerts("feed:%")) == 1
    env.sql("update feed_status set status = 'OK' where instrument_id = %s", (AAA,))
    guardian.run_guardian(env.conn, now + 2 * MIN)
    assert env.alerts(f"feed:kraken:{AAA}|4h")[0]["status"] == "RESOLVED"


def test_autopilot_error_raises_critical_and_resolves(env: Env) -> None:
    status, result = env.command("PAPER_CREATE", None, T0 + MIN, name="Err", start_cash="10000")
    account_id = str(result["account_id"])
    env.repo.set_autopilot(account_id, "ERROR", "Abweichung im Kontobuch", T0 + MIN)
    guardian.run_guardian(env.conn, T0 + 2 * MIN)
    crit = env.alerts(f"guardian:{account_id}:error")
    assert crit[0]["level"] == "CRITICAL" and "Abweichung im Kontobuch" in crit[0]["body"]
    env.repo.set_autopilot(account_id, "READY", "repariert", T0 + 3 * MIN)
    guardian.run_guardian(env.conn, T0 + 4 * MIN)
    assert env.alerts(f"guardian:{account_id}:error")[0]["status"] == "RESOLVED"


# --- Tageskurse ------------------------------------------------------------------------------------


class Frankfurter:
    def __init__(self, rates: dict[str, str]) -> None:
        self.rates = rates
        self.urls: list[str] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.urls.append(str(req.url))
        span = str(req.url).split("/v1/")[1].split("?")[0]
        start, end = span.split("..")
        data = {d: {"CHF": float(r)} for d, r in self.rates.items() if start <= d <= end}
        return httpx.Response(200, json={"amount": 1.0, "base": "USD", "start_date": start, "end_date": end, "rates": data})


@pytest.fixture()
def conn() -> Iterator[Conn]:
    fresh_db().close()
    assert DSN
    c = psycopg.connect(DSN, autocommit=True, row_factory=dict_row)
    yield c
    c.close()


def test_fx_backfill_without_inventing_weekend_rates(conn: Conn) -> None:
    api = Frankfurter({"2025-09-01": "0.8012", "2026-02-27": "0.9000", "2026-03-02": "0.8800"})
    client = httpx.Client(transport=httpx.MockTransport(api.handler))
    now = datetime(2026, 3, 2, 17, 0, tzinfo=UTC)
    assert refresh_fx(conn, now, client) == {"USD/CHF": 3}
    assert len(api.urls) == 1 and "/v1/2025-01-26..2026-03-02" in api.urls[0] and "base=USD" in api.urls[0] and "symbols=CHF" in api.urls[0]
    dates = [r["date"] for r in conn.execute("select date from fx_rate order by date").fetchall()]
    assert dates == ["2025-09-01", "2026-02-27", "2026-03-02"]  # kein Samstag/Sonntag erfunden
    assert fx_rate_for(conn, "USD", "CHF", date(2026, 3, 1)) == (D("0.9000"), date(2026, 2, 27))
    assert fx_rate_for(conn, "USD", "CHF", date(2024, 1, 1)) is None
    assert fx_rate_for(conn, "CHF", "CHF", date(2026, 3, 1)) == (D(1), date(2026, 3, 1))
    assert source_of(conn) == {"ecb-frankfurter"}

    # heutiges Fixing vorhanden → kein weiterer Abruf
    refresh_fx(conn, now + timedelta(hours=5), client)
    assert len(api.urls) == 1
    # Folgetag: höchstens alle 3 h, ab dem letzten bekannten Datum
    next_day = datetime(2026, 3, 3, 8, 0, tzinfo=UTC)
    refresh_fx(conn, next_day, client)
    refresh_fx(conn, next_day + timedelta(hours=1), client)
    assert len(api.urls) == 2 and "/v1/2026-03-02..2026-03-03" in api.urls[1]
    refresh_fx(conn, next_day + timedelta(hours=4), client)
    assert len(api.urls) == 3


def source_of(conn: Conn) -> set[str]:
    return {r["source"] for r in conn.execute("select source from fx_rate").fetchall()}
