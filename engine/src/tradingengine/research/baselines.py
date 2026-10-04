"""Baselines je Strategie (docs/04, Abschnitt 2) über dieselben Testfenster und dasselbe Kostenmodell.

(a) Cash: Rendite 0.
(b) Buy-and-Hold: gleichgewichtetes Halten der aktiven Instrumente in den Testfenstern (tägliche Renditen,
    Mittel über die Instrumente), ein Rundlauf als Taker inklusive Slippage am ersten und letzten Tag.
(c) Exposure-gleiches Buy-and-Hold: dieselben Tagesrenditen × durchschnittliche Marktexposure der Strategie
    (Σ Einstiegswert/Kapital × Haltedauer ÷ Testtage), Kosten ebenso skaliert.
(d) Zufallseinstiege: je Testfenster und Instrument so viele Trades wie die Strategie, Einstieg zum Schluss einer
    zufälligen Kerze des Fensters (Maker-Gebühr), Haltedauer und Grösse aus der empirischen Verteilung der
    Strategie, Ausstieg zum Schluss als Taker mit Slippage. Viele Ziehungen mit festem Seed → 95 %-Perzentil.

Renditen sind auf das Startkapital bezogen und addieren sich wie bei der Strategie (jedes Fenster ein Konto).
"""

from __future__ import annotations

import math
import random
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..core.costs import BPS, CostModel
from .stats import TradeRec, max_drawdown_additive, percentile
from .walkforward import Fold, FoldOut, Prepared, active_instruments, day_of


@dataclass(frozen=True, slots=True)
class SeriesStats:
    total: float
    max_dd: float

    @property
    def ratio(self) -> float | None:
        """Rendite / Max-Drawdown (None ohne Drawdown)."""
        return self.total / self.max_dd if self.max_dd > 0 else None


def series_stats(returns: list[float]) -> SeriesStats:
    return SeriesStats(sum(returns), max_drawdown_additive(returns))


def _round_trip_cost(cost: CostModel) -> float:
    return float((cost.taker_bps * 2 + cost.slippage_bps * 2) / BPS)


def buy_hold_returns(prep: Prepared, folds: list[Fold], index: list[date], cost: CostModel, scale: float = 1.0) -> list[float]:
    """Tagesrenditen des gleichgewichteten Buy-and-Hold auf dem Tagesraster `index` (nur Testtage)."""
    by_day: dict[date, float] = {}
    for f in folds:
        insts = active_instruments(prep, f.test_start, f.test_end)
        for inst in insts:
            rets = prep.returns.get(inst, {})
            d0, d1 = f.test_start.date(), day_of(f.test_end)
            for d, r in rets.items():
                if d0 <= d <= d1:
                    simple = math.exp(r) - 1
                    by_day[d] = by_day.get(d, 0.0) + scale * simple / len(insts)
    out = [by_day.get(d, 0.0) for d in index]
    rt = _round_trip_cost(cost) * scale
    if out:
        out[0] -= rt / 2
        out[-1] -= rt / 2
    return out


def exposure(fold_outs: list[FoldOut], folds: list[Fold]) -> float:
    days = sum((f.test_end - f.test_start).total_seconds() / 86400 for f in folds)
    return sum(fo.exposure_days for fo in fold_outs) / days if days > 0 else 0.0


def random_entry_totals(prep: Prepared, folds: list[Fold], trades: list[TradeRec], cost: CostModel, draws: int, seed: int) -> list[float]:
    if not trades:
        return [0.0] * draws
    holds = [max(1, t.bars) for t in trades]
    fracs = [t.notional_frac for t in trades]
    maker = float(cost.maker_bps / BPS)
    taker = float(cost.taker_bps / BPS)
    slip = float(cost.slippage_bps / BPS)
    # Je Fenster und Instrument: Kandidaten-Einstiegskerzen und Anzahl Strategietrades
    plan: list[tuple[list[float], list[int], int, int]] = []  # (Schlusskurse, Kandidaten-Indizes, Anzahl, aktive Instrumente)
    for f in folds:
        insts = active_instruments(prep, f.test_start, f.test_end)
        for inst in insts:
            n = sum(1 for t in trades if t.fold == f.index and t.instrument == inst)
            if n == 0:
                continue
            closes_t = prep.closes[inst]
            closes = [float(c.close) for c in prep.ds.candles[inst]]
            lo, hi = bisect_left(closes_t, f.test_start), bisect_left(closes_t, f.test_end)
            plan.append((closes, list(range(lo, hi)), n, len(insts)))
    rng = random.Random(seed)
    totals: list[float] = []
    for _ in range(draws):
        total = 0.0
        for closes, cands, n, n_inst in plan:
            if not cands:
                continue
            for _ in range(n):
                i = cands[rng.randrange(len(cands))]
                j = min(i + holds[rng.randrange(len(holds))], len(closes) - 1)
                frac = fracs[rng.randrange(len(fracs))]
                ret = closes[j] * (1 - slip) * (1 - taker) / (closes[i] * (1 + maker)) - 1
                total += frac * ret / n_inst
        totals.append(total)
    return totals


@dataclass(frozen=True, slots=True)
class BaselineResult:
    cash: SeriesStats
    buy_hold: SeriesStats
    matched: SeriesStats
    exposure: float
    random_p95: float
    random_median: float
    draws: int


def compute(prep: Prepared, folds: list[Fold], fold_outs: list[FoldOut], index: list[date], cost: CostModel, draws: int, seed: int) -> BaselineResult:
    trades = [t for fo in fold_outs for t in fo.trades]
    e = exposure(fold_outs, folds)
    bh = series_stats(buy_hold_returns(prep, folds, index, cost))
    matched = series_stats(buy_hold_returns(prep, folds, index, cost, scale=e))
    rand = random_entry_totals(prep, folds, trades, cost, draws, seed)
    return BaselineResult(SeriesStats(0.0, 0.0), bh, matched, e, percentile(rand, 0.95), percentile(rand, 0.5), draws)


def money(x: float, start_cash: Decimal) -> str:
    return f"{Decimal(str(x)) * start_cash:.2f}"
