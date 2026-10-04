"""Start-Universum des ersten vertikalen Schnitts (docs/09, Annahme A-UNI). Der Scanner arbeitet
nur innerhalb dieser Liste; Erweiterungen sind eine bewusste, auditierte Änderung."""

from .ports import Instrument

BTC_USD = Instrument(
    id="KRAKEN:BTC/USD",
    kind="CRYPTO_SPOT",
    venue="KRAKEN",
    venue_symbol="XBTUSD",
    name="Bitcoin / US-Dollar",
    base_asset="BTC",
    quote_currency="USD",
    leader_id=None,
)
ETH_USD = Instrument(
    id="KRAKEN:ETH/USD",
    kind="CRYPTO_SPOT",
    venue="KRAKEN",
    venue_symbol="ETHUSD",
    name="Ether / US-Dollar",
    base_asset="ETH",
    quote_currency="USD",
    leader_id=BTC_USD.id,
)


def _alt(base: str, name: str) -> Instrument:
    return Instrument(
        id=f"KRAKEN:{base}/USD",
        kind="CRYPTO_SPOT",
        venue="KRAKEN",
        venue_symbol=f"{base}USD",
        name=f"{name} / US-Dollar",
        base_asset=base,
        quote_currency="USD",
        leader_id=BTC_USD.id,
    )


SEED_INSTRUMENTS = [BTC_USD, ETH_USD, _alt("SOL", "Solana"), _alt("XRP", "XRP"), _alt("LINK", "Chainlink")]
SIGNAL_TIMEFRAMES = ["4h", "1d"]
