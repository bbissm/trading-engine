"""US-Aktien/ETFs: Alpaca-Tageskerzen, Börsenkalender (T22), Feed-Qualität, Signalgültigkeit, IBKR-Kosten,
Positionsgrösse in ganzen Stücken und Simulation über Sitzungslücken."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from helpers import random_walk

from tradingengine.adapters.alpaca_data import AlpacaCredentials, AlpacaData, parse_bars, to_candles
from tradingengine.adapters.memory_store import MemoryStore
from tradingengine.calendar_us import NEW_YORK, UsCalendar, default_calendar, make_session, nyse_holidays, rule_sessions
from tradingengine.core.account import Account
from tradingengine.core.candles import Candle
from tradingengine.core.costs import KRAKEN_SPOT_TIER1
from tradingengine.core.costs_stocks import IBKR_FIXED_US
from tradingengine.core.execution import Mode, Order, OrderRole, OrderState, OrderType, Side, transition
from tradingengine.core.paper import PaperState, RiskInputs, SignalIn, step_candle, submit_entries
from tradingengine.core.regime import Regime
from tradingengine.core.risk import RiskPolicy
from tradingengine.core.simulator import simulate
from tradingengine.core.sizing import InstrumentSpec, order_risk, size_position
from tradingengine.core.strategies import ACTIVE
from tradingengine.services import signals, stocks
from tradingengine.universe_stocks import LEADER_ID, SYMBOLS, stock_instruments

FIXTURES = Path(__file__).parent / "fixtures"
ZURICH = ZoneInfo("Europe/Zurich")
KEY_ID, SECRET = "PKTESTKEYID0001", "s3cr3t-test-value-never-logged"
CAL = default_calendar(datetime(2026, 10, 4, tzinfo=UTC))
AAPL, SPY = "ALPACA:AAPL", "ALPACA:SPY"


def utc(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def close_of(day: date) -> datetime:
    s = CAL.session(day)
    assert s is not None
    return s.close


# --- Kalender (T22) -------------------------------------------------------------------------------------


def test_t22_holidays_half_days_and_special_closures() -> None:
    assert not CAL.is_session(date(2026, 4, 3))  # Karfreitag
    assert not CAL.is_session(date(2026, 7, 3))  # 4. Juli fällt auf Samstag → Freitag geschlossen
    assert not CAL.is_session(date(2026, 11, 26))  # Thanksgiving
    assert not CAL.is_session(date(2025, 1, 9))  # Staatstrauer Carter
    assert not CAL.is_session(date(2022, 6, 20))  # Juneteenth (Sonntag) → Montag
    assert CAL.is_session(date(2021, 12, 31))  # Neujahr 2022 auf Samstag: Freitag davor bleibt offen
    assert not CAL.is_session(date(2026, 10, 3)) and not CAL.is_session(date(2026, 10, 4))  # Wochenende
    half = CAL.session(date(2026, 11, 27))
    assert half is not None and half.early_close and half.close.astimezone(NEW_YORK).hour == 13
    xmas_eve = CAL.session(date(2026, 12, 24))
    assert xmas_eve is not None and xmas_eve.early_close
    assert not CAL.session(date(2026, 12, 23)).early_close  # type: ignore[union-attr]
    assert len(nyse_holidays(2026)) == 10
    # Erste Sitzung der Alpaca-Historie
    assert rule_sessions(date(2016, 1, 1), date(2016, 1, 8))[0].day == date(2016, 1, 4)


def test_t22_dst_weeks_differ_between_us_and_switzerland() -> None:
    # USA wechselt am 8.3.2026, die Schweiz erst am 29.3.2026: in den Wochen dazwischen schliesst New York um 21:00 Zürich.
    assert close_of(date(2026, 3, 6)) == utc(2026, 3, 6, 21)
    assert close_of(date(2026, 3, 9)) == utc(2026, 3, 9, 20)
    assert close_of(date(2026, 3, 9)).astimezone(ZURICH).hour == 21
    assert close_of(date(2026, 3, 30)).astimezone(ZURICH).hour == 22
    assert CAL.session(date(2026, 3, 9)).open == utc(2026, 3, 9, 13, 30)  # type: ignore[union-attr]
    # Herbst: Schweiz am 25.10., USA am 1.11.2026
    assert close_of(date(2026, 10, 30)) == utc(2026, 10, 30, 20) and close_of(date(2026, 10, 30)).astimezone(ZURICH).hour == 21
    assert close_of(date(2026, 11, 2)) == utc(2026, 11, 2, 21) and close_of(date(2026, 11, 2)).astimezone(ZURICH).hour == 22
    assert not CAL.in_session(utc(2026, 3, 9, 13, 29)) and CAL.in_session(utc(2026, 3, 9, 13, 30))


def test_alpaca_calendar_overrides_rules() -> None:
    rows = json.loads((FIXTURES / "alpaca_calendar_thanksgiving_2026.json").read_text())
    alpaca = UsCalendar.from_alpaca(rows, date(2026, 11, 23), date(2026, 11, 30))
    assert [s.day for s in alpaca.sessions_between(date(2026, 11, 23), date(2026, 11, 30))] == [
        s.day for s in CAL.sessions_between(date(2026, 11, 23), date(2026, 11, 30))
    ]
    assert alpaca.session(date(2026, 11, 27)) == CAL.session(date(2026, 11, 27))
    # ausserordentliche Schliessung, die die Regeln nicht kennen: der Alpaca-Kalender gewinnt
    closed = UsCalendar.from_alpaca([r for r in rows if r["date"] != "2026-11-24"], date(2026, 11, 23), date(2026, 11, 30))
    merged = CAL.overlay(closed)
    assert not merged.is_session(date(2026, 11, 24)) and merged.is_session(date(2026, 12, 1)) and merged.is_session(date(2026, 11, 20))
    assert merged.next_session(merged.session(date(2026, 11, 23))).day == date(2026, 11, 25)  # type: ignore[arg-type, union-attr]


# --- Universum ---------------------------------------------------------------------------------------------


def test_universe_only_with_keys() -> None:
    assert stock_instruments(False) == []
    assert AlpacaCredentials.from_env({}) is None
    assert AlpacaCredentials.from_env({"ALPACA_API_KEY_ID": KEY_ID, "ALPACA_API_SECRET_KEY": ""}) is None
    insts = stock_instruments(True, {"SPY": "NYSEARCA", "AAPL": "NASDAQ"})
    assert len(insts) == 20 and [i.venue_symbol for i in insts] == SYMBOLS
    by_id = {i.id: i for i in insts}
    assert by_id[SPY].kind == "ETF" and by_id[SPY].venue == "NYSEARCA" and by_id[SPY].leader_id is None
    assert by_id[AAPL].kind == "STOCK" and by_id[AAPL].leader_id == LEADER_ID and by_id["ALPACA:MSFT"].venue == "US"
    assert all(i.min_qty == 1 and i.tick_size == D("0.01") and i.quote_currency == "USD" for i in insts)


# --- Adapter und Abruf -------------------------------------------------------------------------------------


class FakeAlpaca:
    """httpx.MockTransport: zeichnet Anfragen auf und liefert Seiten bzw. Kalenderzeilen im dokumentierten Format."""

    def __init__(self, pages: list[dict[str, Any]] | Callable[[httpx.Request], dict[str, Any]]) -> None:
        self.pages = pages
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v2/calendar":
            start, end = (date.fromisoformat(request.url.params[k]) for k in ("start", "end"))
            rows = [
                {"date": s.day.isoformat(), "open": s.open.astimezone(NEW_YORK).strftime("%H:%M"), "close": s.close.astimezone(NEW_YORK).strftime("%H:%M"),
                 "session_open": "0400", "session_close": "2000", "settlement_date": s.day.isoformat()}
                for s in rule_sessions(start, end)
            ]
            return httpx.Response(200, json=rows)
        if request.url.path == "/v2/stocks/bars":
            if callable(self.pages):
                return httpx.Response(200, json=self.pages(request))
            idx = 0 if "page_token" not in request.url.params else 1
            return httpx.Response(200, json=self.pages[idx])
        return httpx.Response(404, json={"message": "not found"})

    @property
    def bar_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/v2/stocks/bars"]


def _data(fake: FakeAlpaca) -> AlpacaData:
    return AlpacaData(AlpacaCredentials(KEY_ID, SECRET), client=httpx.Client(transport=httpx.MockTransport(fake)))


def _store(symbols: tuple[str, ...] = ("AAPL", "SPY")) -> MemoryStore:
    store = MemoryStore(schema_version=3)
    store.upsert_instruments([i for i in stock_instruments(True) if i.venue_symbol in symbols])
    return store


def _fixture_pages() -> list[dict[str, Any]]:
    return [json.loads((FIXTURES / f"alpaca_bars_page{n}.json").read_text()) for n in (1, 2)]


def test_bars_parse_decimal_and_ny_trading_day() -> None:
    rows, token = parse_bars(json.loads((FIXTURES / "alpaca_bars_page1.json").read_text(), parse_float=D))
    assert token and len(rows) == 2 and rows[0].day == date(2026, 9, 28) and rows[0].open == D("254.12")
    assert rows[0].trades == 512345 and rows[0].volume == D(41234567)


def test_backfill_paginates_multi_symbol_sends_params_and_headers_without_logging_secret(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    store, fake = _store(), FakeAlpaca(_fixture_pages())
    now = close_of(date(2026, 10, 2)) + timedelta(minutes=25)
    result, _ = stocks.sync_stocks(store, _data(fake), now)

    assert len(fake.bar_requests) == 2  # zwei Seiten
    first, second = fake.bar_requests
    p = first.url.params
    assert sorted(p["symbols"].split(",")) == ["AAPL", "SPY"]
    assert (p["timeframe"], p["adjustment"], p["feed"], p["limit"], p["sort"]) == ("1Day", "split", "sip", "10000", "asc")
    assert p["start"] == "2016-01-01T00:00:00Z"
    assert datetime.fromisoformat(p["end"].replace("Z", "+00:00")) <= now - timedelta(minutes=15)  # Basic-Plan: SIP nur älter als 15 min
    assert second.url.params["page_token"] == _fixture_pages()[0]["next_page_token"]
    for r in fake.requests:
        assert r.headers["APCA-API-KEY-ID"] == KEY_ID and r.headers["APCA-API-SECRET-KEY"] == SECRET
        assert SECRET not in str(r.url)

    aapl = store.load_candles(AAPL, "1d")
    assert len(aapl) == 5 and len(store.load_candles(SPY, "1d")) == 5
    assert aapl[0].open_time == utc(2026, 9, 28, 13, 30) and aapl[0].close_time == utc(2026, 9, 28, 20)  # 09:30–16:00 New York (EDT)
    assert aapl[-1].close == D("258.1") and aapl[-1].source == "alpaca-sip"
    assert result.status == {f"{AAPL}|1d": "OK", f"{SPY}|1d": "OK"} and result.changed == {f"{AAPL}|1d", f"{SPY}|1d"}
    assert SECRET not in caplog.text and SECRET not in repr(_data(fake)) and SECRET not in repr(AlpacaCredentials(KEY_ID, SECRET))


def test_http_error_does_not_leak_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": f"forbidden for {request.headers['APCA-API-SECRET-KEY']}"})

    data = AlpacaData(AlpacaCredentials(KEY_ID, SECRET), client=httpx.Client(transport=httpx.MockTransport(handler)))
    store = _store()
    result, _ = stocks.sync_stocks(store, data, close_of(date(2026, 10, 2)) + timedelta(minutes=25))
    assert result.status[f"{AAPL}|1d"] == "ERROR"
    detail = store.feed[("alpaca-sip", AAPL, "1d")]["detail"]
    assert "403" in detail and SECRET not in detail


def _daily_page(days: dict[str, list[tuple[date, str]]]) -> dict[str, Any]:
    bars = {
        sym: [{"t": datetime.combine(d, datetime.min.time(), NEW_YORK).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "o": float(c), "h": float(c) + 1, "l": float(c) - 1, "c": float(c), "v": 1_000_000, "n": 1000, "vw": float(c)} for d, c in rows]
        for sym, rows in days.items()
    }
    return {"bars": bars, "next_page_token": None, "currency": "USD"}


def test_fetch_only_after_close_plus_20_minutes_and_once_per_session() -> None:
    store, fake = _store(), FakeAlpaca(_fixture_pages())
    fri = date(2026, 10, 2)
    stocks.sync_stocks(store, _data(fake), close_of(fri) + timedelta(minutes=25))
    n = len(fake.requests)

    # Wochenende und Montag während der Sitzung: nichts fällig, kein Abruf
    for t in (utc(2026, 10, 3, 12), utc(2026, 10, 4, 23), utc(2026, 10, 5, 15), close_of(date(2026, 10, 5)) + timedelta(minutes=19)):
        result, _ = stocks.sync_stocks(store, _data(fake), t)
        assert len(fake.requests) == n and result.changed == set() and set(result.status.values()) == {"OK"}

    mon = date(2026, 10, 5)
    fake.pages = lambda req: _daily_page({"AAPL": [(fri, "258.1"), (mon, "260")], "SPY": [(fri, "667.9"), (mon, "670")]})
    result, _ = stocks.sync_stocks(store, _data(fake), close_of(mon) + timedelta(minutes=21))
    assert len(fake.bar_requests) == 3
    assert fake.bar_requests[-1].url.params["start"] == "2026-10-02T00:00:00Z"  # Fortsetzung ab der jüngsten Kerze
    assert result.changed == {f"{AAPL}|1d", f"{SPY}|1d"} and store.load_candles(AAPL, "1d")[-1].close == D(260)
    n = len(fake.requests)
    stocks.sync_stocks(store, _data(fake), close_of(mon) + timedelta(minutes=40))
    assert len(fake.requests) == n  # einmal pro Sitzung


def test_stale_after_two_hours_and_gaps_only_on_trading_days() -> None:
    store, fake = _store(), FakeAlpaca(_fixture_pages())
    stocks.sync_stocks(store, _data(fake), close_of(date(2026, 10, 2)) + timedelta(minutes=25))
    fake.pages = lambda req: {"bars": {}, "next_page_token": None}  # Montagskerze kommt (noch) nicht
    mon_close = close_of(date(2026, 10, 5))
    within, _ = stocks.sync_stocks(store, _data(fake), mon_close + timedelta(minutes=90))
    assert within.status[f"{AAPL}|1d"] == "OK"
    late, _ = stocks.sync_stocks(store, _data(fake), mon_close + timedelta(hours=2, minutes=1))
    assert late.status[f"{AAPL}|1d"] == "STALE"

    # Wochenende zwischen 2.10. und 5.10. ist keine Lücke; ein fehlender Handelstag schon
    gap_store = _store(("AAPL",))
    days = [date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 5), date(2026, 10, 6)]
    gap_store.insert_candles([_candle(AAPL, d, D(100)) for d in days], utc(2026, 10, 7))
    status, detail, _ = stocks.assess_stock(gap_store, gap_store.universe()[0], close_of(date(2026, 10, 6)) + timedelta(minutes=30), CAL)
    assert status == "GAP" and detail is not None and "2026-10-02" in detail
    ok_store = _store(("AAPL",))
    ok_store.insert_candles([_candle(AAPL, d, D(100)) for d in (date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5))], utc(2026, 10, 6))
    assert stocks.assess_stock(ok_store, ok_store.universe()[0], close_of(date(2026, 10, 5)) + timedelta(minutes=30), CAL)[0] == "OK"


def test_split_adjustment_change_blocks_feed_and_adds_no_mixed_scale() -> None:
    store, fake = _store(), FakeAlpaca(_fixture_pages())
    fri, mon = date(2026, 10, 2), date(2026, 10, 5)
    stocks.sync_stocks(store, _data(fake), close_of(fri) + timedelta(minutes=25))
    # 2:1-Split am Montag: Alpaca liefert den Freitag nun halbiert
    fake.pages = lambda req: _daily_page({"AAPL": [(fri, "129.05"), (mon, "130")], "SPY": [(fri, "667.9"), (mon, "670")]})
    result, _ = stocks.sync_stocks(store, _data(fake), close_of(mon) + timedelta(minutes=25))
    assert result.status[f"{AAPL}|1d"] == "ERROR" and result.status[f"{SPY}|1d"] == "OK"
    assert store.load_candles(AAPL, "1d")[-1].close == D("258.1")  # keine Kerze im neuen Massstab angehängt
    assert "Kursanpassung" in store.feed[("alpaca-sip", AAPL, "1d")]["detail"]


def test_backfill_respects_time_budget_and_resumes() -> None:
    store, fake = _store(), FakeAlpaca(_fixture_pages())
    ticks = iter([0.0, 0.0, 0.0, 19.0, 19.0, 19.0, 19.0])
    now = close_of(date(2026, 10, 2)) + timedelta(minutes=25)
    stocks.sync_stocks(store, _data(fake), now, clock=lambda: next(ticks, 19.0))
    assert len(fake.bar_requests) == 1  # zweite Seite passt nicht mehr ins Budget
    assert len(store.load_candles(AAPL, "1d")) == 2 and store.load_candles(SPY, "1d") == []
    # nächster Tick: AAPL ab der jüngsten Kerze, SPY ab 2016 (zwei Gruppen)
    fake.pages = lambda req: _daily_page(
        {"AAPL": [(date(2026, 9, d), "256.77") for d in (29, 30)]} if "AAPL" in req.url.params["symbols"] else {"SPY": [(date(2026, 10, 2), "667.9")]}
    )
    stocks.sync_stocks(store, _data(fake), now + timedelta(minutes=1))
    starts = sorted(r.url.params["start"] for r in fake.bar_requests[1:])
    assert starts == ["2016-01-01T00:00:00Z", "2026-09-29T00:00:00Z"]
    assert len(store.load_candles(AAPL, "1d")) == 3


def test_running_session_and_non_session_bars_are_dropped() -> None:
    rows, _ = parse_bars(_daily_page({"AAPL": [(date(2026, 10, 2), "1"), (date(2026, 10, 3), "1"), (date(2026, 10, 5), "1")]}))
    candles = to_candles(rows, {"AAPL": AAPL}, CAL, utc(2026, 10, 5, 15))  # Montag läuft noch, Samstag ist kein Handelstag
    assert [c.open_time.date() for c in candles] == [date(2026, 10, 2)]


# --- Signale -------------------------------------------------------------------------------------------------


def _candle(iid: str, day: date, close: D, open_: D | None = None, low: D | None = None, high: D | None = None, volume: D = D(1_000_000)) -> Candle:
    s = CAL.session(day)
    assert s is not None
    o = open_ if open_ is not None else close
    return Candle(iid, "1d", s.open, s.close, o, high if high is not None else max(o, close) + 1, low if low is not None else min(o, close) - 1, close,
                  volume, 100, "test")


def _history(store: MemoryStore, last: date, n: int = 320) -> None:
    sessions = CAL.sessions_between(last - timedelta(days=n * 2), last)[-n:]
    for iid, seed in ((AAPL, 3), (SPY, 4)):
        closes = random_walk(len(sessions), seed, drift=0.0005, vol=0.01)
        store.insert_candles([_candle(iid, s.day, D(str(round(c, 2)))) for s, c in zip(sessions, closes, strict=True)], utc(2026, 10, 3))


def test_signal_valid_until_next_session_close() -> None:
    fn = stocks.valid_until_of(CAL)
    aapl = stock_instruments(True)[12]
    assert aapl.id == AAPL
    assert fn(aapl, "1d", close_of(date(2026, 10, 2))) == close_of(date(2026, 10, 5))  # Freitag → Montag
    assert fn(aapl, "1d", close_of(date(2026, 11, 25))) == close_of(date(2026, 11, 27))  # über Thanksgiving auf den Halbtag
    assert fn(aapl, "1d", utc(2026, 10, 3, 0)) == utc(2026, 10, 3, 0)  # kein Sitzungsschluss → sofort abgelaufen
    crypto = stock_instruments(True)[0].__class__("KRAKEN:BTC/USD", "CRYPTO_SPOT", "KRAKEN", "XBTUSD", "BTC", "BTC", "USD")
    assert fn(crypto, "1d", utc(2026, 10, 3, 0)) is None


def test_signals_only_for_latest_session_and_valid_over_weekend() -> None:
    fri = date(2026, 10, 2)
    keys = {f"{AAPL}|1d", f"{SPY}|1d"}
    feeds = dict.fromkeys(keys, "OK")

    # Samstag: das Freitagssignal ist noch gültig (bis Montag 16:00 New York)
    store = _store()
    _history(store, fri)
    created, still = signals.run_once(store, ["4h", "1d"], feeds, keys, "alpaca-sip", utc(2026, 10, 3, 21), valid_until_of=stocks.valid_until_of(CAL))
    assert created == 2 * len(ACTIVE) and still == set()
    sig = next(iter(store.signals.values()))
    assert sig.decision.candle_close == close_of(fri) and sig.valid_until == close_of(date(2026, 10, 5))
    # Standardregel (Schluss + 24 h) hätte das Freitagssignal am Samstagabend schon verworfen
    plain = _store()
    _history(plain, fri)
    assert signals.run_once(plain, ["4h", "1d"], feeds, keys, "alpaca-sip", utc(2026, 10, 3, 21))[0] == 0
    # nach dem Montagsschluss: verpasst, wird nicht nachgeholt
    late = _store()
    _history(late, fri)
    assert signals.run_once(late, ["4h", "1d"], feeds, keys, "alpaca-sip", close_of(date(2026, 10, 5)), valid_until_of=stocks.valid_until_of(CAL))[0] == 0
    # Leitinstrument SPY ohne die Freitagskerze → Signal für AAPL bleibt offen (Regime unvollständig)
    partial = _store()
    _history(partial, fri)
    del partial.candles[(SPY, "1d", CAL.session(fri).open)]  # type: ignore[union-attr]
    created, still = signals.run_once(partial, ["1d"], feeds, {f"{AAPL}|1d"}, "alpaca-sip", utc(2026, 10, 2, 21), valid_until_of=stocks.valid_until_of(CAL))
    assert created == 0 and still == {f"{AAPL}|1d"}


def test_stock_open_keys_survive_the_weekend() -> None:
    store = _store()
    _history(store, date(2026, 10, 2), n=30)
    store.latest_signal_counts = lambda: {f"{AAPL}|1d": (close_of(date(2026, 10, 2)), 1)}  # type: ignore[method-assign]
    assert stocks.stock_open_keys(store, utc(2026, 10, 4, 12), CAL, len(ACTIVE)) == {f"{AAPL}|1d"}
    assert stocks.stock_open_keys(store, close_of(date(2026, 10, 5)), CAL, len(ACTIVE)) == set()


# --- Kosten, Grösse, Simulation ----------------------------------------------------------------------------------


def test_ibkr_fixed_fee_min_and_max() -> None:
    m = IBKR_FIXED_US
    assert m.order_fee(D(1000), D(100), True) == D("5.000")  # 0.005 × 1000
    assert m.order_fee(D(100), D(50), True) == D("1.00")  # 0.50 → Mindestgebühr 1.00
    assert m.order_fee(D(1), D(50), False) == D("0.50")  # Mindestgebühr wäre > 1 % → Höchstgrenze 1 % gilt
    assert m.order_fee(D(500), D("0.10"), True) == D("0.5000")  # 2.50 je Stück wäre > 1 % von 50
    assert m.order_fee(D(0), D(100), True) == 0
    assert not m.linear and KRAKEN_SPOT_TIER1.linear
    assert KRAKEN_SPOT_TIER1.order_fee(D(2), D(100), True) == KRAKEN_SPOT_TIER1.fee(D(200), True)  # Krypto unverändert
    assert m.scaled(D(2)).order_fee(D(100), D(50), True) == D("2.00")


def test_whole_share_sizing_includes_minimum_fee() -> None:
    spec = InstrumentSpec(D("0.01"), D(1), D(1), D(0))
    s = size_position(D(50), D(10_000), D(100), D(96), IBKR_FIXED_US, spec)
    # je Aktie ≈ 4.06 Risiko → 12 Stück, aber mit 2 × USD 1 Mindestgebühr 50.58 > 50 → 11 Stück
    assert s.qty == 11 and s.qty == s.qty.to_integral_value()
    assert s.planned_risk == order_risk(IBKR_FIXED_US, D(11), D(100), D(96)) and s.planned_risk <= 50
    assert order_risk(IBKR_FIXED_US, D(12), D(100), D(96)) > 50
    too_small = size_position(D(3), D(10_000), D(100), D(96), IBKR_FIXED_US, spec)
    assert not too_small.ok and too_small.reason is not None
    # Kapitalgrenze erlaubt nur 2 ganze Aktien
    assert size_position(D(500), D(250), D(100), D(96), IBKR_FIXED_US, spec).qty == 2


def _working(order_id: str, side: Side, type_: OrderType, role: OrderRole, qty: D, at: datetime, **kw: Any) -> Order:
    o = Order(id=order_id, intent_key=order_id, mode=Mode.PAPER, account_id="paper-x", instrument_id=AAPL, side=side, type=type_, role=role,
              qty=qty, created_at=at, **kw)
    for s in (OrderState.CHECKED, OrderState.SUBMITTED, OrderState.ACCEPTED):
        transition(o, s, at)
    return o


def test_overnight_gap_stop_fills_at_open() -> None:
    stop = _working("s", Side.SELL, OrderType.STOP, OrderRole.PROTECT, D(11), close_of(date(2026, 10, 2)), stop_price=D(96))
    fill = simulate(stop, _candle(AAPL, date(2026, 10, 5), D("93.5"), open_=D(94), low=D(93)), IBKR_FIXED_US, stocks.STOCK_SIM, "USD")
    assert fill is not None and fill.price == D(94) * (1 - D("0.0005")) and fill.fee == D("1.00") and fill.qty == 11


def test_paper_stock_cycle_whole_shares_next_session_fill_and_gap_stop() -> None:
    fri, mon, tue = date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)
    now = close_of(fri) + timedelta(minutes=25)
    state = PaperState("paper-x", 1, Account("paper-x", "USD", D(10_000)), close_of(fri))
    s2 = next(s for s in ACTIVE if s.version.id.startswith("s2"))
    sig = SignalIn("1", AAPL, "1d", s2.version.id, close_of(fri), Regime.UP, 70, D(100), D(96), None, 30, D(99), close_of(mon))
    risk = RiskInputs(D(10_000), D(10_000), D(10_000), 0, {}, True, {AAPL: True})
    spec = {AAPL: InstrumentSpec(D("0.01"), D(1), D(1), D(0))}
    submit_entries(state, [sig], now, {AAPL: D(100)}, risk, RiskPolicy(), IBKR_FIXED_US, spec, [s2])
    assert state.outcomes[-1].status == "ORDERED"
    entry = next(iter(state.orders.values())).order
    assert entry.qty == entry.qty.to_integral_value() and entry.qty > 0

    # Montag: Eröffnung über dem Limit, Tief darunter → Fill zum Limit in ganzen Stücken, Gebühr nach IBKR
    step_candle(state, close_of(mon), {AAPL: _candle(AAPL, mon, D("100.5"), open_=D("100.8"), low=D("99.5"), volume=D(5_000_000))}, {}, {},
                IBKR_FIXED_US, stocks.STOCK_SIM, {AAPL: D("0.01")}, "USD")
    trade = state.trades[AAPL]
    assert trade.qty == entry.qty and trade.entry_fees == IBKR_FIXED_US.order_fee(entry.qty, D(100), False)
    # Dienstag: Eröffnung mit Lücke unter den Stop → Ausführung zur Eröffnung, nicht zum Stop
    step_candle(state, close_of(tue), {AAPL: _candle(AAPL, tue, D(93), open_=D("94.2"), low=D(92))}, {}, {}, IBKR_FIXED_US, stocks.STOCK_SIM,
                {AAPL: D("0.01")}, "USD")
    closed = state.dirty_trades[trade.id]
    assert closed.status == "CLOSED" and closed.exit_reason == "Stop"
    assert closed.exit_value == trade.qty * D("94.2") * (1 - D("0.0005"))
    assert closed.net is not None and closed.net < -closed.planned_risk  # Lücke: schlechter als geplant
    assert make_session(fri).close == close_of(fri)
