"""Schnittstelle zum Handelsplatz für den Live-Orderweg (Kraken Spot) – vom echten Client und vom Fake erfüllt.

Fehlerklassen tragen die für die Ausführung entscheidende Frage: *Ist die Order sicher nicht platziert?*

- `Rejected`   – der Handelsplatz hat die Anfrage abgelehnt; es ist sicher nichts platziert/geändert.
- `NotSent`    – die Verbindung kam nie zustande; die Anfrage hat den Handelsplatz nicht erreicht.
- `Uncertain`  – Anfrage gesendet, Antwort fehlt (Timeout, Abbruch, Dienst nicht verfügbar): Ergebnis offen.
                 Für AddOrder führt das zum Zustand UNKNOWN; es wird **nie** blind erneut gesendet.
- `Unavailable` – Lesezugriff nicht möglich (Wartung, Dienst nicht verfügbar).

Kein Objekt dieser Datei enthält Zugangsdaten.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

OrderStatus = Literal["pending", "open", "closed", "canceled", "expired"]
SystemStatus = Literal["online", "maintenance", "cancel_only", "post_only", "limit_only", "unknown"]
Probe = Literal["DENIED", "ALLOWED", "UNCLEAR"]


class ExchangeError(Exception):
    """Basisklasse. `code` ist der Fehlertext des Handelsplatzes (z. B. "EOrder:Insufficient funds"), nie ein Geheimnis."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code} {detail}".strip())
        self.code = code


class Rejected(ExchangeError):
    """Sicher nicht ausgeführt (Ablehnung durch den Handelsplatz)."""


class RateLimited(Rejected):
    pass


class InvalidNonce(Rejected):
    pass


class PermissionDenied(Rejected):
    pass


class MarketRestricted(Rejected):
    """Markt in cancel_only/post_only/limit_only: neue Orders werden abgelehnt."""


class NotSent(ExchangeError):
    """Verbindung nicht hergestellt – die Anfrage hat den Handelsplatz nicht erreicht."""


class Uncertain(ExchangeError):
    """Anfrage möglicherweise verarbeitet, Antwort fehlt."""


class Unavailable(ExchangeError):
    """Dienst nicht verfügbar (Wartung, Überlast)."""


@dataclass(frozen=True, slots=True)
class OrderRequest:
    pair: str
    side: Literal["buy", "sell"]
    ordertype: Literal["limit", "market", "stop-loss"]
    volume: Decimal
    cl_ord_id: str
    price: Decimal | None = None  # Limitpreis bzw. Auslösepreis (stop-loss)
    deadline: datetime | None = None  # Kraken: now+2 s … now+60 s
    timeinforce: Literal["GTC", "IOC", "GTD"] = "GTC"
    expiretm: datetime | None = None  # nur GTD
    validate: bool = False


@dataclass(frozen=True, slots=True)
class ExOrder:
    txid: str
    cl_ord_id: str | None
    pair: str
    side: str
    ordertype: str
    status: OrderStatus
    vol: Decimal
    vol_exec: Decimal
    avg_price: Decimal
    stopprice: Decimal | None = None
    limitprice: Decimal | None = None
    trade_ids: tuple[str, ...] = ()
    opentm: datetime | None = None
    closetm: datetime | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExTrade:
    trade_id: str
    ordertxid: str
    pair: str
    side: str
    price: Decimal
    vol: Decimal
    fee: Decimal
    time: datetime


@dataclass(frozen=True, slots=True)
class Quote:
    bid: Decimal
    ask: Decimal
    at: datetime


@dataclass(frozen=True, slots=True)
class CancelResult:
    count: int
    pending: bool


@dataclass(slots=True)
class Capabilities:
    """Fähigkeitsmatrix (docs/03, 2.3). Was hier False ist, wird nie als echte Order vorgetäuscht."""

    stop_loss: bool = True
    oco: bool = False  # kein OCO/Bracket per API
    conditional_close_used: bool = False  # bewusst nicht verwendet (docs/06, 3.3)
    dead_man_switch_used: bool = False  # CancelAllOrdersAfter storniert auch Schutz-Stops (docs/06, 3.3)
    amend: bool = True
    supported_protection: frozenset[str] = field(default_factory=lambda: frozenset({"STOP_AT_EXCHANGE"}))


class Exchange(Protocol):
    capabilities: Capabilities

    def system_status(self) -> SystemStatus: ...
    def balances(self) -> dict[str, Decimal]: ...
    def open_orders(self, cl_ord_id: str | None = None) -> list[ExOrder]: ...
    def closed_orders(self, cl_ord_id: str | None = None, start: datetime | None = None) -> list[ExOrder]: ...
    def trades(self, start: datetime | None = None) -> list[ExTrade]: ...
    def add_order(self, req: OrderRequest) -> str:
        """Rückgabe: txid. Fehler: Rejected | NotSent | Uncertain."""
        ...

    def amend_order(self, cl_ord_id: str, order_qty: Decimal | None = None, trigger_price: Decimal | None = None) -> None: ...
    def cancel_order(self, cl_ord_id: str) -> CancelResult: ...
    def probe_withdraw_permission(self) -> Probe:
        """Nur lesender Aufruf (WithdrawMethods). DENIED = Schlüssel hat kein Auszahlungsrecht."""
        ...

    def ticker(self, pair: str) -> Quote: ...


# Kraken-Asset-Codes je Basiswährung (Balance liefert teils Altnamen mit X/Z-Präfix). Nicht genannte: Code = Basis.
ASSET_CODES: dict[str, tuple[str, ...]] = {
    "BTC": ("XXBT", "XBT"),
    "ETH": ("XETH", "ETH"),
    "XRP": ("XXRP", "XRP"),
    "LTC": ("XLTC", "LTC"),
    "USD": ("ZUSD", "USD"),
    "EUR": ("ZEUR", "EUR"),
    "CHF": ("CHF", "ZCHF"),
}


def asset_balance(balances: dict[str, Decimal], asset: str) -> Decimal:
    """Handelbarer Bestand einer Basiswährung. Unterkonten mit Suffix (.F, .S, .M, .B, .T) zählen nicht."""
    return sum((balances.get(code, Decimal(0)) for code in ASSET_CODES.get(asset, (asset,))), Decimal(0))
