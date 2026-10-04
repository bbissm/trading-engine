"""Bedienbefehle für den Live-Autopiloten (Tabelle `command`, verarbeitet nur im Live-Prozess).

Befehle ändern Zustände in der Datenbank; die Wirkung beim Anbieter (Stornos, Exits, Schutz) führt der nächste
Schritt des Live-Durchlaufs aus – zustandsgetrieben, damit ein Abbruch nichts halb erledigt zurücklässt:
Einstiegsorders werden storniert, sobald der Zustand nicht AKTIV ist; ein gesetzter `exit_reason` an einem offenen
Trade bedeutet «Ausstieg angefordert» (Stop stornieren → Bestätigung → Exit).

Mandatsaktivierung und Live-Freigabe einer Strategieversion schreibt die Web-App nach Step-up; der Live-Prozess
prüft die Frische des Step-ups bei der Übernahme (manager._mandate). `LIVE_CLOSE_ALL` und `LIVE_RESUME`
verlangen `params.step_up_at` (höchstens 5 Minuten alt).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ..ports import Command
from .alerts import raise_alert
from .exchange import Exchange, ExchangeError, OrderRequest, PermissionDenied, Rejected
from .locks import STEP_UP_MAX_AGE

Conn = psycopg.Connection[dict[str, Any]]
ACTOR = "engine:live"
REGISTER = "LIVE_ACCOUNT_REGISTER"
ACCOUNT_COMMANDS = ("LIVE_PAUSE", "LIVE_RESUME", "LIVE_STOP", "LIVE_CLOSE_ALL", "LIVE_EMERGENCY", "ORDER_APPROVAL_DECIDE", "MANDATE_SUSPEND")
LIVE_TYPES = (REGISTER, *ACCOUNT_COMMANDS)
ACCOUNT_ID = "live-kraken"


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


def handle(conn: Conn, cmd: Command, now: datetime, exchange: Exchange | None = None, fingerprint: str | None = None) -> tuple[str, dict[str, Any]]:
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

    return "REJECTED", {"reason": f"Unbekannter Befehl {cmd.type}"}


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
