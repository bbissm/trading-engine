"""Live-Autopilot für Kraken Spot: ein zustandsloser Durchlauf je Minute (docs/06, Abschnitte 2–3; docs/11, E-1).

Reihenfolge je Konto (Wiederherstellungsreihenfolge docs/06, 3.5 – fest):
  1. Verbindung und Authentifizierung (Systemstatus, Kontostand)
  2. Abgleich: Fills seit letztem Stand, Orders, Bestände; fremde Positionen/Orders; manuelle Verkäufe
     (a) und Klärung von Orders im Zustand UNKNOWN über die cl_ord_id, ohne erneutes Senden (b)
  3. Bedienbefehle (Pause, Stopp, Schliessen, Notfall, Freigaben, Zuordnung fremder Positionen)
  4. Schutz jeder verwalteten Position prüfen und reparieren (Stop bei Kraken = gehaltene Menge) (c)
  5. Einstiegsorders gegen Gültigkeit und Zustand prüfen, abgelaufene/unerwünschte stornieren
  6. Verlustgrenzen neu bewerten
  7. Betreuung: Trailing (nur enger), synthetisches Ziel, strategische Exits (Stop stornieren → bestätigen → Exit) (d)
  8. Erst dann neue Einstiege – nur wenn alle Sperren gelten und dies kein Wiederherstellungsdurchlauf ist (e)
Kennzahlen (f) werden an den jeweiligen Stellen geschrieben. Jeder Schritt schreibt in eigenen Transaktionen und
ins Auditprotokoll. Ein Dead-Man-Switch (CancelAllOrdersAfter) wird bewusst nicht verwendet.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from ..adapters.pg_paper import PaperRepo, policy_from_json
from ..core import indicators as ind
from ..core.account import Account, Position, Reservation
from ..core.candles import Candle, floor_time
from ..core.costs import BPS, KRAKEN_SPOT_TIER1, plan_costs
from ..core.execution import WORKING, Fill, Mode, Order, OrderError, OrderRole, OrderState, OrderType, Side, apply_fill, transition
from ..core.exits import ManagedTrade, on_close
from ..core.paper import TradeRec
from ..core.regime import Regime, daily_regimes, regime_at
from ..core.risk import EntryRequest, RiskContext, RiskPolicy, check_entry, loss_limits
from ..core.signals import Action, Decision
from ..core.sizing import InstrumentSpec, size_position
from ..core.strategies import ACTIVE as STRATEGIES
from . import commands as live_commands
from .alerts import raise_alert, resolve_alert
from .exchange import (
    Exchange,
    ExchangeError,
    ExOrder,
    ExTrade,
    NotSent,
    OrderRequest,
    Quote,
    Rejected,
    Uncertain,
    asset_balance,
)
from .locks import FX_MAX_AGE_DAYS, AccountLocks, Mandate, account_lock_reasons, strategy_lock_reasons
from .repo import LiveOrder, LiveRepo, LiveTrade

log = logging.getLogger(__name__)
S = OrderState
ZURICH = ZoneInfo("Europe/Zurich")
PREFIX = "te"  # eigene cl_ord_id: "te" + 16 Hex-Zeichen = 18 ASCII-Zeichen (Kraken: ≤ 18)
DUST = Decimal("0.00000001")
MAX_SLIPPAGE = Decimal("0.003")  # Exit-Limit marktnah: höchstens 30 bp unter Bid
STOP_STATES = {"maintenance", "unknown"}
NO_NEW_ORDER_STATES = {"maintenance", "cancel_only", "post_only", "limit_only", "unknown"}


def cl_ord_id(intent_key: str) -> str:
    """Deterministische Client-Order-ID aus dem Idempotenzschlüssel (vor dem Senden gespeichert)."""
    return PREFIX + hashlib.sha256(intent_key.encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class LiveConfig:
    enabled: bool
    keys: bool
    fingerprint: str | None
    schema_version: int
    entry_deadline_s: int = 15
    max_signal_age: timedelta = timedelta(minutes=10)
    accept_window: timedelta = timedelta(minutes=15)


@dataclass(slots=True)
class Ctx:
    repo: LiveRepo
    ex: Exchange
    now: datetime
    cfg: LiveConfig
    account: dict[str, Any]
    instruments: dict[str, Any]
    orders: dict[str, LiveOrder] = field(default_factory=dict)
    trades: dict[str, LiveTrade] = field(default_factory=dict)
    balances: dict[str, Decimal] = field(default_factory=dict)
    quotes: dict[str, Quote] = field(default_factory=dict)
    system: str = "unknown"
    diffs: list[dict[str, Any]] = field(default_factory=list)
    foreign: dict[str, Decimal] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def account_id(self) -> str:
        return str(self.account["id"])

    def diff(self, kind: str, severity: str, **data: Any) -> None:
        self.diffs.append({"kind": kind, "severity": severity, **{k: (str(v) if isinstance(v, Decimal) else v) for k, v in data.items()}})

    def reload(self) -> None:
        self.orders = self.repo.working_orders(self.account_id)
        self.trades = self.repo.open_trades(self.account_id)

    def pair(self, instrument_id: str) -> str:
        return str(self.instruments[instrument_id].venue_symbol)

    def quote_ccy(self, instrument_id: str) -> str:
        return str(self.instruments[instrument_id].quote_currency)


# ───────────────────────────── Einstieg ─────────────────────────────


def run_tick(conn: Any, ex: Exchange | None, now: datetime, cfg: LiveConfig) -> dict[str, Any]:
    """Ein Live-Durchlauf. `ex` ist None, wenn keine Schlüssel vorhanden sind (dann geschieht nichts beim Anbieter)."""
    repo = LiveRepo(conn, now)
    if not cfg.enabled:
        return {"live": "disabled"}
    if ex is None:
        return {"live": "no-keys"}
    if not repo.try_lock():
        return {"live": "busy"}
    try:
        hb = repo.heartbeat()
        detail = dict((hb or {}).get("detail") or {})
        prev_ok = hb is not None and detail.get("ok") is True and now - hb["last_seen"] <= timedelta(minutes=3)
        resume: dict[str, str] = dict(detail.get("resume") or {})
        repo.set_heartbeat(now, cfg.schema_version, {**detail, "ok": False, "started_at": now.isoformat()})

        out: dict[str, Any] = {"live": "ran", "recovery": not prev_ok, "commands": _registration_commands(repo, ex, now, cfg)}
        accounts = repo.live_accounts()
        mine = [a for a in accounts if (a["permissions"] or {}).get("key_fingerprint") == cfg.fingerprint] or (accounts if len(accounts) == 1 else [])
        for acc in mine:
            out[acc["id"]] = _run_account(repo, ex, acc, now, cfg, prev_ok, resume)
        for acc in accounts:
            if acc not in mine:
                raise_alert(conn, "WARNING", "live.key_mismatch", acc["id"], "key", "MISMATCH", "Schlüssel passt zu keinem Live-Konto",
                            "Die Kraken-Schlüssel im Live-Prozess gehören zu keinem registrierten Konto. Konto neu registrieren.", now)
        repo.set_heartbeat(now, cfg.schema_version, {"ok": True, "finished_at": now.isoformat(), "resume": resume,
                                                     "summary": {k: v for k, v in out.items() if k != "commands"}})
        return out
    finally:
        repo.unlock()


def _registration_commands(repo: LiveRepo, ex: Exchange, now: datetime, cfg: LiveConfig) -> int:
    n = 0
    for cmd in live_commands.pending(repo.c, ("LIVE_ACCOUNT_REGISTER",)):
        status, result = live_commands.handle(repo.c, cmd, now, ex, cfg.fingerprint)
        live_commands.complete(repo.c, cmd, status, result, now)
        n += 1
    return n


def _run_account(repo: LiveRepo, ex: Exchange, acc: dict[str, Any], now: datetime, cfg: LiveConfig, prev_ok: bool, resume: dict[str, str]) -> dict[str, Any]:
    ctx = Ctx(repo, ex, now, cfg, acc, repo.instruments())
    aid = ctx.account_id
    state0 = repo.autopilot_state(aid)
    if not prev_ok and state0 != "RECOVERY":
        resume[aid] = state0
        repo.set_autopilot(aid, "RECOVERY", "Wiederherstellung: Abgleich und Schutz vor allem anderen", now)

    # 1. Verbindung und Authentifizierung
    try:
        ctx.system = ex.system_status()
    except ExchangeError:
        ctx.system = "unknown"
    try:
        ctx.balances = ex.balances()
    except ExchangeError as exc:
        if repo.autopilot_state(aid) != "RECOVERY":
            resume.setdefault(aid, repo.autopilot_state(aid))
            repo.set_autopilot(aid, "RECOVERY", f"Kraken nicht erreichbar ({exc.code})", now)
        repo.insert_reconciliation(aid, "ERROR", [{"kind": "UNREACHABLE", "severity": "ERROR", "code": exc.code}], now)
        raise_alert(repo.c, "WARNING", "live.unreachable", aid, "kraken", "DOWN", "Kraken nicht erreichbar",
                    f"Abgleich nicht möglich ({exc.code}). Keine neuen Einstiege. Die Stop-Loss-Orders bei Kraken bleiben bestehen.", now)
        return {"ok": False, "reason": exc.code}
    resolve_alert(repo.c, "live.unreachable", aid, "kraken", "DOWN", now)
    if ctx.system != "online":
        raise_alert(repo.c, "WARNING", "live.market_mode", aid, "kraken", ctx.system, f"Kraken im Modus {ctx.system}",
                    "In Wartungsmodi wird nicht gematcht: Stops können währenddessen nicht auslösen. Keine neuen Einstiege.", now)

    # 2. Abgleich (a) und Klärung UNKNOWN (b)
    ctx.reload()
    recon_status = _reconcile(ctx)
    _resolve_unknown(ctx)

    # 3. Bedienbefehle
    market = live_commands.MarketView(lambda i: _quote(ctx, i) if i in ctx.instruments else None, lambda: _equity(ctx)[0])
    for cmd in live_commands.pending(repo.c, live_commands.ACCOUNT_COMMANDS):
        if cmd.target not in (None, aid) and cmd.type != "ORDER_APPROVAL_DECIDE":
            continue
        status, result = live_commands.handle(repo.c, cmd, now, ex, cfg.fingerprint, market)
        live_commands.complete(repo.c, cmd, status, result, now)
        if cmd.type == live_commands.ASSIGN and status == "DONE":
            ctx.foreign.pop(str(result["instrument_id"]), None)  # ab jetzt verwaltet, nicht fremd (sonst doppelt im Eigenkapital)
    ctx.reload()

    mandate = _mandate(ctx, cfg)
    state = repo.autopilot_state(aid)

    # 4. Schutz (c)
    _protect_all(ctx, mandate)
    # 5. Einstiegsorders prüfen
    _check_entry_orders(ctx, state)
    # 6. Verlustgrenzen
    equity, invested = _equity(ctx)
    state = _loss_limits(ctx, equity, state, mandate)
    # 7. Betreuung (d) und daraus folgende Exits
    _manage(ctx)
    _protect_all(ctx, mandate)

    # Zustand nach Abgleich: FEHLER bei ungeklärter Abweichung, sonst Rückkehr aus WIEDERHERSTELLUNG/FEHLER
    state = repo.autopilot_state(aid)
    if recon_status == "ERROR":
        if state != "ERROR":
            resume.setdefault(aid, state if state not in ("RECOVERY",) else resume.get(aid, "ENTRIES_PAUSED"))
            repo.set_autopilot(aid, "ERROR", "Abgleich mit ungeklärter Abweichung – Einstiege blockiert", now)
    elif state in ("RECOVERY", "ERROR") and recon_status in ("OK", "DIFF"):
        back = resume.pop(aid, "ENTRIES_PAUSED")
        repo.set_autopilot(aid, back, "Wiederherstellung abgeschlossen (Abgleich → Schutz → Grenzen)", now)
        if state == "ERROR":
            resolve_alert(repo.c, "live.reconciliation", aid, "reconciliation", "ERROR", now)
    state = repo.autopilot_state(aid)
    if state == "WINDING_DOWN" and not ctx.trades and not ctx.orders:
        repo.set_autopilot(aid, "STOPPED", "Abwicklung abgeschlossen: Bestand 0 bestätigt, keine offenen Orders", now)
        state = "STOPPED"

    # 8. Einstiege (e)
    locks = AccountLocks(
        enabled=cfg.enabled, keys=cfg.keys, permissions=acc["permissions"], fingerprint=cfg.fingerprint, mandate=mandate,
        autopilot_state=state, recovery_tick=not prev_ok or state0 in ("RECOVERY", "ERROR"), last_recon_status=recon_status, last_recon_at=now,
        unknown_orders=sum(1 for o in ctx.orders.values() if o.order.state in (S.UNKNOWN, S.SUBMITTED)),
        test_ack_at=repo.test_ack_at(), fx_ok=_fx_ok(repo, now), supported_protection=ex.capabilities.supported_protection,
    )
    if ctx.system in NO_NEW_ORDER_STATES:
        reasons_extra = [f"Kraken im Modus {ctx.system}: keine neuen Orders"]
    else:
        reasons_extra = []
    entries = _entries(ctx, mandate, locks, reasons_extra, equity)

    if equity is not None:
        repo.snapshot(aid, now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0),
                      equity, asset_balance(ctx.balances, "USD"), invested)
    ctx.summary.update({"ok": True, "state": state, "reconciliation": recon_status, "open_trades": len(ctx.trades),
                        "working_orders": len(ctx.orders), "entries": entries, "system": ctx.system})
    return ctx.summary


# ───────────────────────────── Mandat ─────────────────────────────


def _mandate(ctx: Ctx, cfg: LiveConfig) -> Mandate | None:
    repo, now, aid = ctx.repo, ctx.now, ctx.account_id
    mandates = repo.active_mandates(aid)
    for older in mandates[1:]:
        repo.set_mandate_status(older.id, "ENDED", f"Durch neueres Mandat {mandates[0].id} ersetzt", now)
    m = mandates[0] if mandates else None
    state = repo.autopilot_state(aid)
    if m is not None and not repo.mandate_accepted(m.id):
        fresh = (
            m.activated_by is not None and m.step_up_at is not None and m.activated_at is not None
            and timedelta(0) <= m.activated_at - m.step_up_at <= timedelta(minutes=5)
            and now - m.activated_at <= cfg.accept_window
        )
        if not fresh:
            repo.set_mandate_status(m.id, "SUSPENDED", "Aktivierung ohne frische Step-up-Anmeldung (≤ 5 min) – bitte neu aktivieren", now)
            raise_alert(repo.c, "WARNING", "live.mandate", aid, f"mandate:{m.id}", "REJECTED", "Mandat nicht übernommen",
                        "Die Step-up-Anmeldung zur Aktivierung war älter als 5 Minuten oder fehlte. Mandat bitte neu aktivieren.", now)
            m = None
        else:
            repo.audit("live.mandate.accepted", f"mandate:{m.id}", {"account": aid, "level": m.autonomy_level, "budget": str(m.budget),
                                                                     "step_up_at": m.step_up_at.isoformat() if m.step_up_at else None})
            if m.protection not in ctx.ex.capabilities.supported_protection:
                repo.set_autopilot(aid, "SETUP", f"Schutzpolicy «{m.protection}» wird von Kraken nicht unterstützt – keine Ersatzorder", now)
            elif state in ("SETUP", "READY", "STOPPED", "ENTRIES_PAUSED"):
                repo.set_autopilot(aid, "ACTIVE", f"Mandat {m.id} aktiviert (Stufe {m.autonomy_level})", now)
    if m is not None and m.protection not in ctx.ex.capabilities.supported_protection:
        if repo.autopilot_state(aid) not in ("SETUP", "RECOVERY", "ERROR"):
            repo.set_autopilot(aid, "SETUP", f"Schutzpolicy «{m.protection}» wird von Kraken nicht unterstützt – keine Ersatzorder", now)
    if m is None and repo.autopilot_state(aid) in ("ACTIVE", "READY"):
        if ctx.trades:
            repo.set_autopilot(aid, "ENTRIES_PAUSED", "Kein aktives Mandat – Positionen werden weiter betreut", now)
        else:
            repo.set_autopilot(aid, "SETUP", "Kein aktives Mandat", now)
    return m


def _fx_ok(repo: LiveRepo, now: datetime) -> bool:
    d = repo.fx_date()
    if d is None:
        return False
    try:
        fixing = datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        return False
    return (now.date() - fixing).days <= FX_MAX_AGE_DAYS


# ───────────────────────────── Order-Hilfen ─────────────────────────────


def _advance(o: Order, target: OrderState, at: datetime) -> None:
    """Zustandswechsel über erlaubte Zwischenschritte (z. B. SUBMITTED → ACCEPTED → CANCELED)."""
    if o.state is target:
        return
    for path in ([target], [S.ACCEPTED, target], [S.UNKNOWN, target]):
        try:
            probe = Order(**{f: getattr(o, f) for f in ("id", "intent_key", "mode", "account_id", "instrument_id", "side", "type", "role", "qty",
                                                        "created_at")}, state=o.state)
            for step in path:
                transition(probe, step, at)
        except OrderError:
            continue
        for step in path:
            transition(o, step, at)
        return
    if target is S.EXPIRED:
        _advance(o, S.CANCELED, at)
        return
    raise OrderError(f"Kein Weg von {o.state.value} nach {target.value}")


def _new_order(ctx: Ctx, intent_key: str, instrument_id: str, side: Side, type_: OrderType, role: OrderRole, qty: Decimal, *, trade_id: str | None,
               strategy: str, signal_id: str | None, timeframe: str, limit: Decimal | None = None, stop: Decimal | None = None,
               valid_until: datetime | None = None, reason: str | None = None, plan: Any = None) -> LiveOrder:
    o = Order(id=cl_ord_id(intent_key), intent_key=intent_key, mode=Mode.LIVE, account_id=ctx.account_id, instrument_id=instrument_id, side=side,
              type=type_, role=role, qty=qty, created_at=ctx.now, limit_price=limit, stop_price=stop, valid_until=valid_until)
    transition(o, S.CHECKED, ctx.now)
    return LiveOrder(o, strategy, signal_id, timeframe, trade_id, plan, reason)


def _send(ctx: Ctx, lo: LiveOrder, req: OrderRequest) -> str:
    """Übermitteln: SUBMITTED wird **vor** dem Netzaufruf gespeichert. Ergebnis ACCEPTED | REJECTED | UNKNOWN."""
    repo, o = ctx.repo, lo.order
    transition(o, S.SUBMITTED, ctx.now)
    repo.save_order(lo, ctx.now)
    ctx.orders[o.id] = lo
    repo.audit("live.order.submit", f"order:{o.id}", {"intent": o.intent_key, "role": o.role.value, "side": o.side.value, "type": req.ordertype,
                                                     "qty": str(o.qty), "price": None if req.price is None else str(req.price)})
    started = time.monotonic()
    try:
        txid = ctx.ex.add_order(req)
    except (Rejected, NotSent) as exc:
        _advance(o, S.REJECTED, ctx.now)
        lo.reason = f"Abgelehnt: {exc.code}" if isinstance(exc, Rejected) else f"Nicht übermittelt: {exc.code}"
        repo.save_order(lo, ctx.now)
        repo.audit("live.order.rejected", f"order:{o.id}", {"code": exc.code})
        return "REJECTED"
    except Uncertain as exc:
        _advance(o, S.UNKNOWN, ctx.now)
        lo.reason = f"Antwort fehlt ({exc.code}) – Status wird über die Client-Order-ID geklärt, kein erneutes Senden"
        repo.save_order(lo, ctx.now)
        repo.audit("live.order.unknown", f"order:{o.id}", {"code": exc.code})
        raise_alert(repo.c, "WARNING", "live.order_unknown", ctx.account_id, f"order:{o.id}", "UNKNOWN", "Orderstatus unbekannt",
                    f"{o.side.value} {o.qty} {o.instrument_id}: keine Antwort von Kraken. Neue Einstiege sind blockiert, bis der Status geklärt ist.", ctx.now)
        return "UNKNOWN"
    ack_ms = int((time.monotonic() - started) * 1000)
    _advance(o, S.ACCEPTED, ctx.now)
    repo.save_order(lo, ctx.now)
    repo.audit("live.order.ack", f"order:{o.id}", {"txid": txid, "ms": ack_ms})
    repo.metric(ctx.account_id, o.id, ctx.now, order_to_ack_ms=ack_ms)
    ctx.orders[o.id] = lo
    return "ACCEPTED"


def _lookup(ctx: Ctx, lo: LiveOrder) -> tuple[ExOrder | None, dict[str, ExTrade]]:
    """Gezielte Abfrage einer Order über die cl_ord_id (offene und geschlossene) samt ihrer Trades."""
    hits = ctx.ex.open_orders(cl_ord_id=lo.order.id) + ctx.ex.closed_orders(cl_ord_id=lo.order.id)
    if not hits:
        return None, {}
    exo = hits[0]
    trades = {t.trade_id: t for t in ctx.ex.trades(start=lo.order.created_at - timedelta(minutes=5))} if exo.trade_ids else {}
    return exo, trades


# ───────────────────────────── Abgleich (a) ─────────────────────────────


def _reconcile(ctx: Ctx) -> str:
    repo, now = ctx.repo, ctx.now
    last = repo.last_reconciliation(ctx.account_id)
    starts = [o.order.created_at for o in ctx.orders.values()] + [last["at"] if last else now - timedelta(hours=1)]
    since = max(min(starts) - timedelta(minutes=10), now - timedelta(days=7))
    try:
        open_ = ctx.ex.open_orders()
        closed = ctx.ex.closed_orders(start=since)
        trades = {t.trade_id: t for t in ctx.ex.trades(start=since)}
    except ExchangeError as exc:
        repo.insert_reconciliation(ctx.account_id, "ERROR", [{"kind": "UNREACHABLE", "severity": "ERROR", "code": exc.code}], now)
        return "ERROR"

    by_cl: dict[str, list[ExOrder]] = {}
    for exo in open_ + closed:
        if exo.cl_ord_id:
            by_cl.setdefault(exo.cl_ord_id, []).append(exo)
    for cl, hits in by_cl.items():
        if len(hits) > 1 and cl.startswith(PREFIX):
            ctx.diff("DUPLICATE_CL_ORD_ID", "ERROR", cl_ord_id=cl, count=len(hits))

    for lo in list(ctx.orders.values()):
        if lo.order.state not in WORKING:
            continue
        found = by_cl.get(lo.order.id)
        if found:
            _sync(ctx, lo, found[0], trades)
        elif lo.order.state not in (S.UNKNOWN, S.SUBMITTED):
            single, extra = _lookup(ctx, lo)
            if single is not None:
                _sync(ctx, lo, single, extra)
            else:
                ctx.diff("ORDER_MISSING", "ERROR", order=lo.order.id, state=lo.order.state.value)

    known = set(ctx.orders) | {o for o in by_cl if repo.order(o) is not None}
    for exo in open_:
        if exo.cl_ord_id and exo.cl_ord_id.startswith(PREFIX):
            if exo.cl_ord_id not in known:
                ctx.diff("OWN_ORDER_UNKNOWN_LOCALLY", "ERROR", cl_ord_id=exo.cl_ord_id, pair=exo.pair)
        else:
            ctx.diff("FOREIGN_ORDER", "INFO", txid=exo.txid, pair=exo.pair, side=exo.side, vol=exo.vol, note="fremd – nicht verwaltet")
            raise_alert(repo.c, "WARNING", "live.foreign_order", ctx.account_id, f"order:{exo.txid}", "OPEN", "Fremde Order bei Kraken",
                        f"{exo.side} {exo.vol} {exo.pair} ohne TradingEngine-Kennung: wird angezeigt, nie verändert.", now)

    ctx.trades = repo.open_trades(ctx.account_id)
    ctx.orders = repo.working_orders(ctx.account_id)
    _holdings(ctx, by_cl)

    severities = {d["severity"] for d in ctx.diffs}
    status = "ERROR" if "ERROR" in severities else "DIFF" if "DIFF" in severities else "OK"
    snapshot = {"kind": "SNAPSHOT", "severity": "INFO", "system": ctx.system, "balances": {k: str(v) for k, v in sorted(ctx.balances.items()) if v != 0},
                "managed": {t.rec.instrument_id: str(t.rec.qty) for t in ctx.trades.values()},
                "foreign": {k: str(v) for k, v in ctx.foreign.items()}}
    repo.insert_reconciliation(ctx.account_id, status, [snapshot, *ctx.diffs], now)
    if status == "ERROR":
        raise_alert(repo.c, "CRITICAL", "live.reconciliation", ctx.account_id, "reconciliation", "ERROR", "Abgleich mit ungeklärter Abweichung",
                    "; ".join(f"{d['kind']}" for d in ctx.diffs if d["severity"] == "ERROR") + ". Einstiege blockiert; Schutz läuft weiter.", now)
    return status


def _holdings(ctx: Ctx, by_cl: dict[str, list[ExOrder]]) -> None:
    """Bestand bei Kraken ↔ verwaltete Trades: fremde Positionen, manuelle Verkäufe (docs/06, 3.4)."""
    repo, now = ctx.repo, ctx.now
    managed: dict[str, list[LiveTrade]] = {}
    for lt in ctx.trades.values():
        managed.setdefault(lt.rec.instrument_id, []).append(lt)
    seen_assets: set[str] = set()
    for inst in ctx.instruments.values():
        if inst.base_asset in seen_assets:
            continue
        seen_assets.add(inst.base_asset)
        held = asset_balance(ctx.balances, inst.base_asset)
        trades = [lt for i, lts in managed.items() if ctx.instruments[i].base_asset == inst.base_asset for lt in lts]
        mine = sum((lt.rec.qty for lt in trades), Decimal(0))
        # Fills unserer Verkaufsorders, die Kraken schon verbucht, wir aber noch nicht gesehen haben
        pending_sell = Decimal(0)
        for lo in ctx.orders.values():
            if lo.order.side is Side.SELL and ctx.instruments[lo.order.instrument_id].base_asset == inst.base_asset:
                hits = by_cl.get(lo.order.id)
                if hits:
                    pending_sell += max(hits[0].vol_exec - lo.order.filled_qty, Decimal(0))
        expected = mine - pending_sell
        if held > expected + DUST:
            ctx.foreign[inst.id] = held - max(expected, Decimal(0))
            ctx.diff("FOREIGN_POSITION", "INFO", instrument=inst.id, qty=held - max(expected, Decimal(0)), note="fremd – nicht verwaltet")
            raise_alert(repo.c, "WARNING", "live.foreign_position", ctx.account_id, inst.id, "OPEN", "Fremde Position bei Kraken",
                        f"{held - max(expected, Decimal(0))} {inst.base_asset} stammen nicht aus TradingEngine-Orders: angezeigt, im Budget und in der "
                        "Konzentration als belegt gezählt, nie gehandelt.", now)
        elif held < expected - DUST:
            if pending_sell:
                ctx.diff("FILL_PENDING", "DIFF", instrument=inst.id, note="Verkaufs-Fill bei Kraken verbucht, Details folgen")
                continue
            missing = expected - held
            for lt in sorted(trades, key=lambda t: t.rec.opened_at, reverse=True):
                if missing <= 0:
                    break
                cut = min(missing, lt.rec.qty)
                _reduce_manual(ctx, lt, cut)
                missing -= cut
            ctx.diff("MANUAL_SELL", "DIFF", instrument=inst.id, expected=expected, held=held, note="lokale Menge und Stop auf Rest angepasst")
            raise_alert(repo.c, "WARNING", "live.manual_sell", ctx.account_id, inst.id, "REDUCED", "Bestand kleiner als verwaltet",
                        f"Bei Kraken liegen {held} {inst.base_asset}, verwaltet waren {expected}. Position und Stop werden auf den Rest angepasst.", now)
    ctx.trades = repo.open_trades(ctx.account_id)


def _reduce_manual(ctx: Ctx, lt: LiveTrade, qty: Decimal) -> None:
    rec = lt.rec
    share = qty / rec.qty
    cost = rec.entry_value * share
    with ctx.repo.c.transaction():
        rec.entry_value -= cost
        lt.sold_cost += cost
        rec.planned_risk -= rec.planned_risk * share
        rec.qty -= qty
        lt.manual_qty += qty
        if rec.qty <= 0:
            rec.qty = Decimal(0)
            _close(ctx, lt, "Bestand bei Kraken null bestätigt (ausserhalb von TradingEngine verkauft)")
        ctx.repo.save_trade(ctx.account_id, lt)
        ctx.repo.audit("live.manual_sell", f"trade:{rec.id}", {"qty": str(qty), "remaining": str(rec.qty)})


def _sync(ctx: Ctx, lo: LiveOrder, exo: ExOrder, trades: dict[str, ExTrade]) -> None:
    """Zustand einer eigenen Order vom Anbieter übernehmen: neue Fills, Annahme, Fill, Storno, Ablauf."""
    repo, o, now = ctx.repo, lo.order, ctx.now
    missing = False
    with repo.c.transaction():
        for tid in exo.trade_ids:
            t = trades.get(tid)
            if t is None:
                missing = True
                continue
            _apply_fill(ctx, lo, t)
        if missing or o.filled_qty < exo.vol_exec:
            ctx.diff("FILL_PENDING", "DIFF", order=o.id, local=o.filled_qty, exchange=exo.vol_exec)
        elif exo.status in ("open", "pending"):
            if o.state in (S.SUBMITTED, S.UNKNOWN):
                _advance(o, S.PARTIALLY_FILLED if o.filled_qty > 0 else S.ACCEPTED, now)
        elif exo.status == "closed":
            if not o.is_terminal and o.filled_qty == exo.vol_exec == exo.vol and o.qty != exo.vol:
                o.qty = exo.vol  # Menge wurde beim Anbieter geändert (Amend) – gefüllt ist, was Kraken meldet
                _advance(o, S.FILLED, now)
        elif exo.status in ("canceled", "expired") and not o.is_terminal:
            was_requested = o.state is S.CANCEL_REQUESTED
            remaining = o.remaining_qty
            _advance(o, S.CANCELED if exo.status == "canceled" else S.EXPIRED, now)
            if o.role is OrderRole.ENTRY:
                repo.release(o.intent_key)  # erst die Bestätigung gibt Reserviertes frei
            if o.role is OrderRole.PROTECT and not was_requested:
                ctx.diff("STOP_CANCELED_EXTERNALLY", "DIFF", order=o.id, remaining=remaining)
                raise_alert(repo.c, "WARNING", "live.stop_canceled", ctx.account_id, f"order:{o.id}", "CANCELED", "Stop-Loss bei Kraken storniert",
                            "Die Schutzorder wurde ausserhalb von TradingEngine storniert oder ist abgelaufen; sie wird sofort ersetzt.", now)
            repo.audit("live.order." + exo.status, f"order:{o.id}", {"remaining": str(remaining), "requested": was_requested})
        repo.save_order(lo, now)
    if o.is_terminal:
        ctx.orders.pop(o.id, None)


def _apply_fill(ctx: Ctx, lo: LiveOrder, t: ExTrade) -> None:
    repo, o = ctx.repo, lo.order
    fill = Fill(f"kraken:{t.trade_id}", o.id, t.vol, t.price, t.fee, ctx.quote_ccy(o.instrument_id), t.time, simulated=False)
    if any(f.id == fill.id for f in o.fills) or repo.fill_exists(fill.id):
        return  # doppelt geliefertes Fill-Event ist wirkungslos
    if fill.qty > o.remaining_qty:
        ctx.diff("FILL_EXCEEDS_ORDER", "ERROR", order=o.id, fill=fill.id)
        return
    if o.state in (S.CHECKED, S.PREPARED):
        _advance(o, S.SUBMITTED, ctx.now)
    apply_fill(o, fill)
    repo.insert_fill(fill)
    repo.audit("live.fill", f"order:{o.id}", {"fill": fill.id, "qty": str(fill.qty), "price": str(fill.price), "fee": str(fill.fee)})
    if o.side is Side.BUY:
        _entry_fill(ctx, lo, fill)
    else:
        _exit_fill(ctx, lo, fill)
    raise_alert(repo.c, "INFO", "live.fill", ctx.account_id, f"fill:{fill.id}", "FILLED", f"Ausgeführt: {fill.qty} {o.instrument_id} zu {fill.price}",
                f"{'Kauf' if o.side is Side.BUY else 'Verkauf'} ({o.role.value}), Gebühr {fill.fee} {fill.fee_currency}.", ctx.now)


def _entry_fill(ctx: Ctx, lo: LiveOrder, fill: Fill) -> None:
    repo, o = ctx.repo, lo.order
    res = repo.reservations(ctx.account_id).get(o.intent_key)
    risk_part = Decimal(0)
    if res is not None and res["qty"] > 0:
        share = min(fill.qty / res["qty"], Decimal(1))
        risk_part = res["risk"] * share
        repo.update_reservation(o.intent_key, res["qty"] - fill.qty, res["cash"] - res["cash"] * share, res["risk"] - risk_part)
    assert lo.trade_id is not None and lo.plan is not None
    lt = ctx.trades.get(lo.trade_id) or repo.open_trades(ctx.account_id).get(lo.trade_id)
    if lt is None:
        rec = TradeRec(id=lo.trade_id, instrument_id=o.instrument_id, strategy_version_id=lo.strategy_version_id, signal_id=lo.signal_id,
                       timeframe=lo.timeframe, opened_at=fill.time, qty=Decimal(0), entry_value=Decimal(0), entry_fees=Decimal(0),
                       planned_stop=lo.plan.stop, planned_risk=Decimal(0), plan=lo.plan, stop=lo.plan.stop, highest_close=fill.price, bars_held=0,
                       managed_through=floor_time(o.created_at, lo.timeframe))
        lt = LiveTrade(rec)
    lt.rec.qty += fill.qty
    lt.rec.entry_value += fill.qty * fill.price
    lt.rec.entry_fees += fill.fee
    lt.rec.planned_risk += risk_part
    lt.meta["last_entry_fill"] = fill.time.isoformat()
    ctx.trades[lt.rec.id] = lt
    repo.save_trade(ctx.account_id, lt)
    if o.limit_price:
        repo.metric(ctx.account_id, o.id, ctx.now, slippage_bps=((fill.price - o.limit_price) / o.limit_price * BPS).quantize(Decimal("0.0001")))


def _exit_fill(ctx: Ctx, lo: LiveOrder, fill: Fill) -> None:
    repo = ctx.repo
    lt = ctx.trades.get(lo.trade_id or "") or repo.open_trades(ctx.account_id).get(lo.trade_id or "")
    if lt is None:
        ctx.diff("SELL_WITHOUT_TRADE", "ERROR", order=lo.order.id)
        return
    rec = lt.rec
    qty = min(fill.qty, rec.qty)
    share = qty / rec.qty if rec.qty else Decimal(1)
    cost = rec.entry_value * share
    rec.entry_value -= cost
    lt.sold_cost += cost
    rec.planned_risk -= rec.planned_risk * share
    rec.qty -= qty
    rec.exit_value = (rec.exit_value or Decimal(0)) + fill.qty * fill.price
    rec.exit_fees = (rec.exit_fees or Decimal(0)) + fill.fee
    if rec.qty <= 0:
        if lo.order.role is OrderRole.PROTECT:
            reason = "Stop-Loss bei Kraken ausgelöst"
        else:
            reason = rec.exit_reason or lo.reason or "Ausstieg"
        _close(ctx, lt, reason)
    elif lo.order.state is S.FILLED or lo.order.is_terminal:
        ctx.orders.pop(lo.order.id, None)
    repo.save_trade(ctx.account_id, lt)


def _close(ctx: Ctx, lt: LiveTrade, reason: str) -> None:
    rec = lt.rec
    rec.status, rec.closed_at, rec.exit_reason = "CLOSED", ctx.now, reason
    rec.entry_value = lt.sold_cost + rec.entry_value  # Einstandswert der gesamten Position
    rec.qty = Decimal(0)
    if lt.manual_qty > 0:
        rec.net = None  # Erlös des manuellen Verkaufs ist nicht bekannt → Ergebnis nicht bestimmbar
    else:
        rec.net = (rec.exit_value or Decimal(0)) - rec.entry_value - rec.entry_fees - (rec.exit_fees or Decimal(0))
    ctx.trades.pop(rec.id, None)
    ctx.repo.audit("live.trade.closed", f"trade:{rec.id}", {"reason": reason, "net": None if rec.net is None else str(rec.net)})
    resolve_alert(ctx.repo.c, "live.protection", ctx.account_id, rec.id, "MISSING", ctx.now)


# ───────────────────────────── Klärung UNKNOWN (b) ─────────────────────────────


def _resolve_unknown(ctx: Ctx) -> None:
    repo, now = ctx.repo, ctx.now
    for lo in list(ctx.orders.values()):
        o = lo.order
        if o.state not in (S.UNKNOWN, S.SUBMITTED):
            continue
        if o.state is S.SUBMITTED:
            _advance(o, S.UNKNOWN, now)  # Absturz nach dem Speichern von SUBMITTED: Ergebnis offen
            lo.reason = "Übermittlung ohne gespeicherte Antwort – Status wird über die Client-Order-ID geklärt"
            repo.save_order(lo, now)
        try:
            exo, trades = _lookup(ctx, lo)
        except ExchangeError:
            continue
        if exo is not None:
            _sync(ctx, lo, exo, trades)
            repo.audit("live.unknown.resolved", f"order:{o.id}", {"status": exo.status, "txid": exo.txid})
            resolve_alert(repo.c, "live.order_unknown", ctx.account_id, f"order:{o.id}", "UNKNOWN", now)
            continue
        sent_at = _sent_at(repo, o.id) or o.created_at
        if now <= sent_at + timedelta(seconds=ctx.cfg.entry_deadline_s):
            continue  # Deadline noch nicht abgelaufen: Order könnte noch ankommen
        repo.audit("live.unknown.negative", f"order:{o.id}", {"at": now.isoformat()})
        if repo.negative_answers(o.id) >= 2:
            with repo.c.transaction():
                _advance(o, S.REJECTED, now)
                lo.reason = "Nicht platziert: zwei übereinstimmende Negativabfragen nach Ablauf der Deadline (kein automatischer Neuversuch)"
                repo.save_order(lo, now)
                if o.role is OrderRole.ENTRY:
                    repo.release(o.intent_key)
                repo.audit("live.unknown.not_placed", f"order:{o.id}", {})
            ctx.orders.pop(o.id, None)
            resolve_alert(repo.c, "live.order_unknown", ctx.account_id, f"order:{o.id}", "UNKNOWN", now)


def _sent_at(repo: LiveRepo, order_id: str) -> datetime | None:
    row = repo.c.execute("select max(ts) as t from audit_event where kind = 'live.order.submit' and object = %s", (f"order:{order_id}",)).fetchone()
    return None if row is None else row["t"]


# ───────────────────────────── Schutz (c) und Exits ─────────────────────────────


def _sells(ctx: Ctx, trade_id: str, role: OrderRole | None = None) -> list[LiveOrder]:
    return [lo for lo in ctx.orders.values() if lo.trade_id == trade_id and lo.order.side is Side.SELL and not lo.order.is_terminal
            and (role is None or lo.order.role is role)]


def _protect_all(ctx: Ctx, mandate: Mandate | None) -> None:
    for lt in list(ctx.trades.values()):
        if lt.rec.status != "OPEN" or lt.rec.qty <= 0:
            continue
        if lt.rec.exit_reason:
            _exit(ctx, lt, mandate)
        else:
            _ensure_stop(ctx, lt, mandate)


def _ensure_stop(ctx: Ctx, lt: LiveTrade, mandate: Mandate | None) -> None:
    """Genau eine Stop-Loss-Order bei Kraken über die gehaltene Menge (minus laufender Exit-Mengen)."""
    repo, rec, now = ctx.repo, lt.rec, ctx.now
    exits_open = sum((lo.order.remaining_qty for lo in _sells(ctx, rec.id, OrderRole.EXIT)), Decimal(0))
    want = rec.qty - exits_open
    stops = [lo for lo in _sells(ctx, rec.id, OrderRole.PROTECT)]
    if any(lo.order.state in (S.UNKNOWN, S.SUBMITTED, S.CANCEL_REQUESTED) for lo in stops):
        return  # Klärung läuft; keine zweite Verkaufsorder (Summe offener Verkäufe ≤ Bestand)
    if want <= 0:
        for lo in stops:
            _cancel(ctx, lo, "Kein ungesicherter Bestand")
        return
    for extra in stops[1:]:
        _cancel(ctx, extra, "Mehr als eine Schutzorder")
    stop = stops[0] if stops else None
    if stop is not None and stop.order.state in WORKING:
        changes: dict[str, Decimal] = {}
        if stop.order.remaining_qty != want:
            changes["order_qty"] = stop.order.filled_qty + want
        if stop.order.stop_price is not None and rec.stop > stop.order.stop_price:
            changes["trigger_price"] = rec.stop  # nur enger, nie weiter
        if not changes:
            return
        if ctx.system != "online":
            return
        try:
            ctx.ex.amend_order(stop.order.id, order_qty=changes.get("order_qty"), trigger_price=changes.get("trigger_price"))
        except ExchangeError as exc:
            repo.audit("live.protect.amend_failed", f"order:{stop.order.id}", {"code": exc.code})
            if _cancel(ctx, stop, "Ersetzen nach fehlgeschlagener Änderung") and rec.status == "OPEN":
                _ensure_stop(ctx, lt, mandate)
            return
        if "order_qty" in changes:
            stop.order.qty = changes["order_qty"]
        if "trigger_price" in changes:
            stop.order.stop_price = changes["trigger_price"]
        repo.save_order(stop, now)
        repo.audit("live.protect.amended", f"order:{stop.order.id}", {k: str(v) for k, v in changes.items()})
        return

    # Kein Stop vorhanden → setzen (im Durchlauf bis zu zwei Versuche)
    if ctx.system in NO_NEW_ORDER_STATES:
        raise_alert(repo.c, "CRITICAL", "live.protection", ctx.account_id, rec.id, "MISSING", f"Stop-Loss fehlt: {rec.instrument_id}",
                    f"Kraken im Modus {ctx.system}: die Schutzorder kann gerade nicht gesetzt werden. Sie wird sofort nach Ende der Wartung gesetzt.", now)
        return
    for _attempt in range(2):
        n = repo.count_protect_orders(rec.id, "PROTECT") + 1
        lo = _new_order(ctx, f"L:{rec.id}:PROTECT:{n}", rec.instrument_id, Side.SELL, OrderType.STOP, OrderRole.PROTECT, want, trade_id=rec.id,
                        strategy=rec.strategy_version_id, signal_id=rec.signal_id, timeframe=rec.timeframe, stop=rec.stop, reason="Stop-Loss bei Kraken")
        if not repo.insert_intent(lo, now):
            return
        result = _send(ctx, lo, OrderRequest(ctx.pair(rec.instrument_id), "sell", "stop-loss", want, lo.order.id, price=rec.stop))
        if result == "ACCEPTED":
            lt.protect_failures = 0
            repo.save_trade(ctx.account_id, lt)
            last = lt.meta.get("last_entry_fill")
            if last:
                ms = int((now - datetime.fromisoformat(last)).total_seconds() * 1000)
                repo.metric(ctx.account_id, lo.order.id, now, fill_to_protect_ms=max(ms, 0))
                lt.meta.pop("last_entry_fill", None)
                repo.save_trade(ctx.account_id, lt)
            resolve_alert(repo.c, "live.protection", ctx.account_id, rec.id, "MISSING", now)
            return
        if result == "UNKNOWN":
            return
        lt.protect_failures += 1
        repo.save_trade(ctx.account_id, lt)
    raise_alert(repo.c, "CRITICAL", "live.protection", ctx.account_id, rec.id, "MISSING", f"Stop-Loss nicht herstellbar: {rec.instrument_id}",
                "Zwei Versuche, die Schutzorder zu setzen, sind gescheitert. Notfallpolicy: marktnaher Ausstieg; Einstiege pausiert.", now)
    if repo.autopilot_state(ctx.account_id) == "ACTIVE":
        repo.set_autopilot(ctx.account_id, "ENTRIES_PAUSED", f"Schutz für {rec.instrument_id} nicht herstellbar", now)
    rec.exit_reason = "Notfall: Schutzorder nicht herstellbar"
    repo.save_trade(ctx.account_id, lt)
    _exit(ctx, lt, mandate)


def _cancel(ctx: Ctx, lo: LiveOrder, reason: str) -> bool:
    """Storno anfragen und beim Anbieter bestätigen lassen. True, wenn die Order jetzt sicher nicht mehr arbeitet."""
    repo, o = ctx.repo, lo.order
    if o.is_terminal:
        return True
    if ctx.system == "maintenance":
        return False
    if o.state is not S.CANCEL_REQUESTED:
        if o.state in (S.UNKNOWN, S.SUBMITTED):
            return False
        _advance(o, S.CANCEL_REQUESTED, ctx.now)
        lo.reason = reason
        repo.save_order(lo, ctx.now)
        repo.audit("live.order.cancel_requested", f"order:{o.id}", {"reason": reason})
        try:
            ctx.ex.cancel_order(o.id)
        except ExchangeError as exc:
            repo.audit("live.order.cancel_error", f"order:{o.id}", {"code": exc.code})
    try:
        exo, trades = _lookup(ctx, lo)
    except ExchangeError:
        return False
    if exo is None:
        ctx.diff("ORDER_MISSING", "ERROR", order=o.id, state=o.state.value)
        return False
    _sync(ctx, lo, exo, trades)
    return o.is_terminal


def _exit(ctx: Ctx, lt: LiveTrade, mandate: Mandate | None) -> None:
    """Synthetischer bzw. strategischer Ausstieg: Stop stornieren → Bestätigung → Exit-Order (docs/06, 3.2/3.3)."""
    repo, rec, now = ctx.repo, lt.rec, ctx.now
    if _sells(ctx, rec.id, OrderRole.EXIT):
        return  # Exit läuft bereits
    for stop in _sells(ctx, rec.id, OrderRole.PROTECT):
        if not _cancel(ctx, stop, f"Ersetzt durch Ausstieg: {rec.exit_reason}"):
            return  # erst Bestätigung abwarten, dann Exit senden
    if rec.status != "OPEN" or rec.qty <= 0:
        return
    if ctx.system in NO_NEW_ORDER_STATES:
        _ensure_stop(ctx, lt, mandate)
        return
    target = bool(rec.exit_reason and rec.exit_reason.startswith("Ziel")) and rec.plan.target is not None
    n = repo.count_protect_orders(rec.id, "EXIT") + 1
    pair = ctx.pair(rec.instrument_id)
    if target:
        assert rec.plan.target is not None
        req_type, price, tif = "limit", rec.plan.target, "IOC"
    else:
        req_type, price, tif = "market", None, "GTC"
    lo = _new_order(ctx, f"L:{rec.id}:EXIT:{n}", rec.instrument_id, Side.SELL, OrderType.LIMIT if target else OrderType.MARKET, OrderRole.EXIT,
                    rec.qty, trade_id=rec.id, strategy=rec.strategy_version_id, signal_id=rec.signal_id, timeframe=rec.timeframe, limit=price,
                    reason=rec.exit_reason)
    if not repo.insert_intent(lo, now):
        return
    result = _send(ctx, lo, OrderRequest(pair, "sell", req_type, rec.qty, lo.order.id, price=price, timeinforce=tif,  # type: ignore[arg-type]
                                         deadline=now + timedelta(seconds=ctx.cfg.entry_deadline_s)))
    if result == "ACCEPTED":
        try:
            exo, trades = _lookup(ctx, lo)
            if exo is not None:
                _sync(ctx, lo, exo, trades)
        except ExchangeError:
            pass
    if rec.status == "OPEN" and lo.order.is_terminal:
        if target:
            rec.exit_reason = None  # Ziel nicht erreicht (IOC nicht gefüllt): weiter mit Stop betreuen
            repo.save_trade(ctx.account_id, lt)
        raise_alert(repo.c, "WARNING", "live.exit", ctx.account_id, rec.id, "RETRY", f"Ausstieg {rec.instrument_id} nicht vollständig",
                    f"{lo.reason or 'Exit-Order'}: {lo.order.state.value}. Stop-Loss wird wieder gesetzt, neuer Versuch im nächsten Durchlauf.", now)
        lt2 = ctx.trades.get(rec.id)
        if lt2 is not None:
            _ensure_stop(ctx, lt2, mandate)


# ───────────────────────────── Einstiegsorders, Verlustgrenzen ─────────────────────────────


def _check_entry_orders(ctx: Ctx, state: str) -> None:
    for lo in list(ctx.orders.values()):
        o = lo.order
        if o.role is not OrderRole.ENTRY or o.is_terminal:
            continue
        if o.state is S.CHECKED:
            continue  # wird im Einstiegsschritt gesendet oder verworfen
        expired = o.valid_until is not None and o.valid_until <= ctx.now
        # Wiederherstellung/Fehler: nur abgelaufene stornieren (docs/06, 3.5 Schritt 4); bei Pause/Stopp/Notfall alle
        if expired or state in ("ENTRIES_PAUSED", "WINDING_DOWN", "STOPPED", "EMERGENCY", "SETUP", "READY"):
            _cancel(ctx, lo, "Einstiegsorder abgelaufen" if expired else f"Einstiege gestoppt ({state})")


def _equity(ctx: Ctx) -> tuple[Decimal | None, Decimal]:
    """Eigenkapital = USD-Cash + verwaltete und fremde Bestände zum Bid. Fehlt ein Kurs, ist es unbekannt."""
    cash = asset_balance(ctx.balances, "USD")
    held: dict[str, Decimal] = dict(ctx.foreign)
    invested = Decimal(0)
    for lt in ctx.trades.values():
        held[lt.rec.instrument_id] = held.get(lt.rec.instrument_id, Decimal(0)) + lt.rec.qty
        invested += lt.rec.entry_value
    total = cash
    for instrument_id, qty in held.items():
        q = _quote(ctx, instrument_id)
        if q is None:
            return None, invested
        total += qty * q.bid
    return total, invested


def _quote(ctx: Ctx, instrument_id: str) -> Quote | None:
    pair = ctx.pair(instrument_id)
    if pair not in ctx.quotes:
        try:
            ctx.quotes[pair] = ctx.ex.ticker(pair)
        except ExchangeError:
            return None
    return ctx.quotes[pair]


def _day_week(now: datetime) -> tuple[datetime, datetime]:
    local = now.astimezone(ZURICH)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return day.astimezone(now.tzinfo), (day - timedelta(days=day.weekday())).astimezone(now.tzinfo)


def _risk_base(ctx: Ctx, equity: Decimal | None, mandate: Mandate | None) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    if equity is None:
        return None, None, None, None
    day, week = _day_week(ctx.now)
    aid = ctx.account_id
    day_start = ctx.repo.first_equity_since(aid, day) or equity
    week_start = ctx.repo.first_equity_since(aid, week) or equity
    peak = max(ctx.repo.peak_equity(aid) or equity, equity)
    return equity, day_start, week_start, peak


def _policy(mandate: Mandate | None) -> RiskPolicy:
    return policy_from_json(dict((mandate.policy if mandate else {}).get("risk") or {}))


def _loss_limits(ctx: Ctx, equity: Decimal | None, state: str, mandate: Mandate | None) -> str:
    if state != "ACTIVE" or equity is None:
        return state
    eq, day, week, peak = _risk_base(ctx, equity, mandate)
    policy = _policy(mandate)
    rc = RiskContext(equity=eq, day_start_equity=day, week_start_equity=week, peak_equity=peak, entries_today=0, consecutive_losses=0,
                     entries_allowed=True, data_fresh=True)
    reasons = loss_limits(rc, policy)
    if reasons:
        ctx.repo.set_autopilot(ctx.account_id, "ENTRIES_PAUSED", "Verlustgrenze: " + "; ".join(reasons), ctx.now)
        raise_alert(ctx.repo.c, "CRITICAL", "live.loss_limit", ctx.account_id, "limits", "BREACHED", "Verlustgrenze erreicht",
                    "; ".join(reasons) + ". Einstiege pausiert, offene Einstiegsorders werden storniert; Schutz und Exits bleiben aktiv.", ctx.now)
        _check_entry_orders(ctx, "ENTRIES_PAUSED")
        return "ENTRIES_PAUSED"
    return state


# ───────────────────────────── Betreuung (d) ─────────────────────────────


def _manage(ctx: Ctx) -> None:
    paper_repo = PaperRepo(ctx.repo.c)  # nur lesend: Kerzen
    for lt in list(ctx.trades.values()):
        rec = lt.rec
        if rec.exit_reason or rec.status != "OPEN":
            continue
        changed = False
        for row in ctx.repo.candles_after(rec.instrument_id, rec.timeframe, rec.managed_through, ctx.now):
            history = paper_repo.candles_until(rec.instrument_id, rec.timeframe, row["close_time"], 600)
            if not history:
                break
            candle = history[-1]
            regime = _regime(paper_repo, rec.instrument_id, candle.close_time, ctx.now)
            managed = ManagedTrade(rec.plan, rec.stop, rec.highest_close, rec.bars_held)
            reason = on_close(managed, candle, _atr(history), regime, ctx.instruments[rec.instrument_id].tick)
            if managed.stop < rec.stop:
                managed.stop = rec.stop  # nie weiter
            rec.stop, rec.highest_close, rec.bars_held, rec.managed_through = managed.stop, managed.highest_close, managed.bars_held, candle.close_time
            changed = True
            if reason:
                rec.exit_reason = reason
                break
        if not rec.exit_reason and rec.plan.target is not None:
            q = _quote(ctx, rec.instrument_id)
            if q is not None and q.bid >= rec.plan.target:
                rec.exit_reason = "Ziel erreicht (von TradingEngine überwacht, nicht von Kraken)"
                changed = True
        if changed:
            ctx.repo.save_trade(ctx.account_id, lt)
            ctx.repo.audit("live.trade.managed", f"trade:{rec.id}", {"stop": str(rec.stop), "exit": rec.exit_reason})


def _atr(history: list[Candle]) -> float | None:
    return ind.atr([float(c.high) for c in history], [float(c.low) for c in history], [float(c.close) for c in history], 14)[-1]


def _regime(paper_repo: PaperRepo, instrument_id: str, at: datetime, now: datetime) -> Regime:
    points = daily_regimes(paper_repo.candles_until(instrument_id, "1d", now, 600))
    return regime_at(points, at)


# ───────────────────────────── Einstiege (e) ─────────────────────────────


def _entries(ctx: Ctx, mandate: Mandate | None, locks: AccountLocks, extra: list[str], equity: Decimal | None) -> int:
    repo, now, aid = ctx.repo, ctx.now, ctx.account_id
    # Abgelaufene Freigabeanfragen (Stufe 2): Schweigen ist keine Erlaubnis
    for a in repo.approvals(aid, "PENDING"):
        if a["expires_at"] <= now:
            repo.set_approval(a["id"], "EXPIRED", now, None)
            repo.outcome(str(a["signal_id"]), aid, "BLOCKED", ["Freigabe abgelaufen – keine Antwort ist keine Erlaubnis"], {}, now)
    if mandate is None or mandate.activated_at is None:
        _send_checked(ctx, mandate, ["Kein aktives Mandat"])
        return 0

    global_reasons = account_lock_reasons(locks, now) + extra
    if locks.recovery_tick:
        # Wiederherstellung: neue Signale verfallen (werden nicht nachgeholt). Gespeicherte, nie gesendete Absichten
        # bleiben stehen und werden im nächsten Durchlauf – nach erneuter Prüfung aller Sperren – gesendet oder verworfen.
        for sig in repo.new_buy_signals(aid, mandate.activated_at, list(mandate.strategy_version_ids)):
            repo.outcome(str(sig["id"]), aid, "BLOCKED", _dedupe(global_reasons), {}, now)
        return 0
    # Leftover-Absichten (Absturz nach dem Speichern, vor dem Senden) und neue Absichten gehen denselben Weg
    sent = _send_checked(ctx, mandate, global_reasons)

    candidates: list[tuple[dict[str, Any], bool]] = [(s, False) for s in repo.new_buy_signals(aid, mandate.activated_at, list(mandate.strategy_version_ids))]
    for a in repo.approvals(aid, "APPROVED"):
        intent_key = f"L:{mandate.id}:{a['signal_id']}:ENTRY"
        if repo.order(cl_ord_id(intent_key)) is None and not _has_outcome(repo, str(a["signal_id"]), aid):
            approved_sig = repo.signal(str(a["signal_id"]))
            if approved_sig is not None:
                candidates.append((approved_sig, True))
    if not candidates:
        return sent

    account = _risk_account(ctx, equity, mandate)
    policy = _policy(mandate)
    eq, day, week, peak = _risk_base(ctx, equity, mandate)
    eq_alloc = None if eq is None else min(eq, mandate.budget)
    if eq is not None and eq_alloc is not None and eq > 0:
        # Verlustgrenzen gelten relativ zum ganzen Konto; auf das Mandatsbudget skaliert bleiben die Quoten gleich
        scale = eq_alloc / eq
        day, week, peak = (None if v is None else v * scale for v in (day, week, peak))
    day_start, week_start = _day_week(now)
    entries_today = repo.entries_since(aid, day_start)
    by_id = {s.version.id: (n, s) for n, s in enumerate(STRATEGIES)}

    for sig, approved in sorted(candidates, key=lambda c: (by_id.get(c[0]["strategy_version_id"], (99, None))[0], -(c[0]["score"] or 0))):
        sid = str(sig["id"])
        reasons = list(global_reasons)
        if not approved and sig["created_at"] < now - ctx.cfg.max_signal_age:
            reasons.append("Signal verpasst – Live holt keine Signale nach")
        if sig["valid_until"] <= now:
            reasons.append("Signal abgelaufen")
        reasons += strategy_lock_reasons(sig["strategy_version_id"], sig["instrument_id"], mandate, repo.approved_live(sig["strategy_version_id"]),
                                         repo.gates(sig["strategy_version_id"]))
        strategy = by_id.get(sig["strategy_version_id"], (0, None))[1]
        inst = ctx.instruments.get(sig["instrument_id"])
        if strategy is None or inst is None or inst.tick is None or inst.min_qty is None or inst.min_notional is None:
            reasons.append("Strategie oder Instrument-Spezifikation unbekannt")
        if reasons:
            repo.outcome(sid, aid, "BLOCKED", _dedupe(reasons), {}, now)
            continue
        assert strategy is not None and inst is not None and inst.tick is not None and inst.min_qty is not None and inst.min_notional is not None
        quote = _quote(ctx, sig["instrument_id"])
        fresh = quote is not None and now - quote.at <= timedelta(seconds=policy.max_quote_age_s) and repo.feed_ok(sig["instrument_id"], sig["timeframe"])
        spread = None if quote is None or quote.bid <= 0 else ((quote.ask - quote.bid) / ((quote.ask + quote.bid) / 2) * BPS).quantize(Decimal("0.01"))

        entry, stop, target = Decimal(sig["entry"]), Decimal(sig["stop"]), None if sig["target"] is None else Decimal(sig["target"])
        decision = Decision(sig["candle_close"], Action.BUY, Regime(sig["regime"]), [], [], sig["score"], entry, stop, target, sig["max_hold_bars"], sig["ref_level"])
        plan = strategy.exit_plan(decision)
        model = KRAKEN_SPOT_TIER1
        costs = plan_costs(model, entry, stop, target)
        if target is not None and (costs.net_reward_risk is None or costs.net_reward_risk < 1):
            repo.outcome(sid, aid, "BLOCKED", ["Netto-Chance-Risiko nach Kosten unter 1"], {}, now)
            continue
        if eq_alloc is None:
            repo.outcome(sid, aid, "BLOCKED", ["Eigenkapital unbekannt (Kurs oder Kontostand fehlt)"], {}, now)
            continue
        fee_rate = model.maker_bps / BPS
        free = account.free_cash - eq_alloc * policy.cash_reserve
        spec = InstrumentSpec(inst.tick, Decimal("0.00000001"), inst.min_qty, inst.min_notional)
        sizing = size_position(eq_alloc * policy.risk_per_trade * Decimal(str(strategy.risk_factor)), min(eq_alloc * policy.max_instrument_share, free / (1 + fee_rate)),
                               entry, stop, model, spec)
        if not sizing.ok:
            repo.outcome(sid, aid, "BLOCKED", [sizing.reason or "Positionsgrösse nicht bestimmbar"], {}, now)
            continue
        intent_key = f"L:{mandate.id}:{sid}:ENTRY"
        losses = _loss_streak(repo, aid, sig["strategy_version_id"], policy, now)
        req = EntryRequest(intent_key, sig["instrument_id"], sig["strategy_version_id"], sizing.qty, entry, sizing.planned_risk, sizing.notional * (1 + fee_rate))
        rc = RiskContext(equity=eq_alloc, day_start_equity=day, week_start_equity=week, peak_equity=peak, entries_today=entries_today,
                         consecutive_losses=losses, entries_allowed=locks.autopilot_state == "ACTIVE", data_fresh=fresh,
                         reconciliation_ok=locks.last_recon_status == "OK", unknown_orders=locks.unknown_orders, spread_bps=spread, require_spread=True)
        verdict = check_entry(req, account, rc, policy)
        values = {**verdict.values, "menge": f"{sizing.qty}", "geplantes_risiko": f"{sizing.planned_risk:.2f}", "budget_mandat": f"{mandate.budget}",
                  "kostenmodell": model.version}
        if not verdict.allowed:
            repo.outcome(sid, aid, "BLOCKED", verdict.reasons, values, now)
            continue

        if mandate.autonomy_level == 2 and not approved:
            intent = {"instrument_id": sig["instrument_id"], "qty": str(sizing.qty), "limit": str(entry), "stop": str(stop), "strategy": sig["strategy_version_id"],
                      "planned_risk": str(sizing.planned_risk), "notional": str(sizing.notional), "mandate_id": mandate.id}
            approval_id = repo.create_order_approval(aid, sid, intent, sig["valid_until"], now)
            raise_alert(repo.c, "SIGNAL", "live.approval", aid, f"approval:{approval_id}", "PENDING",
                        f"Order vorbereitet – wartet auf deine Freigabe bis {sig['valid_until'].astimezone(ZURICH):%H:%M}",
                        f"Kauf {sizing.qty} {sig['instrument_id']} limit {entry}, Stop {stop}. Freigabe nur in der Web-App mit Anmeldung.", now)
            continue

        trade_id = f"L{mandate.id}-{sid}"
        lo = _new_order(ctx, intent_key, sig["instrument_id"], Side.BUY, OrderType.LIMIT, OrderRole.ENTRY, sizing.qty, trade_id=trade_id,
                        strategy=sig["strategy_version_id"], signal_id=sid, timeframe=sig["timeframe"], limit=entry, valid_until=sig["valid_until"],
                        reason="Einstieg (Limit, kurze Laufzeit)", plan=plan)
        with repo.c.transaction():
            repo.lock_account(aid)  # Risikoprüfung und Reservierung unter Kontosperre
            if not repo.insert_intent(lo, now):
                continue  # Absicht existiert schon (zweiter Prozess / Wiederholung)
            repo.reserve(intent_key, aid, sig["instrument_id"], sizing.qty, req.cash_needed, sizing.planned_risk)
            repo.outcome(sid, aid, "ORDERED", [], values, now)
            repo.audit("live.intent", f"order:{lo.order.id}", {"intent": intent_key, "signal": sid, "values": values})
        account.reserve(intent_key, sig["instrument_id"], sizing.qty, req.cash_needed, sizing.planned_risk)
        ctx.orders[lo.order.id] = lo
        entries_today += 1
        sig_ms = int((now - sig["created_at"]).total_seconds() * 1000)
        repo.metric(aid, lo.order.id, now, signal_to_order_ms=max(sig_ms, 0))
    return sent + _send_checked(ctx, mandate, global_reasons)


def _has_outcome(repo: LiveRepo, signal_id: str, account_id: str) -> bool:
    return repo.c.execute("select 1 from signal_outcome where signal_id = %s and account_id = %s and episode_id = 0", (signal_id, account_id)).fetchone() is not None


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _send_checked(ctx: Ctx, mandate: Mandate | None, global_reasons: list[str]) -> int:
    """Gespeicherte, noch nicht gesendete Einstiegsabsichten senden – nur wenn alle Sperren gelten; sonst verwerfen."""
    repo, now = ctx.repo, ctx.now
    sent = 0
    for lo in list(ctx.orders.values()):
        o = lo.order
        if o.role is not OrderRole.ENTRY or o.state is not S.CHECKED:
            continue
        reasons = list(global_reasons)
        if o.valid_until is not None and o.valid_until <= now + timedelta(seconds=10):
            reasons.append("Signal abgelaufen")
        if reasons:
            with repo.c.transaction():
                _advance(o, S.EXPIRED, now)
                lo.reason = "Nicht gesendet: " + "; ".join(_dedupe(reasons))[:400]
                repo.save_order(lo, now)
                repo.release(o.intent_key)
            ctx.orders.pop(o.id, None)
            continue
        expire = min(o.valid_until or now + timedelta(hours=4), now + timedelta(hours=8))
        req = OrderRequest(ctx.pair(o.instrument_id), "buy", "limit", o.qty, o.id, price=o.limit_price,
                           deadline=now + timedelta(seconds=ctx.cfg.entry_deadline_s), timeinforce="GTD", expiretm=expire)
        result = _send(ctx, lo, req)
        if result == "REJECTED":
            repo.release(o.intent_key)
            ctx.orders.pop(o.id, None)
            raise_alert(repo.c, "WARNING", "live.order_rejected", ctx.account_id, f"order:{o.id}", "REJECTED", f"Einstieg abgelehnt: {o.instrument_id}",
                        f"{lo.reason}. Kein automatischer Neuversuch.", now)
        elif result == "UNKNOWN":
            break  # weitere Einstiege des Kontos warten, bis der Status geklärt ist (Absichten bleiben gespeichert)
        elif result == "ACCEPTED":
            sent += 1
            raise_alert(repo.c, "INFO", "live.order_sent", ctx.account_id, f"order:{o.id}", "SENT", f"Order gesendet: Kauf {o.qty} {o.instrument_id}",
                        f"Limit {o.limit_price}, gültig bis {expire.astimezone(ZURICH):%d.%m. %H:%M}.", now)
    return sent


def _loss_streak(repo: LiveRepo, account_id: str, strategy_id: str, policy: RiskPolicy, now: datetime) -> int:
    recent = repo.recent_closed(account_id, strategy_id, policy.loss_streak_cooldown + 5)
    streak = 0
    for net, _ in recent:
        if net is None or net >= 0:
            break
        streak += 1
    if streak >= policy.loss_streak_cooldown and recent and now >= recent[0][1] + timedelta(hours=policy.loss_streak_cooldown_hours):
        streak = 0
    return streak


def _risk_account(ctx: Ctx, equity: Decimal | None, mandate: Mandate) -> Account:
    """Kontobuch für die Risikoprüfung: Mandatsbudget ist die Kontogrenze; fremde Bestände zählen als belegt."""
    usd = asset_balance(ctx.balances, "USD")
    invested_managed = sum((lt.rec.entry_value for lt in ctx.trades.values()), Decimal(0))
    foreign_value = Decimal(0)
    for instrument_id, qty in ctx.foreign.items():
        q = ctx.quotes.get(ctx.pair(instrument_id))
        foreign_value += qty * (q.bid if q else Decimal(0))
    cash = min(usd, mandate.budget - invested_managed - foreign_value)
    account = Account(ctx.account_id, "USD", max(cash, Decimal(0)))
    for lt in ctx.trades.values():
        account.positions[lt.rec.instrument_id] = Position(lt.rec.instrument_id, lt.rec.qty, lt.rec.entry_value, lt.rec.planned_risk, lt.rec.strategy_version_id)
    for instrument_id, qty in ctx.foreign.items():
        pos = account.positions.setdefault(instrument_id, Position(instrument_id, owner="fremd – nicht verwaltet"))
        q = ctx.quotes.get(ctx.pair(instrument_id))
        pos.qty += qty
        pos.cost += qty * (q.bid if q else Decimal(0))
        pos.owner = pos.owner or "fremd – nicht verwaltet"
    for key, r in ctx.repo.reservations(ctx.account_id).items():
        account.reservations[key] = Reservation(key, r["instrument_id"], r["qty"], r["cash"], r["risk"])
    return account

