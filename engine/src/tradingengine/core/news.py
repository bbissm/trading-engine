"""Nachrichten als Merkmal (docs/11, E-12): Claudes Einordnungen aus `news_assessment`, nur was vor dem Kerzenschluss
vorlag. Der Wert ist eine Summe gewichteter Richtungen, keine Wahrscheinlichkeit und keine Prognose."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

# Kürzel bei Kraken → Kürzel in den Meldungen
ASSET_ALIASES = {"XBT": "BTC", "XDG": "DOGE"}
CREDIBILITY_WEIGHT = {"OFFICIAL": 1.0, "REPORTED": 0.7, "RUMOR": 0.0}


@dataclass(frozen=True, slots=True)
class NewsPoint:
    at: datetime  # wann die Einordnung gespeichert wurde (nicht das Veröffentlichungsdatum)
    direction: int  # −2 … +2
    relevance: int  # 0 … 100
    credibility: str  # OFFICIAL | REPORTED | RUMOR
    scope: str  # COIN | MARKET | MACRO | NONE


def news_asset(base_asset: str | None) -> str | None:
    if not base_asset:
        return None
    return ASSET_ALIASES.get(base_asset.upper(), base_asset.upper())


def news_score(points: Sequence[NewsPoint], until: datetime, days: int = 14, min_relevance: int = 30) -> float:
    """Summe von Richtung × Relevanz/100 × Glaubwürdigkeit über die Coin-Meldungen der letzten `days` Tage vor `until`."""
    since = until - timedelta(days=days)
    return sum(
        p.direction * p.relevance / 100 * CREDIBILITY_WEIGHT.get(p.credibility, 0.0)
        for p in points
        if p.scope == "COIN" and since < p.at <= until and p.relevance >= min_relevance
    )
