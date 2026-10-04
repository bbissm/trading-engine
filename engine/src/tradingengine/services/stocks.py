"""US-Aktien/ETFs im Signalbetrieb und Paper-Handel: Tageskerzen von Alpaca, Börsenkalender, IBKR-Kosten.

Ablauf je Minuten-Tick (zustandslos, alles aus der Datenbank abgeleitet):

1. `sync_stocks`: Abruf erst 20 min nach Sitzungsschluss und nur, solange die Kerze der zuletzt
   abgeschlossenen Sitzung fehlt (also einmal pro Sitzung). Historie ab 2016 seitenweise innerhalb eines
   Zeitbudgets (Standard 20 s); der nächste Tick setzt bei der jüngsten gespeicherten Kerze fort.
2. Feed-Qualität gegen den Börsenkalender: STALE, wenn die Kerze der letzten Sitzung > 2 h nach Schluss
   fehlt; GAP nur für fehlende *Handelstage* (Wochenenden/Feiertage sind keine Lücken).
3. Signale nur zur zuletzt abgeschlossenen Sitzung; gültig bis zum Schluss der nächsten Sitzung
   (`valid_until_of` für `signals.run_once`). Die Paper-Order füllt frühestens zur Eröffnung der Folgesitzung.
4. Paper-Konten mit IBKR-Kostenmodell schreiten über Sitzungsschlüsse fort (`run_stock_account`).

Adjustierung: `adjustment=split`. Alpaca rechnet Splits rückwirkend mit dem *heutigen* Faktor ein – Kurse vor
einem Split sind also nicht die damals gehandelten (Punkt-in-Zeit-Verzerrung, v. a. für Stückzahlen,
Mindestgebühren und Preisfilter). Dividenden werden für Signale nicht berücksichtigt: Ex-Tage erscheinen als
Kursrückgang. Erscheint nach einem neuen Split eine gespeicherte Kerze mit anderem Kurs, wird der Feed auf
ERROR gesetzt und es kommen keine neuen Kerzen hinzu (sonst lägen zwei Massstäbe in einer Reihe), bis die
Historie des Symbols neu geladen ist.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..adapters.alpaca_data import SIP_DELAY, SOURCE, AlpacaData, AlpacaError, instruments_by_symbol, to_candles
from ..adapters.pg_paper import PaperRepo, policy_from_json
from ..calendar_us import UsCalendar, default_calendar, ny_date
from ..core import indicators as ind
from ..core.candles import Candle
from ..core.costs import CostModel
from ..core.costs_stocks import IBKR_FIXED_US, STOCK_COST_MODELS
from ..core.paper import Management, PaperState, step_candle, submit_entries
from ..core.regime import RegimePoint, daily_regimes, regime_at
from ..core.simulator import REALISM, SimConfig
from ..core.sizing import InstrumentSpec
from ..ports import Instrument, Store
from ..universe_stocks import STOCK_TIMEFRAMES, is_stock, is_stock_id, stock_instruments, symbol_of
from .marketdata import GAP_CHECK_CANDLES, SyncResult, key_of
from .paper import _risk_inputs, _strategies

log = logging.getLogger(__name__)

TIMEFRAME = STOCK_TIMEFRAMES[0]  # "1d"
HISTORY_START = date(2016, 1, 1)
FETCH_DELAY = timedelta(minutes=20)  # Abruf frühestens 20 min nach Sitzungsschluss
STALE_AFTER = timedelta(hours=2)
TICK_BUDGET_S = 20.0
MIN_REQUEST_S = 2.0  # neuer Abruf nur, wenn mindestens so viel Budget übrig ist
SPLIT_TOLERANCE = Decimal("0.01")  # erneut geladene Kerze weicht > 1 % ab → Anpassung (Split) vermutet
SPLIT_MARKER = "Kursanpassung"
MISSING_CANDLE_WAIT = timedelta(days=1)  # Paper wartet so lange auf eine fehlende Kerze eines beteiligten Titels

STOCK_SIM = SimConfig(version="sim-stocks@1", qty_step=Decimal(1))
STOCK_REALISM: dict[str, str] = {
    **REALISM,
    "Kurslücken bei Stops": "simuliert: Lücke über Nacht/Wochenende → Stop füllt zum Eröffnungskurs, abzüglich Slippage",
    "Mindestmengen und Präzision": "simuliert: nur ganze Aktien (IBKR-API ohne Bruchstücke)",
    "Gebühren beider Seiten": "simuliert: IBKR Fixed USD 0.005/Aktie, min. USD 1, max. 1 % je Order",
    "Handelszeiten": "simuliert: Ausführung nur in regulären Sitzungen (Tageskerzen), keine Vor-/Nachbörse",
    "Dividenden": "fehlen – Kurse split-, nicht dividendenadjustiert; Ausschüttungen werden nicht gutgeschrieben",
}


# --- Universum ---------------------------------------------------------------------------------------


def ensure_stock_universe(store: Store, data: AlpacaData | None) -> None:
    """Legt das Aktien-Universum an, sobald Schlüssel konfiguriert sind (einmalig, mit Kotierungsbörse)."""
    if data is None:
        return
    known = {i.id for i in store.universe()}
    wanted = stock_instruments(True)
    if all(i.id in known for i in wanted):
        return
    try:
        exchanges = data.exchanges([i.venue_symbol for i in wanted if i.id not in known])
    except AlpacaError as exc:
        log.warning("Alpaca-Stammdaten nicht abrufbar, Börse bleibt «US»: %s", exc)
        exchanges = {}
    store.upsert_instruments(stock_instruments(True, exchanges))


# --- Marktdaten ----------------------------------------------------------------------------------------


def assess_stock(store: Store, instrument: Instrument, now: datetime, calendar: UsCalendar) -> tuple[str, str | None, datetime | None]:
    """Status OK | STALE | GAP gegen den Börsenkalender."""
    candles = store.load_candles(instrument.id, TIMEFRAME, GAP_CHECK_CANDLES)
    if not candles:
        return "STALE", "Keine Kerzen vorhanden", None
    last_close = candles[-1].close_time
    due = calendar.last_closed(now - STALE_AFTER)
    if due is not None and last_close < due.close:
        return "STALE", f"Sitzung {due.day.isoformat()} fehlt (Schluss {due.close.isoformat()})", last_close
    have = {ny_date(c.open_time) for c in candles}
    first = ny_date(candles[0].open_time)
    missing = [s.day for s in calendar.sessions_between(first, ny_date(candles[-1].open_time)) if s.day not in have]
    if missing:
        return "GAP", f"{len(missing)} fehlende Handelstag(e), erster {missing[0].isoformat()}", last_close
    return "OK", None, last_close


def _start_of(last: Candle | None) -> datetime:
    """Abrufbeginn: ab der jüngsten gespeicherten Sitzung (Überlappung zur Split-Erkennung), sonst ab 2016."""
    if last is None:
        return datetime(HISTORY_START.year, HISTORY_START.month, HISTORY_START.day, tzinfo=UTC)
    return last.open_time.replace(hour=0, minute=0, second=0, microsecond=0)


def sync_stocks(
    store: Store,
    data: AlpacaData | None,
    now: datetime,
    calendar: UsCalendar | None = None,
    budget_s: float = TICK_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[SyncResult, UsCalendar]:
    """Holt fällige Tageskerzen für die Aktien des Universums. Rückgabe: Status/neue Schlüssel und der
    verwendete Kalender (für `valid_until_of` und Paper im selben Tick)."""
    started = clock()
    cal = calendar or default_calendar(now)
    result = SyncResult()
    stocks = [i for i in store.universe() if is_stock(i)]
    if not stocks:
        return result, cal
    if data is None:
        for inst in stocks:
            key = key_of(inst.id, TIMEFRAME)
            store.set_feed_status(SOURCE, inst.id, TIMEFRAME, "ERROR", "Alpaca-Schlüssel nicht konfiguriert", None, None)
            result.status[key] = "ERROR"
        return result, cal

    known = store.feed_statuses(data.source)
    latest = cal.last_closed(now - FETCH_DELAY)
    last_by_id: dict[str, Candle | None] = {}
    due: list[Instrument] = []
    for inst in stocks:
        rows = store.load_candles(inst.id, TIMEFRAME, 1)
        last = rows[-1] if rows else None
        last_by_id[inst.id] = last
        missing_latest = latest is not None and (last is None or last.close_time < latest.close)
        if missing_latest or known.get(key_of(inst.id, TIMEFRAME)) == "ERROR":
            due.append(inst)

    errors: dict[str, str] = {}
    if due:
        # Kalender von Alpaca für den abgerufenen Bereich: er überschreibt die Regeln (ausserordentliche Schliessungen).
        first_day = min(ny_date(_start_of(last_by_id[i.id])) for i in due)
        try:
            cal = cal.overlay(data.calendar(first_day, ny_date(now) + timedelta(days=14), timeout_s=_remaining(started, budget_s, clock)))
        except AlpacaError as exc:
            log.warning("Alpaca-Kalender nicht abrufbar, Regelkalender gilt: %s", exc)
        errors = _fetch(store, data, due, last_by_id, now, cal, result, started, budget_s, clock)

    for inst in stocks:
        key = key_of(inst.id, TIMEFRAME)
        status, detail, last_close = assess_stock(store, inst, now, cal)
        if inst.id in errors:
            status, detail = "ERROR", errors[inst.id]
        store.set_feed_status(data.source, inst.id, TIMEFRAME, status, detail, last_close, now if status == "OK" else None)
        result.status[key] = status
    return result, cal


def _remaining(started: float, budget_s: float, clock: Callable[[], float]) -> float:
    return max(0.5, budget_s - (clock() - started))


def _fetch(
    store: Store,
    data: AlpacaData,
    due: list[Instrument],
    last_by_id: dict[str, Candle | None],
    now: datetime,
    cal: UsCalendar,
    result: SyncResult,
    started: float,
    budget_s: float,
    clock: Callable[[], float],
) -> dict[str, str]:
    """Seitenweiser Abruf, gruppiert nach Abrufbeginn. Rückgabe: Fehler je Instrument."""
    end = now - SIP_DELAY
    groups: dict[datetime, list[Instrument]] = {}
    for inst in due:
        groups.setdefault(_start_of(last_by_id[inst.id]), []).append(inst)
    ids = instruments_by_symbol(due)
    errors: dict[str, str] = {}
    split_flagged: set[str] = set()
    for start, members in sorted(groups.items(), key=lambda kv: kv[0]):
        token: str | None = None
        while True:
            if budget_s - (clock() - started) < MIN_REQUEST_S:
                log.info("Alpaca: Zeitbudget erschöpft, Abruf wird im nächsten Tick fortgesetzt")
                return errors
            try:
                rows, token = data.bars_page([i.venue_symbol for i in members], start, end, token, timeout_s=_remaining(started, budget_s, clock))
            except AlpacaError as exc:
                log.warning("Alpaca-Abruf fehlgeschlagen: %s", exc)
                for inst in members:
                    errors[inst.id] = str(exc)[:300]
                break
            candles = to_candles(rows, ids, cal, now, data.source)
            fresh: list[Candle] = []
            for c in candles:
                last = last_by_id.get(c.instrument_id)
                if c.instrument_id in split_flagged:
                    continue
                if last is not None and c.open_time == last.open_time:
                    if abs(c.close - last.close) > SPLIT_TOLERANCE * last.close:
                        split_flagged.add(c.instrument_id)
                        errors[c.instrument_id] = (
                            f"{SPLIT_MARKER} erkannt (Split?): Schluss {last.open_time.date()} gespeichert {last.close}, jetzt {c.close} – "
                            "Historie des Symbols neu laden"
                        )
                    continue
                if last is None or c.open_time > last.open_time:
                    fresh.append(c)
            if store.insert_candles(fresh, available_at=now):
                for iid in {c.instrument_id for c in fresh}:
                    result.changed.add(key_of(iid, TIMEFRAME))
                log.info("Alpaca: %d neue Tageskerze(n)", len(fresh))
            if not token:
                break
    for inst in due:
        if inst.id in split_flagged:
            log.warning("%s: %s", symbol_of(inst.id), errors[inst.id])
    return errors


# --- Signale -------------------------------------------------------------------------------------------


def valid_until_of(calendar: UsCalendar) -> Callable[[Instrument, str, datetime], datetime | None]:
    """Für `signals.run_once(..., valid_until_of=...)`: Aktiensignale gelten bis zum Schluss der nächsten Sitzung.

    Für eine Kerze, die auf keinen Sitzungsschluss fällt, ist das Signal sofort abgelaufen (kein Signal ausserhalb
    der Sitzungen). Für andere Instrumente: None → Standardregel."""

    def fn(inst: Instrument, timeframe: str, close_time: datetime) -> datetime | None:
        if not is_stock(inst):
            return None
        session = calendar.session_closing_at(close_time)
        nxt = calendar.next_session(session) if session is not None else None
        return nxt.close if nxt is not None else close_time

    return fn


def stock_open_keys(store: Store, now: datetime, calendar: UsCalendar, strategies: int) -> set[str]:
    """Ergänzt `cli.open_keys` für Aktien: offen bleibt ein Schlüssel bis zum Schluss der nächsten Sitzung
    (nicht bloss 24 h – sonst verfiele ein Freitagssignal am Samstag)."""
    out: set[str] = set()
    for key, (last_close, count) in store.latest_signal_counts().items():
        instrument_id, timeframe = key.rsplit("|", 1)
        if not is_stock_id(instrument_id) or count >= strategies:
            continue
        session = calendar.session_closing_at(last_close)
        nxt = calendar.next_session(session) if session is not None else None
        if nxt is not None and now < nxt.close:
            out.add(key)
    return out


# --- Paper-Handel --------------------------------------------------------------------------------------


def is_stock_account(row: dict[str, Any]) -> bool:
    return row.get("cost_model") in STOCK_COST_MODELS


def stock_specs(specs: dict[str, InstrumentSpec]) -> dict[str, InstrumentSpec]:
    """Nur Aktien, in ganzen Stücken (die Repo-Spezifikation nutzt die Krypto-Präzision 1e-8)."""
    return {i: replace(s, qty_step=Decimal(1), min_qty=max(s.min_qty, Decimal(1))) for i, s in specs.items() if is_stock_id(i)}


def _atr(candles: list[Candle]) -> float | None:

    if not candles:
        return None
    return ind.atr([float(c.high) for c in candles], [float(c.low) for c in candles], [float(c.close) for c in candles], 14)[-1]


def advance_stock_account(
    repo: PaperRepo, state: PaperState, model: CostModel, ticks: dict[str, Decimal | None], now: datetime, calendar: UsCalendar, lookback: int = 600
) -> int:
    """Wie `services.paper._advance`, aber über Sitzungsschlüsse der Tageskerzen statt 4h-Schlüsse.

    Ein Schritt je Sitzung: Orders handeln innerhalb der Tageskerze (Eröffnung = Sitzungsbeginn, also füllt ein
    Stop nach einer Lücke über Nacht zur Eröffnung), danach Betreuung am Schluss. Fehlt für einen beteiligten
    Titel die Kerze, wird bis zu MISSING_CANDLE_WAIT gewartet (Daten unterwegs), danach ohne ihn fortgefahren
    (Handelsunterbruch)."""
    steps = 0
    regimes: dict[str, list[RegimePoint]] = {}
    closes = [t for t in repo.new_closes(TIMEFRAME, state.sim_through, now) if calendar.session_closing_at(t) is not None]
    for close_time in closes:
        involved = sorted({o.order.instrument_id for o in state.orders.values()} | set(state.trades))
        candles = repo.candles_at(TIMEFRAME, close_time, involved)
        if set(involved) - set(candles) and now - close_time < MISSING_CANDLE_WAIT:
            break
        management: dict[str, Management] = {}
        for instrument_id in state.trades:
            history = repo.candles_until(instrument_id, TIMEFRAME, close_time, lookback)
            if not history or history[-1].close_time != close_time:
                continue
            if instrument_id not in regimes:
                regimes[instrument_id] = daily_regimes(repo.candles_until(instrument_id, TIMEFRAME, now, lookback))
            management[instrument_id] = Management(history[-1], _atr(history), regime_at(regimes[instrument_id], close_time))
        step_candle(state, close_time, candles, management, repo.last_prices(TIMEFRAME, close_time), model, STOCK_SIM, ticks, state.account.currency)
        steps += 1
    return steps


def run_stock_account(repo: PaperRepo, row: dict[str, Any], feed_status: dict[str, str], now: datetime, calendar: UsCalendar) -> dict[str, Any]:
    """Ein Tick für ein Paper-Konto mit Aktien-Kostenmodell (Gegenstück zum Schleifenrumpf von
    `services.paper.run_accounts`). Handelt nur Aktiensignale."""

    account_id, ap_state = row["id"], row["state"]
    model = STOCK_COST_MODELS.get(row["cost_model"], IBKR_FIXED_US)
    policy = policy_from_json(row["policy"])
    specs, ticks, _ = repo.specs()
    state = repo.load_state(account_id)
    steps = advance_stock_account(repo, state, model, ticks, now, calendar)

    submitted = 0
    if ap_state == "ACTIVE":
        pending = [s for s, _ in repo.new_buy_signals(row["signals_through"], row["strategy_version_ids"]) if is_stock_id(s.instrument_id)]
        if pending:
            prices = repo.last_prices(TIMEFRAME, now)
            feed_ok = {i: feed_status.get(key_of(i, TIMEFRAME)) == "OK" for i in ticks if is_stock_id(i)}
            before = len(state.outcomes)
            submit_entries(state, pending, now, prices, _risk_inputs(repo, state, row, policy, feed_ok, now), policy, model,
                           stock_specs(specs), _strategies(row["strategy_version_ids"]))
            submitted = sum(1 for o in state.outcomes[before:] if o.status == "ORDERED")
    repo.save_state(state, now)
    repo.mark_signals_seen(state.episode_id, now)

    if ap_state == "WINDING_DOWN" and not state.trades and not state.orders:
        repo.set_autopilot(account_id, "STOPPED", "Abwicklung abgeschlossen: kein Bestand, keine offenen Orders", now)
        ap_state = "STOPPED"
    return {"state": ap_state, "steps": steps, "orders": submitted, "open_trades": len(state.trades)}
