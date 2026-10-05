"""Bedienbefehle für den Live-Autopiloten (Tabelle `command`, verarbeitet nur im Live-Prozess).

Befehle ändern Zustände in der Datenbank; die Wirkung beim Anbieter (Stornos, Exits, Schutz) führt der nächste
Schritt des Live-Durchlaufs aus – zustandsgetrieben, damit ein Abbruch nichts halb erledigt zurücklässt:
Einstiegsorders werden storniert, sobald der Zustand nicht AKTIV ist; ein gesetzter `exit_reason` an einem offenen
Trade bedeutet «Ausstieg angefordert» (Stop stornieren → Bestätigung → Exit).

Mandatsaktivierung und Live-Freigabe einer Strategieversion schreibt die Web-App nach Step-up; der Live-Prozess
prüft die Frische des Step-ups bei der Übernahme (manager._mandate). `LIVE_CLOSE_ALL` und `LIVE_RESUME`
verlangen `params.step_up_at` (höchstens 5 Minuten alt).

`LIVE_ASSIGN_POSITION` ordnet eine fremde Position ausdrücklich einer für Live freigegebenen Strategieversion zu
(docs/06, 3.4). Der Befehl legt nur den verwalteten Trade an; den Stop-Loss bei Kraken setzt der Schutzschritt
desselben Durchlaufs (siehe `_assign`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ..adapters.pg_paper import policy_from_json
from ..core.candles import floor_time
from ..core.costs import KRAKEN_SPOT_TIER1, plan_costs
from ..core.paper import TradeRec
from ..core.regime import Regime
from ..core.signals import Action, Decision
from ..ports import Command
from ..services.strategy_registry import all_strategies
from .alerts import raise_alert, resolve_alert
from .exchange import Exchange, ExchangeError, OrderRequest, PermissionDenied, Quote, Rejected
from .locks import RECON_MAX_AGE, STEP_UP_MAX_AGE
from .repo import LiveRepo, LiveTrade

Conn = psycopg.Connection[dict[str, Any]]
ACTOR = "engine:live"
REGISTER = "LIVE_ACCOUNT_REGISTER"
ASSIGN = "LIVE_ASSIGN_POSITION"
ACCOUNT_COMMANDS = ("LIVE_PAUSE", "LIVE_RESUME", "LIVE_STOP", "LIVE_CLOSE_ALL", "LIVE_EMERGENCY", "ORDER_APPROVAL_DECIDE", "MANDATE_SUSPEND", ASSIGN)
LIVE_TYPES = (REGISTER, *ACCOUNT_COMMANDS)
ACCOUNT_ID = "live-kraken"
DEFAULT_TIMEFRAME = "4h"


@dataclass(frozen=True, slots=True)
class MarketView:
    """Was der Live-Durchlauf zum Zeitpunkt der Befehle weiss (nach dem Abgleich): Kurs je Instrument, Eigenkapital."""

    quote: Callable[[str], Quote | None]
    equity: Callable[[], Decimal | None]


def pending(conn: Conn, types: tuple[str, ...]) -> list[Command]:
    rows = conn.execute("select id, type, target, params, issued_by from command where status = 'PENDING' and type = any(%s) order by id limit 20",
                        (list(types),)).fetchall()
    return [Command(r["id"], r["type"], r["target"], r["params"] or {}, r["issued_by"]) for r in rows]


def complete(conn: Conn, cmd: Command, status: str, result: dict[str, Any], now: datetime) -> None:
    conn.execute("update command set status = %s, result = %s, handled_at = %s where id = %s and status = 'PENDING'", (status, Jsonb(result), now, cmd.id))
    conn.execute("insert into audit_event (actor, kind, object, data) values (%s, %s, %s, %s)",
                 (ACTOR, "command.done" if status == "DONE" else "command.rejected", f"command:{cmd.id}",
                  Jsonb({"type": cmd.type, "target": cmd.target, "issued_by": cmd.issued_by, "result": result})))


def _fresh_step_up(params: dict[str, Any], now: datetime) -> bool:
    raw = params.get("step_up_at")
    if not isinstance(raw, str):
        return False
    try:
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    return at.tzinfo is not None and timedelta(minutes=-1) <= now - at <= STEP_UP_MAX_AGE


def _state(conn: Conn, account_id: str) -> str | None:
    row = conn.execute("select state from autopilot where account_id = %s", (account_id,)).fetchone()
    return None if row is None else str(row["state"])


def _set_state(conn: Conn, account_id: str, state: str, reason: str, now: datetime) -> None:
    before = _state(conn, account_id)
    conn.execute("insert into autopilot (account_id, state, reason, updated_at) values (%s, %s, %s, %s) on conflict (account_id) do update "
                 "set state = excluded.state, reason = excluded.reason, updated_at = excluded.updated_at", (account_id, state, reason, now))
    conn.execute("insert into audit_event (actor, kind, object, data) values (%s, 'live.state', %s, %s)",
                 (ACTOR, f"account:{account_id}", Jsonb({"from": before, "to": state, "reason": reason})))


def _request_exits(conn: Conn, account_id: str, reason: str) -> int:
    rows = conn.execute("update trade set exit_reason = %s where account_id = %s and status = 'OPEN' and exit_reason is null returning id",
                        (reason, account_id)).fetchall()
    return len(rows)


def _open_trades(conn: Conn, account_id: str) -> int:
    row = conn.execute("select count(*) as n from trade where account_id = %s and status = 'OPEN'", (account_id,)).fetchone()
    return 0 if row is None else int(row["n"])


def handle(conn: Conn, cmd: Command, now: datetime, exchange: Exchange | None = None, fingerprint: str | None = None,
           market: MarketView | None = None) -> tuple[str, dict[str, Any]]:
    """Verarbeitet einen Live-Befehl. Rückgabe: (DONE | REJECTED, Ergebnis). Ergebnisse enthalten nie Zugangsdaten."""
    if cmd.type == REGISTER:
        return _register(conn, cmd, now, exchange, fingerprint)

    account_id = cmd.target or ACCOUNT_ID
    acc = conn.execute("select id from account where id = %s and mode = 'LIVE'", (account_id,)).fetchone()
    if acc is None:
        return "REJECTED", {"reason": f"Live-Konto {account_id} nicht gefunden"}
    state = _state(conn, account_id) or "SETUP"

    if cmd.type == "LIVE_PAUSE":
        if state not in ("ACTIVE", "EMERGENCY", "READY", "RECOVERY", "ERROR"):
            return "REJECTED", {"reason": f"Im Zustand {state} nicht möglich"}
        _set_state(conn, account_id, "ENTRIES_PAUSED", "Einstiege pausiert (Bedienhandlung)", now)
        return "DONE", {"state": "ENTRIES_PAUSED", "open_trades": _open_trades(conn, account_id)}

    if cmd.type == "LIVE_RESUME":
        if not _fresh_step_up(cmd.params, now):
            return "REJECTED", {"reason": "Fortsetzen verlangt eine Step-up-Anmeldung (höchstens 5 Minuten alt)"}
        if state not in ("ENTRIES_PAUSED", "EMERGENCY", "STOPPED", "WINDING_DOWN"):
            return "REJECTED", {"reason": f"Im Zustand {state} nicht möglich"}
        if conn.execute("select 1 from mandate where account_id = %s and status = 'ACTIVE'", (account_id,)).fetchone() is None:
            return "REJECTED", {"reason": "Kein aktives Mandat"}
        _set_state(conn, account_id, "ACTIVE", "Fortgesetzt (Bedienhandlung mit Step-up)", now)
        return "DONE", {"state": "ACTIVE"}

    if cmd.type == "LIVE_STOP":
        if state in ("STOPPED",):
            return "REJECTED", {"reason": "Bereits gestoppt"}
        _set_state(conn, account_id, "WINDING_DOWN", "Geordnet stoppen: Positionen nach den Exit-Regeln der Strategie", now)
        return "DONE", {"state": "WINDING_DOWN", "open_trades": _open_trades(conn, account_id)}

    if cmd.type == "LIVE_CLOSE_ALL":
        if not _fresh_step_up(cmd.params, now):
            return "REJECTED", {"reason": "«Positionen jetzt schliessen» verlangt eine Step-up-Anmeldung (höchstens 5 Minuten alt)"}
        n = _request_exits(conn, account_id, "Positionen jetzt schliessen (Bedienhandlung)")
        _set_state(conn, account_id, "WINDING_DOWN", f"Positionen jetzt schliessen: {n} Ausstieg(e) angefordert", now)
        return "DONE", {"state": "WINDING_DOWN", "exits": n}

    if cmd.type == "LIVE_EMERGENCY":
        m = conn.execute("select policy from mandate where account_id = %s order by activated_at desc nulls last, id desc limit 1", (account_id,)).fetchone()
        policy = str(((m or {}).get("policy") or {}).get("emergency", "HOLD_PROTECTED")).upper()
        n = _request_exits(conn, account_id, "Notfall: Positionen schliessen (Notfallpolicy)") if policy == "CLOSE" else 0
        _set_state(conn, account_id, "EMERGENCY", f"Notfall ausgelöst – Policy {'schliessen' if policy == 'CLOSE' else 'halten mit Schutz'}", now)
        raise_alert(conn, "CRITICAL", "live.emergency", account_id, "autopilot", "EMERGENCY", "Notfall ausgelöst",
                    f"Einstiege gestoppt, offene Einstiegsorders werden storniert. Policy: {'schliessen' if policy == 'CLOSE' else 'halten mit Schutz'}.", now)
        return "DONE", {"state": "EMERGENCY", "policy": policy, "exits": n}

    if cmd.type == "MANDATE_SUSPEND":
        rows = conn.execute("update mandate set status = 'SUSPENDED', end_reason = 'Durch Bedienhandlung ausgesetzt', ended_at = %s "
                            "where account_id = %s and status = 'ACTIVE' returning id", (now, account_id)).fetchall()
        trades = _open_trades(conn, account_id)
        if state in ("ACTIVE", "READY"):
            _set_state(conn, account_id, "ENTRIES_PAUSED" if trades else "SETUP", "Mandat ausgesetzt", now)
        return "DONE", {"suspended": [r["id"] for r in rows], "open_trades": trades}

    if cmd.type == "ORDER_APPROVAL_DECIDE":
        decision = str(cmd.params.get("decision", "")).upper()
        try:
            approval_id = int(cmd.params.get("approval_id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return "REJECTED", {"reason": "approval_id fehlt"}
        if decision not in ("APPROVED", "REJECTED"):
            return "REJECTED", {"reason": "decision muss APPROVED oder REJECTED sein"}
        row = conn.execute("select * from order_approval where id = %s and account_id = %s", (approval_id, account_id)).fetchone()
        if row is None or row["status"] != "PENDING":
            return "REJECTED", {"reason": "Freigabeanfrage nicht offen"}
        if row["expires_at"] <= now:
            conn.execute("update order_approval set status = 'EXPIRED', decided_at = %s where id = %s", (now, approval_id))
            return "REJECTED", {"reason": "Freigabeanfrage abgelaufen – keine Antwort ist keine Erlaubnis"}
        conn.execute("update order_approval set status = %s, decided_at = %s, decided_by = %s where id = %s", (decision, now, cmd.issued_by, approval_id))
        if decision == "REJECTED":
            conn.execute("insert into signal_outcome (signal_id, account_id, episode_id, status, reasons, values, created_at) "
                         "values (%s, %s, 0, 'BLOCKED', %s, '{}'::jsonb, %s) on conflict do nothing",
                         (row["signal_id"], account_id, Jsonb(["Freigabe abgelehnt"]), now))
        return "DONE", {"approval_id": approval_id, "status": decision, "note": "Freigegebene Orders werden vor dem Senden erneut risikogeprüft"}

    if cmd.type == ASSIGN:
        return _assign(conn, cmd, account_id, now, market)

    return "REJECTED", {"reason": f"Unbekannter Befehl {cmd.type}"}


def _assign(conn: Conn, cmd: Command, account_id: str, now: datetime, market: MarketView | None) -> tuple[str, dict[str, Any]]:
    """Fremde Position einer Strategieversion zuordnen (docs/06, 3.4). Alle Prüfungen müssen gelten.

    - Immer die **ganze** fremde Menge, keine Teilzuordnung: So bleibt der Abgleich eindeutig (Bestand = verwaltete
      Menge), und es entsteht keine Frage, welche Einheiten verwaltet sind und welche nicht.
    - Einstandswert = Menge × Geldkurs bei der Übernahme, Einstiegsgebühren 0. Das Ergebnis des Trades misst also ab
      der Übernahme; die frühere Kostenbasis bleibt unbekannt (`exit_plan._live.adopted_cost_basis_unknown`).
    - Es wird keine Order gesendet. Der Schutzschritt des Durchlaufs setzt den Stop-Loss bei Kraken über genau diese
      Menge und betreut den Trade danach wie jeden anderen (Trailing nur enger, nie mehr verkaufen als gehalten).
    """
    p = cmd.params
    if not _fresh_step_up(p, now):
        return "REJECTED", {"reason": "Zuordnen verlangt eine Step-up-Anmeldung (höchstens 5 Minuten alt)"}
    instrument_id, version_id = str(p.get("instrument_id") or ""), str(p.get("strategy_version_id") or "")
    try:
        stop = Decimal(str(p.get("stop")))
    except (InvalidOperation, ValueError):
        return "REJECTED", {"reason": "Stop ist keine gültige Zahl"}
    if not stop.is_finite() or stop <= 0:
        return "REJECTED", {"reason": "Stop muss eine positive Zahl sein"}

    inst = conn.execute("select id, venue_symbol, base_asset, tick_size, min_qty from instrument where id = %s and base_asset is not null",
                        (instrument_id,)).fetchone()
    if inst is None:
        return "REJECTED", {"reason": f"Instrument {instrument_id or '–'} unbekannt"}
    if not LiveRepo(conn).approved_live(version_id):
        return "REJECTED", {"reason": f"Strategieversion {version_id or '–'} ist nicht für Live freigegeben (APPROVED_LIVE fehlt)"}
    strategy = next((s for s in all_strategies(conn) if s.version.id == version_id), None)
    if strategy is None:
        return "REJECTED", {"reason": f"Regeln der Strategieversion {version_id} sind nicht bekannt"}
    managed = conn.execute(
        "select t.id from trade t join instrument i on i.id = t.instrument_id "
        "where t.account_id = %s and t.status = 'OPEN' and i.base_asset = %s limit 1", (account_id, inst["base_asset"])).fetchone()
    if managed is not None:
        return "REJECTED", {"reason": f"{inst['base_asset']} ist bereits verwaltet (Trade {managed['id']}) – zuordenbar ist nur ein Bestand ohne verwalteten Trade"}

    recon = conn.execute("select at, status, diffs from reconciliation where account_id = %s order by at desc, id desc limit 1", (account_id,)).fetchone()
    if recon is None or recon["status"] == "ERROR" or now - recon["at"] > RECON_MAX_AGE:
        return "REJECTED", {"reason": "Kein aktueller, fehlerfreier Abgleich mit Kraken – Zuordnung erst danach"}
    diffs: list[dict[str, Any]] = list(recon["diffs"] or [])
    snapshot = next((d for d in diffs if d.get("kind") == "SNAPSHOT"), {})
    try:
        qty = Decimal(str((snapshot.get("foreign") or {}).get(instrument_id, "0")))
    except InvalidOperation:
        qty = Decimal(0)
    if qty <= 0:
        return "REJECTED", {"reason": f"Laut letztem Abgleich gibt es für {instrument_id} keine fremde Menge"}
    if any(d.get("kind") == "FOREIGN_ORDER" and d.get("pair") == inst["venue_symbol"] for d in diffs):
        return "REJECTED", {"reason": f"Fremde offene Order auf {inst['venue_symbol']} bei Kraken – zuerst dort stornieren, sonst reicht der Bestand "
                                      "womöglich nicht für den Stop-Loss"}
    if inst["min_qty"] is not None and qty < inst["min_qty"]:
        return "REJECTED", {"reason": f"Fremde Menge {qty} unter der Mindestmenge {inst['min_qty']} – Kraken nähme keinen Stop-Loss an"}
    tick: Decimal | None = inst["tick_size"]
    if tick is not None and tick > 0 and stop % tick != 0:
        return "REJECTED", {"reason": f"Stop muss ein Vielfaches der Tick-Grösse {tick} sein"}

    m = conn.execute("select budget, policy from mandate where account_id = %s and status = 'ACTIVE' order by activated_at desc nulls last, id desc limit 1",
                     (account_id,)).fetchone()
    if m is None:
        return "REJECTED", {"reason": "Kein aktives Mandat – Risikogrenzen nicht bestimmbar"}
    quote = None if market is None else market.quote(instrument_id)
    if quote is None or quote.bid <= 0:
        return "REJECTED", {"reason": "Kurs unbekannt – Zuordnung erst mit aktuellem Geldkurs"}
    bid = quote.bid
    if stop >= bid:
        return "REJECTED", {"reason": f"Stop {stop} muss unter dem aktuellen Geldkurs {bid} liegen"}
    equity = None if market is None else market.equity()
    if equity is None or equity <= 0:
        return "REJECTED", {"reason": "Eigenkapital unbekannt (Kurs oder Kontostand fehlt)"}

    # Risiko bis zum Stop inkl. geschätzter Ausstiegskosten (Taker-Gebühr + Slippage); eine Einstiegsgebühr fällt nicht an
    exit_cost = plan_costs(KRAKEN_SPOT_TIER1, bid, stop, None).stop_exit_cost
    risk = (qty * (bid - stop + exit_cost)).quantize(Decimal("0.01"))
    policy = policy_from_json(dict((m["policy"] or {}).get("risk") or {}))
    base = min(equity, Decimal(m["budget"]))  # wie bei Einstiegen: Mandatsbudget ist die Kontogrenze
    per_trade = base * policy.risk_per_trade
    values = {"menge": str(qty), "geldkurs": str(bid), "stop": str(stop), "risiko": f"{risk:.2f}", "grenze_je_trade": f"{per_trade:.2f}"}
    if risk > per_trade * Decimal("1.0001"):
        k = exit_cost / stop  # Ausstiegskosten je Einheit sind proportional zum Stop
        fit = (bid - per_trade / qty) / (1 - k)
        hint = f"; ein Stop ab etwa {_ceil_tick(fit, tick)} passt" if 0 < fit < bid else ""
        return "REJECTED", {"reason": f"Risiko {risk:.2f} USD (Menge {qty} × (Geldkurs {bid} − Stop {stop}) inkl. geschätzter Ausstiegskosten) "
                                      f"übersteigt die Grenze je Trade von {per_trade:.2f} USD – engeren Stop wählen{hint}", "values": values}
    open_risk = conn.execute("select coalesce(sum(planned_risk), 0) as r from trade where account_id = %s and status = 'OPEN'", (account_id,)).fetchone()
    reserved = conn.execute("select coalesce(sum(risk), 0) as r from reservation where account_id = %s and episode_id = 0", (account_id,)).fetchone()
    total = Decimal((open_risk or {}).get("r") or 0) + Decimal((reserved or {}).get("r") or 0) + risk
    total_limit = base * policy.max_open_risk
    values.update({"offenes_risiko_neu": f"{total:.2f}", "grenze_offenes_risiko": f"{total_limit:.2f}"})
    if total > total_limit * Decimal("1.0001"):
        return "REJECTED", {"reason": f"Offenes Risiko wäre {total:.2f} USD > Grenze {total_limit:.2f} USD (davon diese Position {risk:.2f} USD) – "
                                      "engeren Stop wählen", "values": values}

    tf_row = conn.execute("select timeframe from signal where strategy_version_id = %s and instrument_id = %s order by created_at desc limit 1",
                          (version_id, instrument_id)).fetchone()
    timeframe = str(tf_row["timeframe"]) if tf_row else DEFAULT_TIMEFRAME
    plan = strategy.exit_plan(Decision(now, Action.BUY, Regime.UNKNOWN, ["Zuordnung einer fremden Position"], entry=bid, stop=stop))
    trade_id = f"LA{cmd.id}"
    rec = TradeRec(id=trade_id, instrument_id=instrument_id, strategy_version_id=version_id, signal_id=None, timeframe=timeframe, opened_at=now,
                   qty=qty, entry_value=qty * bid, entry_fees=Decimal(0), planned_stop=stop, planned_risk=risk, plan=plan, stop=stop,
                   highest_close=bid, bars_held=0, managed_through=floor_time(now, timeframe))
    meta = {"adopted_at": now.isoformat(), "adopted_cost_basis_unknown": True, "adopted_bid": str(bid), "adopted_by": cmd.issued_by,
            "adopted_command": cmd.id}
    with conn.transaction():
        LiveRepo(conn).save_trade(account_id, LiveTrade(rec, meta=meta))
        conn.execute("insert into audit_event (actor, kind, object, data) values (%s, 'live.position.assigned', %s, %s)",
                     (ACTOR, f"trade:{trade_id}", Jsonb({**values, "instrument": instrument_id, "strategy": version_id, "command": cmd.id})))
        resolve_alert(conn, "live.foreign_position", account_id, instrument_id, "OPEN", now)
        raise_alert(conn, "INFO", "live.position_assigned", account_id, f"trade:{trade_id}", "ASSIGNED", f"Fremde Position zugeordnet: {instrument_id}",
                    f"{qty} {inst['base_asset']} werden jetzt von {version_id} verwaltet. Der Stop-Loss {stop} bei Kraken über die ganze Menge folgt "
                    "im selben Durchlauf. Das Ergebnis zählt ab dem Übernahmewert; die frühere Kostenbasis bleibt unbekannt.", now)
    return "DONE", {"trade_id": trade_id, "instrument_id": instrument_id, "strategy_version_id": version_id, "qty": str(qty),
                    "entry_value": str(qty * bid), "stop": str(stop), "risk": f"{risk:.2f}", "cost_basis_unknown": True}


def _ceil_tick(value: Decimal, tick: Decimal | None) -> Decimal:
    if tick is None or tick <= 0:
        return value.quantize(Decimal("0.00000001"))
    return (value / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def _register(conn: Conn, cmd: Command, now: datetime, ex: Exchange | None, fingerprint: str | None) -> tuple[str, dict[str, Any]]:
    """Konto anlegen erst nach bestandener Rechteprüfung: Abfragen ja, Handeln ja, Auszahlen nein."""
    if ex is None or fingerprint is None:
        return "REJECTED", {"reason": "Kraken-Schlüssel fehlen im Live-Prozess"}
    checks: dict[str, Any] = {}
    try:
        ex.balances()
        checks["query_funds"] = True
        ex.open_orders()
        checks["query_orders"] = True
    except ExchangeError as exc:
        return "REJECTED", {"reason": f"Lesezugriff fehlgeschlagen ({exc.code})", "checks": checks}
    try:
        ex.add_order(OrderRequest("XBTUSD", "buy", "limit", Decimal("0.0001"), "permcheck", price=Decimal(1), validate=True))
        checks["trade"] = True
    except PermissionDenied:
        return "REJECTED", {"reason": "Schlüssel ohne Recht «Create & Modify Orders»", "checks": checks}
    except Rejected as exc:
        checks["trade"] = True  # Recht vorhanden; nur die Testangaben wurden geprüft und abgelehnt
        checks["trade_validate"] = exc.code
    except ExchangeError as exc:
        return "REJECTED", {"reason": f"Handelsrecht nicht prüfbar ({exc.code})", "checks": checks}

    probe = ex.probe_withdraw_permission()
    if probe == "ALLOWED":
        raise_alert(conn, "CRITICAL", "live.key_withdraw", ACCOUNT_ID, "key", "WITHDRAW", "Schlüssel mit Auszahlungsrecht abgelehnt",
                    "Der Kraken-Schlüssel darf Auszahlungen auslösen. Schlüssel löschen und ohne «Withdraw Funds» neu erstellen.", now)
        return "REJECTED", {"reason": "Schlüssel hat Auszahlungsrecht – Verbindung verweigert", "checks": checks}
    if probe == "DENIED":
        withdraw_check = "API_PERMISSION_DENIED"
    elif cmd.params.get("withdraw_absent_confirmed") is True and _fresh_step_up(cmd.params, now):
        withdraw_check = "USER_CONFIRMED"
    else:
        return "REJECTED", {"reason": "Auszahlungsrecht nicht automatisch prüfbar – ausdrückliche Bestätigung mit Step-up nötig", "checks": checks}

    permissions = {**checks, "withdraw": False, "withdraw_check": withdraw_check, "key_fingerprint": fingerprint, "checked_at": now.isoformat()}
    name = str(cmd.params.get("name") or "Kraken Spot")[:60]
    ref = cmd.params.get("provider_account_ref")
    ref = None if ref is None else str(ref)[-8:]  # nie vollständige Kontonummern
    with conn.transaction():
        existing = conn.execute("select id from account where id = %s", (ACCOUNT_ID,)).fetchone()
        if existing is None:
            conn.execute("insert into account (id, mode, name, currency, created_at, provider, provider_account_ref, permissions) "
                         "values (%s, 'LIVE', %s, 'USD', %s, 'KRAKEN', %s, %s)", (ACCOUNT_ID, name, now, ref, Jsonb(permissions)))
            conn.execute("insert into autopilot (account_id, state, reason, updated_at) values (%s, 'SETUP', %s, %s)",
                         (ACCOUNT_ID, "Konto verbunden – Mandat fehlt", now))
        else:
            conn.execute("update account set permissions = %s where id = %s", (Jsonb(permissions), ACCOUNT_ID))
        conn.execute("insert into audit_event (actor, kind, object, data) values (%s, 'live.account.registered', %s, %s)",
                     (ACTOR, f"account:{ACCOUNT_ID}", Jsonb(permissions)))
    return "DONE", {"account_id": ACCOUNT_ID, "permissions": permissions}
