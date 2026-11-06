"""Gleichlauf der Coins (docs/11, E-11): Korrelation der täglichen Renditen und Gruppen gleichläufiger Instrumente.

Rein und kausal: verwendet nur Tageskerzen, die bis `now` geschlossen sind. Statistik in float (keine Beträge).
Gruppen entstehen durch Verketten (single linkage): A und B gehören zusammen, wenn ihre Korrelation über den letzten
`window` gemeinsamen Tagen mindestens `threshold` beträgt; über B kommt auch C dazu. Ein Instrument ohne genug
gemeinsame Geschichte gilt vorsichtshalber als Teil der Gruppe seines Leitinstruments (BTC).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

WINDOW = 60
MIN_OVERLAP = 40
THRESHOLD = 0.75


@dataclass(frozen=True, slots=True)
class Exposure:
    """Gruppe je Instrument (Schlüssel = alphabetisch erstes Mitglied) und Beta zum Leitinstrument."""

    group_of: dict[str, str]
    beta: dict[str, float | None]

    def members(self, instrument_id: str) -> list[str]:
        key = self.group_of.get(instrument_id)
        return sorted(i for i, k in self.group_of.items() if k == key) if key is not None else [instrument_id]


def log_returns(closes: Sequence[tuple[datetime, Decimal]], now: datetime) -> dict[datetime, float]:
    """Logarithmische Tagesrenditen, Schlüssel = Schlusszeit der jüngeren Kerze; nur Kerzen bis `now`."""
    pts = sorted((t, float(c)) for t, c in closes if t <= now and c > 0)
    return {t1: math.log(c1 / c0) for (_, c0), (t1, c1) in zip(pts, pts[1:], strict=False)}


def _aligned(a: Mapping[datetime, float], b: Mapping[datetime, float], window: int) -> tuple[list[float], list[float]]:
    common = sorted(set(a) & set(b))[-window:]
    return [a[t] for t in common], [b[t] for t in common]


def correlation(a: Mapping[datetime, float], b: Mapping[datetime, float], window: int = WINDOW, min_overlap: int = MIN_OVERLAP) -> float | None:
    xs, ys = _aligned(a, b, window)
    n = len(xs)
    if n < min_overlap:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def beta(asset: Mapping[datetime, float], leader: Mapping[datetime, float], window: int = WINDOW, min_overlap: int = MIN_OVERLAP) -> float | None:
    """Wie stark das Instrument im Mittel mitgeht, wenn sich das Leitinstrument um 1 bewegt."""
    xs, ys = _aligned(leader, asset, window)
    n = len(xs)
    if n < min_overlap:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / var


def exposure(
    closes: Mapping[str, Sequence[tuple[datetime, Decimal]]],
    leaders: Mapping[str, str | None],
    now: datetime,
    threshold: float = THRESHOLD,
    window: int = WINDOW,
    min_overlap: int = MIN_OVERLAP,
) -> Exposure:
    ids = sorted(closes)
    rets = {i: log_returns(closes[i], now) for i in ids}
    parent = {i: i for i in ids}

    def find(i: str) -> str:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    known: set[str] = set()
    for n, a in enumerate(ids):
        for b in ids[n + 1 :]:
            c = correlation(rets[a], rets[b], window, min_overlap)
            if c is None:
                continue
            known.update((a, b))
            if c >= threshold:
                union(a, b)
    # ohne genug Geschichte: zur Gruppe des Leitinstruments (vorsichtig, nie eine eigene «unabhängige» Gruppe)
    for i in ids:
        lead = leaders.get(i)
        if i not in known and lead and lead in parent and lead != i:
            union(i, lead)

    groups: dict[str, list[str]] = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    group_of = {i: min(members) for members in groups.values() for i in members}
    betas: dict[str, float | None] = {}
    for i in ids:
        lead = leaders.get(i)
        betas[i] = 1.0 if not lead or lead == i else (beta(rets[i], rets[lead], window, min_overlap) if lead in rets else None)
    return Exposure(group_of, betas)


def short_name(instrument_id: str) -> str:
    """"KRAKEN:ETH/USD" → "ETH"."""
    return instrument_id.split(":", 1)[-1].split("/", 1)[0]


def group_label(members: Sequence[str], limit: int = 6) -> str:
    names = [short_name(m) for m in members]
    return "/".join(names[:limit]) + (f" +{len(names) - limit}" if len(names) > limit else "")
