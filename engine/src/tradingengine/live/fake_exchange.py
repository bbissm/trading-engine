"""Steuerbarer Kraken-Doppelgänger für Tests (docs/07, «Fake-Anbieter»). Kein Netzwerk, keine Zugangsdaten.

Skriptbar: Antwort verliert sich nach Annahme, Order kommt nie an, Ablehnung, Teilfüllung, verspäteter Fill
nach Stornoanfrage, Storno «pending», Wartungsmodi, fremde Orders/Positionen, manuelle Verkäufe, Ausfall
einzelner Methoden. Spot-Regeln wie beim echten Handelsplatz: Verkäufe nur bis zum Bestand (inkl. offener
Verkaufsorders), Käufe nur mit Guthaben. Doppelte `cl_ord_id` werden – wie dokumentiert nicht zugesichert –
**angenommen**, damit Tests beweisen, dass der Schutz vor Doppelorders auf unserer Seite liegt.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from .exchange import (
    ASSET_CODES,
    CancelResult,
    Capabilities,
    ExOrder,
    ExTrade,
    MarketRestricted,
    NotSent,
    OrderRequest,
    PermissionDenied,
    Probe,
    Quote,
    Rejected,
    SystemStatus,
    Unavailable,
    Uncertain,
)


@dataclass(slots=True)
class FakeOrder:
    txid: str
    cl_ord_id: str | None
    pair: str
    side: str
    ordertype: str
    vol: Decimal
    price: Decimal | None
    opentm: datetime
    status: str = "open"
    vol_exec: Decimal = Decimal(0)
    cost: Decimal = Decimal(0)
    trade_ids: list[str] = field(default_factory=list)
    closetm: datetime | None = None
    cancel_pending: bool = False
    expiretm: datetime | None = None
    timeinforce: str = "GTC"

    def view(self) -> ExOrder:
        avg = self.cost / self.vol_exec if self.vol_exec else Decimal(0)
        return ExOrder(
            txid=self.txid, cl_ord_id=self.cl_ord_id, pair=self.pair, side=self.side, ordertype=self.ordertype, status=self.status,  # type: ignore[arg-type]
            vol=self.vol, vol_exec=self.vol_exec, avg_price=avg,
            stopprice=self.price if self.ordertype == "stop-loss" else None,
            limitprice=self.price if self.ordertype == "limit" else None,
            trade_ids=tuple(self.trade_ids), opentm=self.opentm, closetm=self.closetm,
        )


class FakeKraken:
    capabilities = Capabilities()

    def __init__(self, clock: Callable[[], datetime] | None = None, balances: dict[str, Decimal] | None = None) -> None:
        self.clock = clock or (lambda: datetime.now(UTC))
        self.status: SystemStatus = "online"
        self.balance: dict[str, Decimal] = dict(balances or {"ZUSD": Decimal(10000)})
        self.orders: dict[str, FakeOrder] = {}
        self.trade_log: list[ExTrade] = []
        self.quotes: dict[str, Quote] = {}
        self.withdraw_probe: Probe = "DENIED"
        self.fee_rate = Decimal("0.004")
        self.calls: list[tuple[str, str]] = []
        # Skript: je Methode eine Liste von Verhalten für die nächsten Aufrufe
        #   "timeout_after_accept" | "lost" (nie angekommen, Timeout) | "not_sent" | "unavailable" | "reject:<Code>"
        self.script: dict[str, list[str]] = {}
        self.cancel_mode: str = "immediate"  # "immediate" | "pending"
        self.late_fill_on_cancel: Decimal | None = None  # Menge, die zwischen Stornoanfrage und Storno noch gefüllt wird
        self.hidden_trades: set[str] = set()  # Trades, die TradesHistory (noch) nicht liefert
        self.stale_quotes = False  # True: Ticker liefert den Zeitstempel von set_quote (veraltete Quote)
        self._seq = 0

    # --- Steuerung ----------------------------------------------------------------------------------
    def _next(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq:06d}"

    def _behaviour(self, method: str) -> str | None:
        queue = self.script.get(method)
        return queue.pop(0) if queue else None

    def _guard_read(self, method: str) -> None:
        self.calls.append((method, ""))
        if self.status == "maintenance":
            raise Unavailable("EService:Unavailable")
        b = self._behaviour(method)
        if b == "unavailable":
            raise Unavailable("EService:Unavailable")
        if b and b.startswith("reject:"):
            raise PermissionDenied(b[7:]) if "Permission" in b else Rejected(b[7:])

    def set_quote(self, pair: str, bid: str, ask: str | None = None) -> None:
        self.quotes[pair] = Quote(Decimal(bid), Decimal(ask or bid), self.clock())

    @staticmethod
    def _assets(pair: str) -> tuple[str, str]:
        base = pair[:-3]
        return ("BTC" if base == "XBT" else base), pair[-3:]

    @staticmethod
    def _code(asset: str) -> str:
        return ASSET_CODES.get(asset, (asset,))[0]

    def _bal(self, asset: str) -> Decimal:
        return sum((self.balance.get(c, Decimal(0)) for c in ASSET_CODES.get(asset, (asset,))), Decimal(0))

    def _add_bal(self, asset: str, delta: Decimal) -> None:
        code = self._code(asset)
        self.balance[code] = self.balance.get(code, Decimal(0)) + delta

    def fill(self, cl_ord_id_or_txid: str, qty: str | Decimal, price: str | Decimal, hidden: bool = False) -> str:
        """Füllt eine offene Order (auch im Zustand «Storno angefragt»). Rückgabe: Trade-ID."""
        o = self._find(cl_ord_id_or_txid)
        assert o is not None and o.status == "open", "nur offene Orders können gefüllt werden"
        q, p = Decimal(qty), Decimal(price)
        assert q <= o.vol - o.vol_exec
        base, quote = self._assets(o.pair)
        fee = (q * p * self.fee_rate).quantize(Decimal("0.00000001"))
        if o.side == "buy":
            self._add_bal(base, q)
            self._add_bal(quote, -(q * p + fee))
        else:
            assert self._bal(base) >= q, "Spot: Verkauf über Bestand"
            self._add_bal(base, -q)
            self._add_bal(quote, q * p - fee)
        tid = self._next("T")
        o.vol_exec += q
        o.cost += q * p
        o.trade_ids.append(tid)
        self.trade_log.append(ExTrade(tid, o.txid, o.pair, o.side, p, q, fee, self.clock()))
        if hidden:
            self.hidden_trades.add(tid)
        if o.vol_exec == o.vol:
            o.status, o.closetm = "closed", self.clock()
        return tid

    def trigger_stops(self, pair: str, price: str) -> list[str]:
        """Kurs fällt auf `price`: ausgelöste Stop-Loss-Orders werden zum Kurs gefüllt."""
        p = Decimal(price)
        if self.status != "online":
            return []  # in keinem Wartungsmodus wird gematcht
        out = []
        for o in list(self.orders.values()):
            if o.pair == pair and o.ordertype == "stop-loss" and o.status == "open" and o.price is not None and p <= o.price:
                out.append(self.fill(o.txid, o.vol - o.vol_exec, p))
        return out

    def manual_sell(self, asset: str, qty: str) -> None:
        """Verkauf ausserhalb von TradingEngine (z. B. in der Kraken-App)."""
        self._add_bal(asset, -Decimal(qty))

    def deposit_foreign(self, asset: str, qty: str) -> None:
        """Bestand, der nicht aus eigenen Orders stammt (fremde Position)."""
        self._add_bal(asset, Decimal(qty))

    def add_foreign_order(self, pair: str, side: str, qty: str, price: str) -> str:
        txid = self._next("OF")
        self.orders[txid] = FakeOrder(txid, None, pair, side, "limit", Decimal(qty), Decimal(price), self.clock())
        return txid

    def expire_orders(self) -> None:
        now = self.clock()
        for o in self.orders.values():
            if o.status == "open" and o.expiretm is not None and o.expiretm <= now:
                o.status, o.closetm = "expired", now

    def confirm_pending_cancels(self) -> None:
        for o in self.orders.values():
            if o.cancel_pending and o.status == "open":
                o.status, o.closetm, o.cancel_pending = "canceled", self.clock(), False

    def _find(self, key: str) -> FakeOrder | None:
        if key in self.orders:
            return self.orders[key]
        matches = [o for o in self.orders.values() if o.cl_ord_id == key]
        open_ = [o for o in matches if o.status == "open"]
        pool = open_ or matches
        return pool[-1] if pool else None

    def working(self, cl_ord_id: str | None = None) -> list[FakeOrder]:
        return [o for o in self.orders.values() if o.status == "open" and (cl_ord_id is None or o.cl_ord_id == cl_ord_id)]

    # --- Exchange-Schnittstelle ---------------------------------------------------------------------
    def system_status(self) -> SystemStatus:
        self.calls.append(("SystemStatus", ""))
        return self.status

    def balances(self) -> dict[str, Decimal]:
        self._guard_read("Balance")
        return dict(self.balance)

    def open_orders(self, cl_ord_id: str | None = None) -> list[ExOrder]:
        self._guard_read("OpenOrders")
        self.expire_orders()
        return [o.view() for o in self.orders.values() if o.status == "open" and (cl_ord_id is None or o.cl_ord_id == cl_ord_id)]

    def closed_orders(self, cl_ord_id: str | None = None, start: datetime | None = None) -> list[ExOrder]:
        self._guard_read("ClosedOrders")
        self.expire_orders()
        return [
            o.view() for o in self.orders.values()
            if o.status != "open" and (cl_ord_id is None or o.cl_ord_id == cl_ord_id) and (start is None or (o.closetm or o.opentm) >= start)
        ]

    def trades(self, start: datetime | None = None) -> list[ExTrade]:
        self._guard_read("TradesHistory")
        return [t for t in self.trade_log if t.trade_id not in self.hidden_trades and (start is None or t.time >= start)]

    def ticker(self, pair: str) -> Quote:
        self.calls.append(("Ticker", pair))
        if pair not in self.quotes:
            raise Unavailable("EQuery:Unknown asset pair")
        q = self.quotes[pair]
        return q if self.stale_quotes else Quote(q.bid, q.ask, self.clock())

    def probe_withdraw_permission(self) -> Probe:
        self.calls.append(("WithdrawMethods", ""))
        return self.withdraw_probe

    def add_order(self, req: OrderRequest) -> str:
        self.calls.append(("AddOrder", req.cl_ord_id))
        b = self._behaviour("AddOrder")
        if b == "not_sent":
            raise NotSent("ConnectError")
        if self.status == "maintenance":
            raise Uncertain("EService:Unavailable")
        if self.status in ("cancel_only", "post_only", "limit_only") and not req.validate:
            raise MarketRestricted(f"EService:Market in {self.status} mode")
        if b == "lost":
            raise Uncertain("ReadTimeout")  # Anfrage kam nie an (bzw. nach der Deadline) – nichts platziert
        if b and b.startswith("reject:"):
            raise Rejected(b[7:])
        base, quote = self._assets(req.pair)
        if req.side == "sell":
            reserved = sum((o.vol - o.vol_exec for o in self.working() if o.side == "sell" and self._assets(o.pair)[0] == base), Decimal(0))
            if reserved + req.volume > self._bal(base):
                raise Rejected("EOrder:Insufficient funds")
        else:
            price = req.price or (self.quotes[req.pair].ask if req.pair in self.quotes else Decimal(0))
            if req.volume * price > self._bal(quote):
                raise Rejected("EOrder:Insufficient funds")
        if req.validate:
            return "VALIDATED"
        txid = self._next("O")
        self.orders[txid] = FakeOrder(txid, req.cl_ord_id, req.pair, req.side, req.ordertype, req.volume, req.price, self.clock(),
                                      expiretm=req.expiretm, timeinforce=req.timeinforce)
        if req.ordertype == "market" and req.pair in self.quotes:
            q = self.quotes[req.pair]
            self.fill(txid, req.volume, q.bid if req.side == "sell" else q.ask)
        elif req.timeinforce == "IOC":
            iq = self.quotes.get(req.pair)
            if iq is not None and req.price is not None and ((req.side == "sell" and iq.bid >= req.price) or (req.side == "buy" and iq.ask <= req.price)):
                self.fill(txid, req.volume, req.price)
            if self.orders[txid].status == "open":
                self.orders[txid].status, self.orders[txid].closetm = "canceled", self.clock()
        if b == "timeout_after_accept":
            raise Uncertain("ReadTimeout")
        return txid

    def amend_order(self, cl_ord_id: str, order_qty: Decimal | None = None, trigger_price: Decimal | None = None) -> None:
        self.calls.append(("AmendOrder", cl_ord_id))
        if self.status != "online":
            raise MarketRestricted(f"EService:Market in {self.status} mode") if self.status != "maintenance" else Uncertain("EService:Unavailable")
        b = self._behaviour("AmendOrder")
        if b and b.startswith("reject:"):
            raise Rejected(b[7:])
        o = self._find(cl_ord_id)
        if o is None or o.status != "open":
            raise Rejected("EOrder:Unknown order")
        if order_qty is not None:
            if order_qty < o.vol_exec:
                raise Rejected("EGeneral:Invalid arguments:order_qty")
            o.vol = order_qty
        if trigger_price is not None:
            o.price = trigger_price

    def cancel_order(self, cl_ord_id: str) -> CancelResult:
        self.calls.append(("CancelOrder", cl_ord_id))
        if self.status == "maintenance":
            raise Uncertain("EService:Unavailable")
        b = self._behaviour("CancelOrder")
        if b == "timeout_after_accept":
            o = self._find(cl_ord_id)
            if o is not None and o.status == "open":
                o.status, o.closetm = "canceled", self.clock()
            raise Uncertain("ReadTimeout")
        if b and b.startswith("reject:"):
            raise Rejected(b[7:])
        o = self._find(cl_ord_id)
        if o is None or o.status != "open":
            raise Rejected("EOrder:Unknown order")
        if self.late_fill_on_cancel is not None:
            self.fill(o.txid, self.late_fill_on_cancel, o.price or Decimal(0))
            self.late_fill_on_cancel = None
            if o.status != "open":
                return CancelResult(0, False)
        if self.cancel_mode == "pending":
            o.cancel_pending = True
            return CancelResult(1, True)
        o.status, o.closetm = "canceled", self.clock()
        return CancelResult(1, False)
