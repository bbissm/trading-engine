"""Alpaca Market Data API v2 (nur Lesen): Tageskerzen, Börsenkalender, Stammdaten. Kein Handels-Endpunkt.

Geprüft gegen docs.alpaca.markets (4.10.2026):
- `GET https://data.alpaca.markets/v2/stocks/bars` – `symbols` (kommagetrennt), `timeframe=1Day`, `start`/`end`
  (RFC-3339, inklusiv), `limit` 1–10 000 *über alle Symbole*, `adjustment` raw|split|dividend|spin-off|all,
  `feed` sip|iex|boats|otc, `page_token`, `sort` asc. Antwort `{"bars": {SYM: [{t,o,h,l,c,v,n,vw}]},
  "next_page_token": …}`, sortiert nach Symbol, dann Zeit. Tageskerzen-Zeitstempel = Tag in New York.
- Basic-Plan (kostenlos): Historie seit 2016, SIP nur älter als 15 min («subscription does not permit querying
  recent SIP data»), 200 Abrufe/min.
- `GET https://paper-api.alpaca.markets/v2/calendar?start=&end=` → `[{date, open "HH:MM", close "HH:MM",
  session_open, session_close, settlement_date}]` (als «legacy» bezeichnet, ohne genannten Ersatz).
- `GET https://paper-api.alpaca.markets/v2/assets/{symbol}` → u. a. `exchange` (NYSE, NASDAQ, NYSEARCA, ARCA, …).
- Authentifizierung über die Header `APCA-API-KEY-ID` und `APCA-API-SECRET-KEY`.

Geheimnisse: Der Schlüssel steht nur in den Request-Headern. Er erscheint in keiner Ausnahme, keinem Log und
keiner `repr`-Ausgabe. Fehlermeldungen enthalten nur Statuscode und die (gekürzte) Antwort von Alpaca.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from ..calendar_us import UsCalendar, ny_date
from ..core.candles import Candle
from ..ports import Instrument

DATA_URL = "https://data.alpaca.markets"
TRADING_URL = "https://paper-api.alpaca.markets"  # Paper-only-Konto; Kalender und Assets sind dort identisch
ENV_KEY_ID = "ALPACA_API_KEY_ID"
ENV_SECRET = "ALPACA_API_SECRET_KEY"
SOURCE = "alpaca-sip"
PAGE_LIMIT = 10_000
# Basic-Plan: SIP-Daten nur älter als 15 min. Eine Minute Reserve gegen Uhrabweichungen.
SIP_DELAY = timedelta(minutes=16)

log = logging.getLogger(__name__)


class AlpacaError(RuntimeError):
    pass


class AlpacaRateLimited(AlpacaError):
    pass


@dataclass(frozen=True, slots=True)
class AlpacaCredentials:
    key_id: str
    secret: str = field(repr=False)

    def __repr__(self) -> str:
        return "AlpacaCredentials(key_id=***, secret=***)"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AlpacaCredentials | None:
        e = os.environ if env is None else env
        key_id, secret = (e.get(ENV_KEY_ID) or "").strip(), (e.get(ENV_SECRET) or "").strip()
        return cls(key_id, secret) if key_id and secret else None

    def headers(self) -> dict[str, str]:
        return {"APCA-API-KEY-ID": self.key_id, "APCA-API-SECRET-KEY": self.secret}


def configured(env: Mapping[str, str] | None = None) -> bool:
    return AlpacaCredentials.from_env(env) is not None


def rfc3339(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class BarRow:
    """Rohe Tageskerze von Alpaca (Preise als Decimal ohne Float-Umweg)."""

    symbol: str
    day: date  # Handelstag in New York
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trades: int | None


def parse_bars(payload: dict[str, Any]) -> tuple[list[BarRow], str | None]:
    """Eine Seite von `/v2/stocks/bars` → Kerzen-Zeilen und Folge-Token."""
    rows: list[BarRow] = []
    for symbol, bars in (payload.get("bars") or {}).items():
        for b in bars or []:
            t = datetime.fromisoformat(str(b["t"]).replace("Z", "+00:00"))
            rows.append(
                BarRow(
                    symbol=symbol, day=ny_date(t), open=Decimal(str(b["o"])), high=Decimal(str(b["h"])), low=Decimal(str(b["l"])),
                    close=Decimal(str(b["c"])), volume=Decimal(str(b["v"])), trades=None if b.get("n") is None else int(b["n"]),
                )
            )
    token = payload.get("next_page_token") or None
    return rows, token


def to_candles(rows: list[BarRow], ids: Mapping[str, str], calendar: UsCalendar, now: datetime, source: str = SOURCE) -> list[Candle]:
    """Tageskerzen mit Sitzungsbeginn/-schluss aus dem Kalender. Kerzen ohne Sitzung im Kalender und noch
    nicht geschlossene Sitzungen werden verworfen (nie als abgeschlossen behandelt)."""
    out: list[Candle] = []
    for r in rows:
        iid = ids.get(r.symbol)
        session = calendar.session(r.day)
        if iid is None:
            continue
        if session is None:
            log.warning("Alpaca-Kerze %s %s liegt auf keinem Handelstag des Kalenders – verworfen", r.symbol, r.day)
            continue
        if session.close > now:
            continue
        out.append(Candle(iid, "1d", session.open, session.close, r.open, r.high, r.low, r.close, r.volume, r.trades, source))
    out.sort(key=lambda c: (c.instrument_id, c.open_time))
    return out


class AlpacaData:
    """Lesender Zugriff auf Alpaca-Marktdaten. `client` ist für Tests ersetzbar (httpx.MockTransport)."""

    source = SOURCE

    def __init__(self, credentials: AlpacaCredentials, client: httpx.Client | None = None, timeout_s: float = 10.0) -> None:
        self._creds = credentials
        self._client = client or httpx.Client(timeout=timeout_s, headers={"User-Agent": "tradingengine/0.1"})
        self.calls = 0  # Abrufe in diesem Objekt (Rate-Limit-Kontrolle)

    def __repr__(self) -> str:
        return "AlpacaData(***)"

    def _get(self, url: str, params: dict[str, str], timeout_s: float | None = None) -> Any:
        self.calls += 1
        try:
            resp = self._client.get(url, params=params, headers=self._creds.headers(), timeout=timeout_s or httpx.USE_CLIENT_DEFAULT)
        except httpx.HTTPError as exc:
            raise AlpacaError(f"Alpaca nicht erreichbar: {type(exc).__name__}") from None
        if resp.status_code == 429:
            raise AlpacaRateLimited("Alpaca: Abruflimit erreicht (HTTP 429)")
        if resp.status_code >= 400:
            raise AlpacaError(f"Alpaca HTTP {resp.status_code}: {_safe_text(resp.text, self._creds)}")
        return json.loads(resp.text, parse_float=Decimal)

    def bars_page(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        page_token: str | None = None,
        adjustment: str = "split",
        feed: str = "sip",
        limit: int = PAGE_LIMIT,
        timeout_s: float | None = None,
    ) -> tuple[list[BarRow], str | None]:
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": rfc3339(start),
            "end": rfc3339(end),
            "adjustment": adjustment,
            "feed": feed,
            "limit": str(limit),
            "sort": "asc",
        }
        if page_token:
            params["page_token"] = page_token
        return parse_bars(self._get(f"{DATA_URL}/v2/stocks/bars", params, timeout_s))

    def calendar(self, start: date, end: date, timeout_s: float | None = None) -> UsCalendar:
        rows = self._get(f"{TRADING_URL}/v2/calendar", {"start": start.isoformat(), "end": end.isoformat()}, timeout_s)
        if not isinstance(rows, list):
            raise AlpacaError("Alpaca-Kalender: unerwartete Antwort")
        return UsCalendar.from_alpaca(rows, start, end)

    def exchanges(self, symbols: list[str], timeout_s: float | None = None) -> dict[str, str]:
        """Kotierungsbörse je Symbol (ein Abruf je Symbol; nur beim Anlegen des Universums)."""
        out: dict[str, str] = {}
        for s in symbols:
            asset = self._get(f"{TRADING_URL}/v2/assets/{s}", {}, timeout_s)
            if isinstance(asset, dict) and asset.get("exchange"):
                out[s] = str(asset["exchange"])
        return out


def _safe_text(text: str, creds: AlpacaCredentials) -> str:
    """Antworttext gekürzt und ohne Schlüssel (falls ein Dienst ihn je zurückspiegeln sollte)."""
    out = text[:200]
    for secret in (creds.secret, creds.key_id):
        if secret:
            out = out.replace(secret, "***")
    return out


def instruments_by_symbol(instruments: list[Instrument]) -> dict[str, str]:
    return {i.venue_symbol: i.id for i in instruments}
