"""US-Aktien/ETF-Teil des Start-Universums (docs/09, Annahme A-UNI) mit Marktdaten von Alpaca.

Die Instrumente werden nur angelegt, wenn Alpaca-Schlüssel konfiguriert sind (`ALPACA_API_KEY_ID`,
`ALPACA_API_SECRET_KEY`). Ohne Schlüssel erscheint nichts, das als defekt angezeigt würde.

Survivorship (docs/04, 4.2): Auswahl nach heutiger Liquidität; für ETFs klein, für Einzelaktien erheblich.
Handel nur in ganzen Stücken (IBKR-API unterstützt keine Bruchstücke, docs/03).
Zeitebene: Tageskerzen (A-TF) – Signal nach US-Börsenschluss, Order zur nächsten Sitzung.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from .ports import Instrument

PREFIX = "ALPACA:"
LEADER_ID = f"{PREFIX}SPY"
STOCK_KINDS = frozenset({"STOCK", "ETF"})
STOCK_TIMEFRAMES = ["1d"]
TICK = Decimal("0.01")
MIN_QTY = Decimal(1)
DEFAULT_VENUE = "US"

ETFS: list[tuple[str, str]] = [
    ("SPY", "SPDR S&P 500 ETF"),
    ("QQQ", "Invesco QQQ (Nasdaq-100)"),
    ("IWM", "iShares Russell 2000 ETF"),
    ("DIA", "SPDR Dow Jones Industrial Average ETF"),
    ("EFA", "iShares MSCI EAFE ETF"),
    ("EEM", "iShares MSCI Emerging Markets ETF"),
    ("GLD", "SPDR Gold Shares"),
    ("TLT", "iShares 20+ Year Treasury Bond ETF"),
    ("XLK", "Technology Select Sector SPDR"),
    ("XLF", "Financial Select Sector SPDR"),
    ("XLE", "Energy Select Sector SPDR"),
    ("XLV", "Health Care Select Sector SPDR"),
]
STOCKS: list[tuple[str, str]] = [
    ("AAPL", "Apple"),
    ("MSFT", "Microsoft"),
    ("NVDA", "NVIDIA"),
    ("AMZN", "Amazon"),
    ("GOOGL", "Alphabet (Klasse A)"),
    ("META", "Meta Platforms"),
    ("JPM", "JPMorgan Chase"),
    ("UNH", "UnitedHealth"),
]
SYMBOLS: list[str] = [s for s, _ in ETFS + STOCKS]


def instrument_id(symbol: str) -> str:
    return f"{PREFIX}{symbol}"


def symbol_of(instrument_id: str) -> str:
    return instrument_id.removeprefix(PREFIX)


def is_stock_id(instrument_id: str) -> bool:
    return instrument_id.startswith(PREFIX)


def is_stock(instrument: Instrument) -> bool:
    return instrument.kind in STOCK_KINDS and is_stock_id(instrument.id)


def stock_instruments(configured: bool, exchanges: Mapping[str, str] | None = None) -> list[Instrument]:
    """Instrumente des Aktien-Universums; leer, solange keine Alpaca-Schlüssel konfiguriert sind.

    `exchanges`: Kotierungsbörse je Symbol aus dem Alpaca-Assets-Endpunkt (z. B. "NYSEARCA"); fehlt sie, gilt "US".
    """
    if not configured:
        return []
    out: list[Instrument] = []
    for kind, rows in (("ETF", ETFS), ("STOCK", STOCKS)):
        for symbol, name in rows:
            iid = instrument_id(symbol)
            out.append(
                Instrument(
                    id=iid,
                    kind=kind,
                    venue=(exchanges or {}).get(symbol) or DEFAULT_VENUE,
                    venue_symbol=symbol,
                    name=name,
                    base_asset=symbol,
                    quote_currency="USD",
                    leader_id=None if iid == LEADER_ID else LEADER_ID,
                    in_universe=True,
                    tick_size=TICK,
                    min_qty=MIN_QTY,
                    min_notional=Decimal(0),
                )
            )
    return out
