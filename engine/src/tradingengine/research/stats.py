"""Statistik des Lernlabors in reinem Python (docs/04, 4.2 und 5.5). Kennzahlen sind Floats – es sind
Schätzungen, keine Geldbeträge. Alles Zufällige läuft über `random.Random(seed)` und ist reproduzierbar.

**Unabhängige Fälle (Cluster).** Zwei Trades gehören zum selben Fall, wenn sie sich zeitlich überlappen und
(a) dasselbe Instrument betreffen oder (b) die Tagesrenditen beider Instrumente im gemeinsamen Zeitfenster eine
Korrelation > 0.7 haben. Liegen weniger als `MIN_CORR_OBS` gemeinsame Tage vor, wird konservativ «korreliert»
angenommen. Die Zusammenfassung ist transitiv (Union-Find).

**Erwartungswert und Intervall.** Erwartungswert = mittleres Netto-R je Trade. Das Intervall stammt aus einem
Block-Bootstrap über Cluster: gezogen werden ganze Fälle mit allen ihren Trades (Statistik = Summe R / Anzahl
Trades der Stichprobe), 95 %-Perzentilintervall, fester Seed.

**Deflated Sharpe Ratio** (Bailey & López de Prado 2014). Mit der Sharpe Ratio SR der *täglichen* Renditen
(nicht annualisiert), T Tagesbeobachtungen, Schiefe γ3 und Kurtosis γ4 (nicht Exzess):

    SR0 = sqrt(V) · ((1 − γ) · Φ⁻¹(1 − 1/N) + γ · Φ⁻¹(1 − 1/(N·e)))     (erwartetes Maximum unter H0)
    DSR = Φ( (SR − SR0) · sqrt(T − 1) / sqrt(1 − γ3·SR + (γ4 − 1)/4 · SR²) )

N = Anzahl tatsächlich getesteter Varianten der Strategiefamilie (alle Experimente, auch abgebrochene),
V = Varianz der täglichen Sharpe Ratios dieser Varianten, γ = Euler-Mascheroni-Konstante. Für N = 1 ist SR0 = 0
(Probabilistic Sharpe Ratio). Tage ohne Position zählen mit Rendite 0 (ein Fonds verdient dann nichts).
Annualisierung nur für die Anzeige: SR_jahr = SR_tag · sqrt(365) (Crypto handelt an 365 Tagen).

**PBO per CSCV** (Bailey, Borwein, López de Prado, Zhu 2015). Matrix Varianten × Tage wird in S gleich lange,
zusammenhängende Abschnitte geteilt (S = 10, Rest am Ende verworfen). Für jede der C(S, S/2) Kombinationen:
In-Sample-Beste Variante n* nach Sharpe, relativer Rang ω von n* out-of-sample (1 … N, Mittelrang bei
Gleichstand) / (N + 1), Logit λ = ln(ω / (1 − ω)). PBO = Anteil λ ≤ 0. Mit N < 2 nicht bestimmbar.

**Plateau.** Alle Nachbarn ±20 % je freiem Parameter müssen ≥ 50 % der Kennzahl der gewählten Variante
erreichen; ist die Kennzahl selbst ≤ 0, gilt der Test als nicht bestanden.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from itertools import combinations
from statistics import NormalDist

EULER_GAMMA = 0.5772156649015329
_N = NormalDist()
CORR_THRESHOLD = 0.7
MIN_CORR_OBS = 10


def mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def stdev(xs: Sequence[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def sharpe(xs: Sequence[float]) -> float | None:
    s = stdev(xs)
    if s is None or s == 0:
        return None
    return (sum(xs) / len(xs)) / s


def skew_kurt(xs: Sequence[float]) -> tuple[float, float]:
    """Schiefe und Kurtosis (nicht Exzess, Normalverteilung = 3) als Momentschätzer."""
    n = len(xs)
    m = sum(xs) / n
    m2 = sum((x - m) ** 2 for x in xs) / n
    if m2 == 0:
        return 0.0, 3.0
    m3 = sum((x - m) ** 3 for x in xs) / n
    m4 = sum((x - m) ** 4 for x in xs) / n
    return m3 / m2**1.5, m4 / m2**2


def percentile(xs: Sequence[float], q: float) -> float:
    """Lineare Interpolation (wie numpy 'linear'), q in [0, 1]."""
    s = sorted(xs)
    if not s:
        raise ValueError("leer")
    pos = (len(s) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def corr(a: Sequence[float], b: Sequence[float]) -> float | None:
    n = len(a)
    if n < 2 or n != len(b):
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / math.sqrt(va * vb)


# ───────────────────────── Cluster ─────────────────────────


@dataclass(frozen=True, slots=True)
class TradeRec:
    """Ein abgeschlossener Forschungs-Trade (Netto-R = Netto / geplantes Risiko)."""

    instrument: str
    entry: datetime
    exit: datetime
    r: float
    net: float  # Handelswährung
    notional_frac: float  # Einstiegswert / Startkapital
    bars: int
    fold: int = 0

    def to_json(self) -> list[object]:
        return [self.instrument, self.entry.isoformat(), self.exit.isoformat(), round(self.r, 8), round(self.net, 6),
                round(self.notional_frac, 8), self.bars, self.fold]

    @staticmethod
    def from_json(row: list[object]) -> TradeRec:
        inst, en, ex, r, net, frac, bars, fold = row
        return TradeRec(str(inst), datetime.fromisoformat(str(en)), datetime.fromisoformat(str(ex)), float(str(r)), float(str(net)),
                        float(str(frac)), int(str(bars)), int(str(fold)))


DailyReturns = dict[str, dict[date, float]]  # Instrument → Tag → Log-Rendite


def _overlap(a: TradeRec, b: TradeRec) -> bool:
    return a.entry < b.exit and b.entry < a.exit


def _correlated(a: TradeRec, b: TradeRec, returns: DailyReturns, threshold: float) -> bool:
    ra, rb = returns.get(a.instrument), returns.get(b.instrument)
    if ra is None or rb is None:
        return True
    lo, hi = min(a.entry, b.entry).date(), max(a.exit, b.exit).date()
    days = sorted(d for d in ra if lo <= d <= hi and d in rb)
    if len(days) < MIN_CORR_OBS:
        return True  # zu kurz für eine Schätzung: konservativ als ein Fall zählen
    c = corr([ra[d] for d in days], [rb[d] for d in days])
    return c is None or c > threshold


def cluster_trades(trades: Sequence[TradeRec], returns: DailyReturns, threshold: float = CORR_THRESHOLD) -> list[list[int]]:
    """Indizes der Trades je unabhängigem Fall (in Reihenfolge des ersten Einstiegs)."""
    parent = list(range(len(trades)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(len(trades)), key=lambda i: (trades[i].entry, trades[i].instrument))
    for pos, i in enumerate(order):
        for j in order[pos + 1 :]:
            if trades[j].entry >= trades[i].exit:
                break
            a, b = trades[i], trades[j]
            if _overlap(a, b) and (a.instrument == b.instrument or _correlated(a, b, returns, threshold)):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    groups: dict[int, list[int]] = {}
    for i in order:
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def cluster_values(trades: Sequence[TradeRec], clusters: list[list[int]]) -> list[list[float]]:
    return [[trades[i].r for i in c] for c in clusters]


def cluster_bootstrap_mean(clusters: list[list[float]], seed: int, n_boot: int = 2000, level: float = 0.95) -> tuple[float, float] | None:
    """Perzentilintervall des mittleren R je Trade bei Ziehung ganzer Cluster."""
    if len(clusters) < 2:
        return None
    rng = random.Random(seed)
    sums = [sum(c) for c in clusters]
    counts = [len(c) for c in clusters]
    k = len(clusters)
    stats: list[float] = []
    for _ in range(n_boot):
        s = n = 0.0
        for _ in range(k):
            j = rng.randrange(k)
            s += sums[j]
            n += counts[j]
        stats.append(s / n)
    a = (1 - level) / 2
    return percentile(stats, a), percentile(stats, 1 - a)


def bootstrap_sum_distribution(values: Sequence[float], n: int, seed: int, n_boot: int = 2000) -> list[float]:
    """Verteilung der Summe von n zufällig (mit Zurücklegen) gezogenen Werten – Erwartung für G2/G3."""
    rng = random.Random(seed)
    vals = list(values)
    return [sum(vals[rng.randrange(len(vals))] for _ in range(n)) for _ in range(n_boot)]


def paired_bootstrap(diffs: Sequence[float], seed: int, n_boot: int = 2000, level: float = 0.95) -> tuple[float, float, float] | None:
    """Mittlere Differenz (Herausforderer − Champion) je identischem Testfenster mit Perzentilintervall."""
    if len(diffs) < 2:
        return None
    rng = random.Random(seed)
    d = list(diffs)
    k = len(d)
    stats = [sum(d[rng.randrange(k)] for _ in range(k)) / k for _ in range(n_boot)]
    a = (1 - level) / 2
    return sum(d) / k, percentile(stats, a), percentile(stats, 1 - a)


# ───────────────────────── Sharpe, DSR, PBO ─────────────────────────


def expected_max_sharpe(n_trials: int, var_trials: float) -> float:
    if n_trials < 2 or var_trials <= 0:
        return 0.0
    return math.sqrt(var_trials) * ((1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / n_trials) + EULER_GAMMA * _N.inv_cdf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(returns: Sequence[float], n_trials: int, var_trials: float) -> float | None:
    """Wahrscheinlichkeit, dass die wahre Sharpe Ratio über dem unter H0 erwarteten Maximum liegt."""
    sr = sharpe(returns)
    t = len(returns)
    if sr is None or t < 3:
        return None
    g3, g4 = skew_kurt(returns)
    sr0 = expected_max_sharpe(n_trials, var_trials)
    denom = 1 - g3 * sr + (g4 - 1) / 4 * sr * sr
    if denom <= 0:
        return None
    return _N.cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom))


def _rank_desc_relative(values: Sequence[float], idx: int) -> float:
    """Relativer Rang von values[idx] (1 = schlechteste … N = beste, Mittelrang bei Gleichstand) / (N + 1)."""
    v = values[idx]
    below = sum(1 for x in values if x < v)
    equal = sum(1 for x in values if x == v)
    rank = below + (equal + 1) / 2
    return rank / (len(values) + 1)


def pbo_cscv(matrix: Sequence[Sequence[float]], slices: int = 10) -> tuple[float, list[float]] | None:
    """PBO aus einer Matrix Varianten × Perioden. Rückgabe (PBO, Logits) oder None, wenn nicht bestimmbar."""
    n = len(matrix)
    if n < 2 or slices < 2 or slices % 2:
        return None
    t = min(len(r) for r in matrix)
    size = t // slices
    if size < 2:
        return None
    # Momente je Variante und Abschnitt: Sharpe einer Abschnittsvereinigung ohne erneutes Durchlaufen der Daten
    moments = [[(float(size), sum(r[s * size : (s + 1) * size]), sum(x * x for x in r[s * size : (s + 1) * size])) for s in range(slices)]
               for r in matrix]

    def perf(k: int, parts: Sequence[int]) -> float:
        cnt = sum(moments[k][s][0] for s in parts)
        tot = sum(moments[k][s][1] for s in parts)
        sq = sum(moments[k][s][2] for s in parts)
        m = tot / cnt
        var = (sq - cnt * m * m) / (cnt - 1)
        return m / math.sqrt(var) if var > 1e-18 else 0.0

    logits: list[float] = []
    for combo in combinations(range(slices), slices // 2):
        rest = [s for s in range(slices) if s not in combo]
        is_perf = [perf(k, combo) for k in range(n)]
        oos_perf = [perf(k, rest) for k in range(n)]
        best = max(range(n), key=lambda k: (is_perf[k], -k))
        w = _rank_desc_relative(oos_perf, best)
        logits.append(math.log(w / (1 - w)))
    return sum(1 for x in logits if x <= 0) / len(logits), logits


def max_drawdown_additive(returns: Sequence[float]) -> float:
    """Grösster Rückgang einer Kurve 1 + Σ r (Renditen bezogen auf das Startkapital), als Bruch des Höchststands."""
    eq = peak = 1.0
    dd = 0.0
    for r in returns:
        eq += r
        peak = max(peak, eq)
        if peak > 0:
            dd = max(dd, (peak - eq) / peak)
    return dd


def plateau(metric: float | None, neighbours: dict[str, float | None]) -> tuple[bool | None, str]:
    if metric is None:
        return None, "Kennzahl der gewählten Variante nicht bestimmbar"
    if not neighbours:
        return None, "keine Nachbarn innerhalb der Parametergrenzen"
    if metric <= 0:
        return False, f"Kennzahl {metric:.3f} ≤ 0"
    if any(v is None for v in neighbours.values()):
        return False, "Nachbar ohne Trades"
    worst_name = min(neighbours, key=lambda k: neighbours[k] or 0.0)
    worst = neighbours[worst_name] or 0.0
    ok = all((v or 0.0) >= 0.5 * metric for v in neighbours.values())
    return ok, f"schwächster Nachbar {worst_name}: {worst:.3f} ({worst / metric:.0%} der Kennzahl)"


def daily_log_returns(closes: Sequence[tuple[date, float]]) -> dict[date, float]:
    out: dict[date, float] = {}
    for (_, p0), (d1, p1) in zip(closes, closes[1:], strict=False):
        if p0 > 0 and p1 > 0:
            out[d1] = math.log(p1 / p0)
    return out


def fmt(x: float | None, digits: int = 3) -> str:
    return "–" if x is None else f"{x:.{digits}f}"


Metric = Callable[[Sequence[float]], float | None]
