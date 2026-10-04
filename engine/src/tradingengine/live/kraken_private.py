"""Privater Kraken-Spot-REST-Client (Live-Orderweg). Nur im Live-Prozess konstruierbar (siehe live/session.py).

Belegt auf docs.kraken.com (Stand 4.10.2026):
- Signatur: API-Sign = Base64(HMAC-SHA512(Base64-dekodiertes Secret, URI-Pfad + SHA256(nonce + POST-Daten))),
  Header `API-Key` und `API-Sign`; Pfad beginnt mit /0/private (guides/spot-rest-auth).
- Nonce: «always increasing, unsigned 64-bit integer for each request» je Schlüssel; zu viele ungültige Nonces
  können zu temporären Sperren führen. Hier: Mikrosekunden-Zeitstempel, im Prozess streng steigend. Über
  Aufrufe hinweg trägt die Uhr; Live-Ticks laufen per Datenbank-Sperre nie gleichzeitig. Ein dennoch zu kleiner
  Nonce führt zu `EAPI:Invalid nonce` = sichere Ablehnung (nichts platziert), nie zu einer Doppelorder.
- AddOrder: `cl_ord_id` (UUID oder ≤ 18 ASCII-Zeichen), `deadline` (RFC3339, now+2 s … now+60 s),
  `validate`, `timeinforce` GTC/IOC/GTD, `expiretm`. AmendOrder ändert Menge/Auslösepreis bei gleicher
  Kennung (EditOrder wird nicht verwendet: neue txid, kein cl_ord_id). CancelOrder per `cl_ord_id`.
- OpenOrders/ClosedOrders filtern per `cl_ord_id`; QueryOrders verlangt eine txid. TradesHistory liefert
  `ordertxid`, aber keine `cl_ord_id`.
- WithdrawMethods braucht die Rechte «Funds – Query» **und** «Funds – Withdraw» und liest nur.
- Fehler: EAPI:Invalid nonce, EAPI:Rate limit exceeded, EOrder:Rate limit exceeded, EGeneral:Permission denied,
  EService:Unavailable, EService:Market in cancel_only mode, EService:Deadline elapsed, EService: Throttled.

Zugangsdaten werden nie geloggt, nie in Fehlermeldungen übernommen und nie zurückgegeben.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import threading
import time
import urllib.parse
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from .exchange import (
    CancelResult,
    Capabilities,
    ExchangeError,
    ExOrder,
    ExTrade,
    InvalidNonce,
    MarketRestricted,
    NotSent,
    OrderRequest,
    PermissionDenied,
    Probe,
    Quote,
    RateLimited,
    Rejected,
    SystemStatus,
    Unavailable,
    Uncertain,
)

log = logging.getLogger(__name__)

BASE_URL = "https://api.kraken.com"
TIMEOUT = httpx.Timeout(connect=3.0, read=8.0, write=3.0, pool=3.0)


def sign(url_path: str, postdata: str, nonce: str, secret_b64: str) -> str:
    """Kraken-Signatur (reine Funktion, testbar mit dem Beispiel aus der Dokumentation)."""
    message = url_path.encode() + hashlib.sha256((nonce + postdata).encode()).digest()
    mac = hmac.new(base64.b64decode(secret_b64), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


class NonceSource:
    """Streng steigende Nonces (Mikrosekunden seit Epoche) – threadsicher innerhalb des Prozesses."""

    def __init__(self, clock: Any = time.time) -> None:
        self._clock = clock
        self._last = 0
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            candidate = int(self._clock() * 1_000_000)
            self._last = max(candidate, self._last + 1)
            return self._last


def map_errors(errors: list[str], write: bool) -> ExchangeError:
    """Fehlerliste des Handelsplatzes → Fehlerklasse. `write`: Anfrage verändert Orders (AddOrder/Amend/Cancel)."""
    code = errors[0] if errors else "EGeneral:Unknown"
    if code.startswith("EAPI:Invalid nonce"):
        return InvalidNonce(code)
    if "Rate limit exceeded" in code or code.replace(" ", "").startswith("EService:Throttled"):
        return RateLimited(code)
    if code.startswith("EGeneral:Permission denied"):
        return PermissionDenied(code)
    if code.startswith("EService:Market in"):
        return MarketRestricted(code)
    if code.startswith(("EService:Unavailable", "EService:Busy")):
        return Uncertain(code) if write else Unavailable(code)
    if code.startswith("EService:Deadline elapsed"):
        return Rejected(code)  # «timed out before the trading engine processed it»
    if code.startswith(("EOrder:", "EGeneral:", "EAPI:", "EQuery:", "EFunding:", "ETrade:")):
        return Rejected(code)
    return Uncertain(code) if write else Unavailable(code)


def _ts(value: Any) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    return datetime.fromtimestamp(float(value), tz=UTC)


def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    d = Decimal(str(value))
    return d if d != 0 else None


def parse_order(txid: str, o: dict[str, Any]) -> ExOrder:
    descr = o.get("descr") or {}
    return ExOrder(
        txid=txid,
        cl_ord_id=o.get("cl_ord_id") or None,
        pair=str(descr.get("pair", "")),
        side=str(descr.get("type", "")),
        ordertype=str(descr.get("ordertype", "")),
        status=o["status"],
        vol=Decimal(str(o.get("vol", "0"))),
        vol_exec=Decimal(str(o.get("vol_exec", "0"))),
        avg_price=Decimal(str(o.get("price", "0") or "0")),
        stopprice=_dec(o.get("stopprice")),
        limitprice=_dec(o.get("limitprice")),
        trade_ids=tuple(o.get("trades") or ()),
        opentm=_ts(o.get("opentm")),
        closetm=_ts(o.get("closetm")),
        reason=o.get("reason"),
    )


def _rfc3339(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _num(value: Decimal) -> str:
    return format(value.normalize(), "f")


class KrakenPrivate:
    """Echter Client. Konstruktion nur über live.session.open_live_exchange (Sperren geprüft)."""

    capabilities = Capabilities()

    def __init__(self, api_key: str, api_secret: str, client: httpx.Client | None = None, nonce: NonceSource | None = None) -> None:
        if not api_key or not api_secret:
            raise ValueError("Kraken-Zugangsdaten fehlen")
        self.__key = api_key
        self.__secret = api_secret
        self._http = client or httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, headers={"User-Agent": "tradingengine-live/0.1"})
        self._nonce = nonce or NonceSource()

    def __repr__(self) -> str:  # nie Schlüssel ausgeben
        return "KrakenPrivate(<redacted>)"

    __str__ = __repr__

    # --- Transport ----------------------------------------------------------------------------------
    def _private(self, method: str, data: dict[str, Any] | None = None, write: bool = False) -> Any:
        path = f"/0/private/{method}"
        payload = {k: v for k, v in (data or {}).items() if v is not None}
        nonce = str(self._nonce.next())
        postdata = urllib.parse.urlencode({"nonce": nonce, **payload})
        headers = {
            "API-Key": self.__key,
            "API-Sign": sign(path, postdata, nonce, self.__secret),
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        }
        try:
            resp = self._http.post(path, content=postdata, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            log.warning("Kraken %s: keine Verbindung (%s)", method, type(exc).__name__)
            raise NotSent(type(exc).__name__) from None
        except httpx.HTTPError as exc:
            log.warning("Kraken %s: Antwort fehlt (%s)", method, type(exc).__name__)
            raise (Uncertain if write else Unavailable)(type(exc).__name__) from None
        return self._unwrap(method, resp, write)

    def _public(self, method: str, params: dict[str, str] | None = None) -> Any:
        try:
            resp = self._http.get(f"/0/public/{method}", params=params)
        except httpx.HTTPError as exc:
            raise Unavailable(type(exc).__name__) from None
        return self._unwrap(method, resp, False)

    @staticmethod
    def _unwrap(method: str, resp: httpx.Response, write: bool) -> Any:
        if resp.status_code != 200:
            log.warning("Kraken %s: HTTP %s", method, resp.status_code)
            raise (Uncertain if write else Unavailable)(f"HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError:
            raise (Uncertain if write else Unavailable)("Antwort nicht lesbar") from None
        if body.get("error"):
            err = map_errors([str(e) for e in body["error"]], write)
            log.warning("Kraken %s: %s", method, err.code)
            raise err
        return body.get("result")

    # --- Lesen --------------------------------------------------------------------------------------
    def system_status(self) -> SystemStatus:
        result = self._public("SystemStatus")
        status = str((result or {}).get("status", "unknown"))
        return status if status in ("online", "maintenance", "cancel_only", "post_only", "limit_only") else "unknown"  # type: ignore[return-value]

    def balances(self) -> dict[str, Decimal]:
        result = self._private("Balance") or {}
        return {str(k): Decimal(str(v)) for k, v in result.items()}

    def open_orders(self, cl_ord_id: str | None = None) -> list[ExOrder]:
        result = self._private("OpenOrders", {"trades": "true", "cl_ord_id": cl_ord_id}) or {}
        return [parse_order(txid, o) for txid, o in (result.get("open") or {}).items()]

    def closed_orders(self, cl_ord_id: str | None = None, start: datetime | None = None) -> list[ExOrder]:
        data = {"trades": "true", "cl_ord_id": cl_ord_id, "start": None if start is None else str(int(start.timestamp())), "without_count": "true"}
        result = self._private("ClosedOrders", data) or {}
        return [parse_order(txid, o) for txid, o in (result.get("closed") or {}).items()]

    def trades(self, start: datetime | None = None) -> list[ExTrade]:
        result = self._private("TradesHistory", {"start": None if start is None else str(int(start.timestamp()))}) or {}
        out: list[ExTrade] = []
        for trade_id, t in (result.get("trades") or {}).items():
            out.append(
                ExTrade(
                    trade_id=str(trade_id), ordertxid=str(t["ordertxid"]), pair=str(t.get("pair", "")), side=str(t.get("type", "")),
                    price=Decimal(str(t["price"])), vol=Decimal(str(t["vol"])), fee=Decimal(str(t.get("fee", "0"))),
                    time=datetime.fromtimestamp(float(t["time"]), tz=UTC),
                )
            )
        return out

    def ticker(self, pair: str) -> Quote:
        result = self._public("Ticker", {"pair": pair}) or {}
        row = next(iter(result.values()))
        return Quote(bid=Decimal(str(row["b"][0])), ask=Decimal(str(row["a"][0])), at=datetime.now(UTC))

    def probe_withdraw_permission(self) -> Probe:
        """WithdrawMethods liest nur (bewegt keine Mittel) und verlangt das Auszahlungsrecht.
        Rechtefehler → Schlüssel hat kein Auszahlungsrecht. Erfolg → hat es. Alles andere → unklar."""
        try:
            self._private("WithdrawMethods")
        except PermissionDenied:
            return "DENIED"
        except ExchangeError:
            return "UNCLEAR"
        return "ALLOWED"

    # --- Schreiben ----------------------------------------------------------------------------------
    def add_order(self, req: OrderRequest) -> str:
        data: dict[str, Any] = {
            "pair": req.pair,
            "type": req.side,
            "ordertype": req.ordertype,
            "volume": _num(req.volume),
            "price": None if req.price is None else _num(req.price),
            "cl_ord_id": req.cl_ord_id,
            "timeinforce": req.timeinforce,
            "expiretm": None if req.expiretm is None else str(int(req.expiretm.timestamp())),
            "deadline": None if req.deadline is None else _rfc3339(req.deadline),
            "validate": "true" if req.validate else None,
        }
        result = self._private("AddOrder", data, write=True) or {}
        txids = result.get("txid") or []
        return str(txids[0]) if txids else ("VALIDATED" if req.validate else "")

    def amend_order(self, cl_ord_id: str, order_qty: Decimal | None = None, trigger_price: Decimal | None = None) -> None:
        data = {
            "cl_ord_id": cl_ord_id,
            "order_qty": None if order_qty is None else _num(order_qty),
            "trigger_price": None if trigger_price is None else _num(trigger_price),
        }
        self._private("AmendOrder", data, write=True)

    def cancel_order(self, cl_ord_id: str) -> CancelResult:
        result = self._private("CancelOrder", {"cl_ord_id": cl_ord_id}, write=True) or {}
        return CancelResult(int(result.get("count", 0)), bool(result.get("pending", False)))
