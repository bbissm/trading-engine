import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from helpers import FakeMarket, instrument, make_candles, random_walk, resample_daily

from tradingengine.adapters import kraken_archive
from tradingengine.adapters.kraken_public import parse_ohlc
from tradingengine.adapters.memory_store import MemoryStore
from tradingengine.core.candles import find_gaps
from tradingengine.core.signals import Action
from tradingengine.core.strategies import ACTIVE
from tradingengine.ports import Command
from tradingengine.services import commands, marketdata, signals

TFS = ["4h", "1d"]
N = len(ACTIVE)
FIXTURES = Path(__file__).parent / "fixtures"


def _setup(n_4h: int = 6 * 400, seed: int = 11) -> tuple[MemoryStore, FakeMarket, datetime]:
    lead, other = instrument("TEST:LEAD/USD"), instrument("TEST:AAA/USD", leader_id="TEST:LEAD/USD")
    series = {}
    for inst, s in ((lead, seed), (other, seed + 1)):
        c4 = make_candles(random_walk(n_4h, s, drift=0.0006, vol=0.008), "4h", inst.id, spread=0.003)
        series[(inst.id, "4h")] = c4
        series[(inst.id, "1d")] = resample_daily(c4)
    store = MemoryStore(schema_version=1)
    store.upsert_instruments([lead, other])
    end = series[(lead.id, "4h")][-1].close_time
    return store, FakeMarket(series), end


def _all_keys(store: MemoryStore) -> set[str]:
    return {marketdata.key_of(i.id, tf) for i in store.universe() for tf in TFS}


def test_kraken_parse_drops_running_candle() -> None:
    payload = json.loads((FIXTURES / "kraken_ohlc_240.json").read_text())
    last_open = datetime.fromtimestamp(payload["result"]["XXBTZUSD"][-1][0], tz=UTC)
    now = last_open + timedelta(hours=1)  # letzte Kerze läuft noch
    candles = parse_ohlc(payload, instrument("KRAKEN:BTC/USD"), "4h", now, "kraken-rest")
    assert len(candles) == len(payload["result"]["XXBTZUSD"]) - 1
    assert all(c.close_time <= now for c in candles)
    assert candles[0].close_time - candles[0].open_time == timedelta(hours=4)
    assert str(candles[0].open) == payload["result"]["XXBTZUSD"][0][1]  # Decimal ohne Float-Umweg


def test_archive_import_fills_missing_intervals(tmp_path: Path) -> None:
    csv = tmp_path / "XBTUSD_240.csv"
    csv.write_text("1704067200,100,110,90,105,12.5,40\n1704110400,106,108,104,107,3,9\n")  # 2 Kerzen fehlen dazwischen
    candles = kraken_archive.load_csv(csv, "KRAKEN:BTC/USD", "4h")
    assert len(candles) == 4
    assert find_gaps(candles) == []
    filled = candles[1]
    assert filled.source == kraken_archive.SOURCE_FILL and filled.volume == 0
    assert filled.open == filled.close == candles[0].close


def test_sync_stores_only_new_candles_and_reports_ok() -> None:
    store, market, end = _setup()
    first = marketdata.sync_once(store, market, TFS, end + timedelta(seconds=30))
    assert set(first.status.values()) == {"OK"}
    assert first.changed == _all_keys(store)
    second = marketdata.sync_once(store, market, TFS, end + timedelta(seconds=90))
    assert second.changed == set()


def test_feed_stale_gap_and_error() -> None:
    store, market, end = _setup()
    key = ("TEST:AAA/USD", "4h")
    full = market.series[key]
    market.series[key] = full[:-1]  # letzte Kerze kommt nicht an
    late = marketdata.sync_once(store, market, TFS, end + timedelta(minutes=10))
    assert late.status["TEST:AAA/USD|4h"] == "STALE"
    within_grace = MemoryStore(1)
    within_grace.upsert_instruments(list(store.instruments.values()))
    assert marketdata.sync_once(within_grace, market, TFS, end + timedelta(seconds=30)).status["TEST:AAA/USD|4h"] == "OK"

    market.series[key] = full[:-40] + full[-35:]  # Lücke im jüngeren Bereich
    gap_store = MemoryStore(1)
    gap_store.upsert_instruments(list(store.instruments.values()))
    assert marketdata.sync_once(gap_store, market, TFS, end + timedelta(seconds=30)).status["TEST:AAA/USD|4h"] == "GAP"

    market.fail.add(key)
    assert marketdata.sync_once(gap_store, market, TFS, end + timedelta(seconds=60)).status["TEST:AAA/USD|4h"] == "ERROR"
    assert gap_store.feed[("fake", "TEST:AAA/USD", "4h")]["status"] == "ERROR"


def test_signal_only_for_latest_closed_candle_and_idempotent() -> None:
    """T13.2 / T4-Vorstufe: kein Signal vor Kerzenschluss, keines für alte Kerzen, kein Duplikat."""
    store, market, end = _setup()
    now = end + timedelta(seconds=20)
    sync = marketdata.sync_once(store, market, TFS, now)
    created, pending = signals.run_once(store, TFS, sync.status, sync.changed, market.source, now)
    assert pending == set()
    assert created == 4 * N  # 2 Instrumente × 2 Zeitebenen × Strategien, je die letzte Kerze
    for sig in store.signals.values():
        assert sig.decision.candle_close <= sig.created_at < sig.valid_until
        assert sig.decision.candle_close == end
        assert sig.data_age_s == 20
        assert sig.decision.action in (Action.BUY, Action.NO_TRADE)
        assert sig.decision.triggers
    # Wiederholung (Neustart, doppeltes Ereignis): keine zweite Entscheidung zur selben Kerze
    again, _ = signals.run_once(store, TFS, sync.status, _all_keys(store), market.source, now + timedelta(seconds=60))
    assert again == 0 and len(store.signals) == 4 * N
    # Feature-Snapshots wurden für die Historie geschrieben, Signale nicht
    assert len(store.snapshots) > 1000


def test_expired_candle_gets_no_signal() -> None:
    store, market, end = _setup()
    now = end + timedelta(hours=30)  # 4h- und Tageskerze sind abgelaufen
    sync = marketdata.sync_once(store, market, TFS, now)
    created, pending = signals.run_once(store, TFS, {k: "OK" for k in sync.status}, sync.changed, market.source, now)
    assert created == 0 and pending == set() and not store.signals


def test_bad_feed_blocks_and_retries_until_ok() -> None:
    """T8-Vorstufe: veralteter/fehlerhafter Feed (auch des Leitinstruments) sperrt Signale."""
    store, market, end = _setup()
    now = end + timedelta(seconds=20)
    sync = marketdata.sync_once(store, market, TFS, now)
    status = dict(sync.status)
    status["TEST:LEAD/USD|1d"] = "STALE"
    created, pending = signals.run_once(store, TFS, status, sync.changed, market.source, now)
    assert created == 0
    assert pending == _all_keys(store)  # alle hängen am Regime des Leitinstruments
    created, pending = signals.run_once(store, TFS, sync.status, pending, market.source, now + timedelta(seconds=60))
    assert created == 4 * N and pending == set()


def test_missing_daily_candle_defers_4h_decision() -> None:
    """Fehlt die fällige Tageskerze noch, wartet die 4h-Entscheidung – sonst wäre sie nicht reproduzierbar."""
    store, market, end = _setup()
    daily_key = ("TEST:AAA/USD", "1d")
    full_daily = market.series[daily_key]
    market.series[daily_key] = full_daily[:-1]
    now = end + timedelta(seconds=20)  # innerhalb der Karenz gilt der Tages-Feed noch als OK
    sync = marketdata.sync_once(store, market, TFS, now)
    assert sync.status["TEST:AAA/USD|1d"] == "OK"
    created, pending = signals.run_once(store, TFS, sync.status, sync.changed, market.source, now)
    assert "TEST:AAA/USD|4h" in pending
    assert not any(k[1] == "TEST:AAA/USD" for k in store.signals)

    market.series[daily_key] = full_daily
    later = now + timedelta(seconds=60)
    sync2 = marketdata.sync_once(store, market, TFS, later)
    created2, pending2 = signals.run_once(store, TFS, sync2.status, pending | sync2.changed, market.source, later)
    assert pending2 == set()
    assert sum(1 for k in store.signals if k[1] == "TEST:AAA/USD") == 2 * N


def test_replay_reproduces_stored_decisions() -> None:
    """T13.5-Vorstufe: zwei unabhängige Läufe über dieselben Daten liefern identische Entscheidungen."""
    results = []
    for _ in range(2):
        store, market, end = _setup(seed=23)
        now = end + timedelta(seconds=5)
        sync = marketdata.sync_once(store, market, TFS, now)
        signals.run_once(store, TFS, sync.status, sync.changed, market.source, now)
        results.append({k: v.decision for k, v in store.signals.items()})
    assert results[0] == results[1] and results[0]


def test_commands_ping_and_unknown() -> None:
    store = MemoryStore(1)
    store.commands = [Command(1, "PING", None, {}, "user:test"), Command(2, "BUY_EVERYTHING", None, {}, "user:test")]
    now = datetime(2026, 10, 4, tzinfo=UTC)
    assert commands.process_pending(store, now) == 2
    assert store.command_results[1]["status"] == "DONE" and store.command_results[1]["result"]["pong"] is True
    assert store.command_results[2]["status"] == "REJECTED"
    assert store.audits and store.audits[0]["kind"] == "command.rejected"
    assert commands.process_pending(store, now) == 0
