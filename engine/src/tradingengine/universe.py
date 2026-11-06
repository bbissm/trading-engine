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


def _alt(base: str, name: str, venue_symbol: str | None = None) -> Instrument:
    return Instrument(
        id=f"KRAKEN:{base}/USD",
        kind="CRYPTO_SPOT",
        venue="KRAKEN",
        venue_symbol=venue_symbol or f"{base}USD",
        name=f"{name} / US-Dollar",
        base_asset=base,
        quote_currency="USD",
        leader_id=BTC_USD.id,
    )


# Erweiterung 6.11.2026 (docs/11, E-11): die liquidesten USD-Paare bei Kraken (24h-Volumen ab etwa 1 Mio. USD) mit
# mindestens 200 Tageskerzen Geschichte; ohne Stablecoins, Gold-Token und Meme-Neulinge. Leitinstrument bleibt BTC.
EXTENSION_2026_11 = [
    _alt("ZEC", "Zcash"),
    _alt("NEAR", "NEAR Protocol"),
    _alt("HYPE", "Hyperliquid"),
    _alt("SUI", "Sui"),
    _alt("XMR", "Monero"),
    _alt("DOGE", "Dogecoin", "XDGUSD"),
    _alt("HBAR", "Hedera"),
    _alt("LTC", "Litecoin"),
    _alt("TAO", "Bittensor"),
    _alt("ADA", "Cardano"),
    _alt("QNT", "Quant"),
    _alt("ENA", "Ethena"),
    _alt("PEPE", "Pepe"),
    _alt("UNI", "Uniswap"),
    _alt("INJ", "Injective"),
    _alt("XLM", "Stellar"),
    _alt("AAVE", "Aave"),
    _alt("AVAX", "Avalanche"),
    _alt("ONDO", "Ondo"),
    _alt("ARB", "Arbitrum"),
    _alt("BCH", "Bitcoin Cash"),
    _alt("TRX", "Tron"),
]

SEED_INSTRUMENTS = [BTC_USD, ETH_USD, _alt("SOL", "Solana"), _alt("XRP", "XRP"), _alt("LINK", "Chainlink"), *EXTENSION_2026_11]
SIGNAL_TIMEFRAMES = ["4h", "1d"]
