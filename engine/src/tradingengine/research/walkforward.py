"""Walk-forward mit Purging und Embargo (docs/04, 4.1) über `backtest.run_backtest`.

**Fenster.** Testfenster liegen lückenlos hintereinander (Länge 6 Monate, Schritt 6 Monate). Zum Testfenster
[T, T + 6M) gehört das Trainingsfenster [T − Sperrzone − 24M, T − Sperrzone). Sperrzone = maximaler
Label-Horizont (maximale Haltedauer der Strategie in Kerzen × Kerzenlänge) + Embargo 5 Tage.

**Purging.** Ein Trainingslauf endet mit der letzten Kerze vor dem Trainingsende; Positionen, die dann noch offen
sind, werden nicht abgeschlossen und zählen nicht (ihr Label wäre erst später bekannt). Damit endet jedes
Trainings-Label vor dem Trainingsende und so mindestens eine Sperrzone vor dem Testbeginn (getestet).

**Aufwärmen.** Jeder Lauf bekommt `warmup_bars` Kerzen vor dem Fensterbeginn, nur damit Indikatoren (EMA, ATR,
Donchian, Bollinger) eingeschwungen sind. Einstiege sind ausschliesslich für Entscheidungskerzen erlaubt, deren
Schluss im Fenster liegt (die Entscheidungsfunktion wird umhüllt, BUY ausserhalb → NO_TRADE). Das Regime kommt
aus den Tageskerzen des ganzen Datensatzes (kausal: Punkt i nutzt nur Tage ≤ i).

**Testtrades.** Ein im Testfenster eröffneter Trade darf über das Fensterende hinaus bis zu seinem Plan-Exit laufen
(höchstens maximale Haltedauer + 3 Kerzen, nie über das Datensatzende – also nie in den Holdout).
Parameterwahl (OPTIMIZE) geschieht nur über Trainingskennzahlen.

**Konto.** Jeder Lauf ist ein eigenes Konto mit 10 000 USD (Instrumente getrennt, keine Portfolio-Interaktion –
Vereinfachung). Die Forschungs-Risikopolicy entspricht `RiskPolicy()` ohne Drawdown-Pause (sonst bliebe ein
Lauf nach 8 % Rückgang für immer blockiert und der Drawdown wäre nicht messbar); das Gate prüft den Drawdown
gegen 1.5 × das konfigurierte Limit. Tick-Grösse für alle Bitstamp-Reihen 0.00001.
"""

from __future__ import annotations

import calendar
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..backtest import BacktestConfig, run_backtest
from ..core.candles import Candle, timeframe_delta
from ..core.costs import KRAKEN_SPOT_TIER1, CostModel
from ..core.regime import Regime, RegimePoint, daily_regimes
from ..core.risk import RiskPolicy
from ..core.sizing import InstrumentSpec
from .data import Dataset
from .stats import DailyReturns, TradeRec, cluster_trades, daily_log_returns
from .variants import Family, make_strategy, max_hold

START_CASH = Decimal(10000)
RESEARCH_POLICY = replace(RiskPolicy(), drawdown_pause=Decimal(1), drawdown_emergency=Decimal(1))
RESEARCH_SPEC = InstrumentSpec(tick=Decimal("0.00001"), qty_step=Decimal("0.00000001"), min_qty=Decimal("0.00000001"), min_notional=Decimal(5))
EXTRA_EXIT_BARS = 3


@dataclass(frozen=True, slots=True)
class WFConfig:
    train_months: int = 24
    test_months: int = 6
    step_months: int = 6
    embargo_days: int = 5
    warmup_bars: int = 150
    min_test_days: int = 90  # letztes, verkürztes Testfenster nur, wenn es mindestens so lang ist

    @staticmethod
    def from_json(d: dict[str, Any] | None) -> WFConfig:
        d = d or {}
        base = WFConfig()
        return WFConfig(**{f.name: int(d.get(f.name, getattr(base, f.name))) for f in fields(WFConfig)})

    def to_json(self) -> dict[str, int]:
        return {f.name: int(getattr(self, f.name)) for f in fields(self)}


@dataclass(frozen=True, slots=True)
class Fold:
    index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    run_end: datetime  # bis hierhin dürfen Testtrades auslaufen

    def to_json(self) -> dict[str, Any]:
        return {"index": self.index, "train": [self.train_start.isoformat(), self.train_end.isoformat()],
                "test": [self.test_start.isoformat(), self.test_end.isoformat()], "run_end": self.run_end.isoformat()}

    @staticmethod
    def from_json(d: dict[str, Any]) -> Fold:
        return Fold(int(d["index"]), datetime.fromisoformat(d["train"][0]), datetime.fromisoformat(d["train"][1]),
                    datetime.fromisoformat(d["test"][0]), datetime.fromisoformat(d["test"][1]), datetime.fromisoformat(d["run_end"]))


def add_months(t: datetime, months: int) -> datetime:
    m = t.month - 1 + months
    year, month = t.year + m // 12, m % 12 + 1
    return t.replace(year=year, month=month, day=min(t.day, calendar.monthrange(year, month)[1]))


def sperrzone(timeframe: str, max_hold_bars: int, embargo_days: int) -> timedelta:
    return timeframe_delta(timeframe) * max_hold_bars + timedelta(days=embargo_days)


def make_folds(start: datetime, end: datetime, timeframe: str, max_hold_bars: int, cfg: WFConfig) -> list[Fold]:
    gap = sperrzone(timeframe, max_hold_bars, cfg.embargo_days)
    ext = timeframe_delta(timeframe) * (max_hold_bars + EXTRA_EXIT_BARS)
    folds: list[Fold] = []
    test_start = add_months(start, cfg.train_months) + gap
    while test_start < end:
        test_end = min(add_months(test_start, cfg.test_months), end)
        if test_end - test_start < timedelta(days=cfg.min_test_days):
            break
        train_end = test_start - gap
        train_start = max(start, add_months(train_end, -cfg.train_months))
        folds.append(Fold(len(folds), train_start, train_end, test_start, test_end, min(test_end + ext, end)))
        test_start = add_months(test_start, cfg.step_months)
    return folds


def day_of(close_time: datetime) -> date:
    """Kalendertag einer Kerze (Schluss 00:00 gehört zum Vortag)."""
    return (close_time - timedelta(microseconds=1)).date()


@dataclass
class Prepared:
    ds: Dataset
    own: dict[str, list[RegimePoint]]
    leader: dict[str, list[RegimePoint]]
    closes: dict[str, list[datetime]]  # Schlusszeiten je Instrument (für bisect)
    returns: DailyReturns
    leader_id: str


def prepare(ds: Dataset) -> Prepared:
    regimes = {k: daily_regimes(v) for k, v in ds.daily.items()}
    own = {i: regimes.get(i, []) for i in ds.instruments}
    leader = {i: regimes.get(ds.leaders.get(i, i), own[i]) for i in ds.instruments}
    closes = {i: [c.close_time for c in ds.candles[i]] for i in ds.instruments}
    returns = {k: daily_log_returns([(day_of(c.close_time), float(c.close)) for c in v]) for k, v in ds.daily.items()}
    leader_id = ds.leaders.get(ds.instruments[0], ds.instruments[0]) if ds.instruments else ""
    return Prepared(ds, own, leader, closes, returns, leader_id)


@dataclass
class WindowOut:
    trades: list[TradeRec] = field(default_factory=list)
    deltas: dict[date, float] = field(default_factory=dict)  # Eigenkapitaländerung / Startkapital je Tag
    exposure_days: float = 0.0  # Σ Einstiegswert/Startkapital × Haltetage


def run_window(prep: Prepared, inst: str, fam: Family, params: dict[str, Any], entry_start: datetime, entry_end: datetime, run_end: datetime,
               cost: CostModel, warmup_bars: int, fold: int = 0, delay_bars: int = 0) -> WindowOut:
    candles: list[Candle] = prep.ds.candles[inst]
    closes = prep.closes[inst]
    first = bisect_left(closes, entry_start)  # erste Entscheidungskerze im Fenster
    last = bisect_right(closes, run_end)
    if first >= last:
        return WindowOut()
    lo = max(0, first - warmup_bars)
    window = candles[lo:last]
    strategy = make_strategy(fam, params, (entry_start, entry_end), delay_bars)
    cfg = BacktestConfig(start_cash=START_CASH, policy=RESEARCH_POLICY, cost=cost, spec=RESEARCH_SPEC, warmup_bars=first - lo)
    res = run_backtest(window, prep.own[inst], prep.leader[inst], strategy, cfg)
    out = WindowOut()
    for t in res.trades:
        out.trades.append(TradeRec(inst, t.entry_time, t.exit_time, float(t.r_multiple), float(t.result.net),
                                   float(t.result.entry_value / START_CASH), t.bars_held, fold))
        out.exposure_days += float(t.result.entry_value / START_CASH) * (t.exit_time - t.entry_time).total_seconds() / 86400
    prev = START_CASH
    for ts, eq in res.equity_curve:
        if eq != prev:
            d = day_of(ts)
            out.deltas[d] = out.deltas.get(d, 0.0) + float((eq - prev) / START_CASH)
        prev = eq
    return out


@dataclass
class FoldOut:
    fold: int
    trades: list[TradeRec]
    deltas: dict[date, float]  # Portfolio: Mittel über die im Fenster aktiven Instrumente
    exposure_days: float
    train_trades: list[TradeRec] | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "fold": self.fold,
            "trades": [t.to_json() for t in self.trades],
            "deltas": [[d.isoformat(), round(v, 10)] for d, v in sorted(self.deltas.items())],
            "exposure_days": round(self.exposure_days, 6),
        }
        if self.train_trades is not None:
            out["train_trades"] = [t.to_json() for t in self.train_trades]
        return out

    @staticmethod
    def from_json(d: dict[str, Any]) -> FoldOut:
        return FoldOut(
            int(d["fold"]), [TradeRec.from_json(t) for t in d["trades"]], {date.fromisoformat(k): float(v) for k, v in d["deltas"]},
            float(d.get("exposure_days", 0.0)),
            None if d.get("train_trades") is None else [TradeRec.from_json(t) for t in d["train_trades"]],
        )


def active_instruments(prep: Prepared, start: datetime, end: datetime) -> list[str]:
    out = []
    for inst in prep.ds.instruments:
        closes = prep.closes[inst]
        if bisect_right(closes, end) - bisect_left(closes, start) > 0:
            out.append(inst)
    return out


def evaluate_fold(prep: Prepared, fam: Family, params: dict[str, Any], fold: Fold, cost: CostModel, cfg: WFConfig,
                  with_train: bool = False, delay_bars: int = 0) -> FoldOut:
    insts = active_instruments(prep, fold.test_start, fold.test_end)
    trades: list[TradeRec] = []
    deltas: dict[date, float] = {}
    exposure = 0.0
    train: list[TradeRec] | None = [] if with_train else None
    for inst in insts:
        w = run_window(prep, inst, fam, params, fold.test_start, fold.test_end, fold.run_end, cost, cfg.warmup_bars, fold.index, delay_bars)
        trades += w.trades
        exposure += w.exposure_days / len(insts)
        for d, v in w.deltas.items():
            deltas[d] = deltas.get(d, 0.0) + v / len(insts)
        if train is not None:
            tw = run_window(prep, inst, fam, params, fold.train_start, fold.train_end, fold.train_end, cost, cfg.warmup_bars, fold.index)
            train += tw.trades
    trades.sort(key=lambda t: (t.entry, t.instrument))
    return FoldOut(fold.index, trades, deltas, exposure, train)


def objective(trades: list[TradeRec], returns: DailyReturns, min_clusters: int = 5, lam: float = 0.05) -> float | None:
    """Trainingsziel (docs/04, 5.3): mittleres Netto-R je Cluster − λ · MaxDrawdown(R). Unter `min_clusters`: None."""
    clusters = cluster_trades(trades, returns)
    if len(clusters) < min_clusters:
        return None
    per = [sum(trades[i].r for i in c) for c in clusters]
    cum = peak = dd = 0.0
    for t in sorted(trades, key=lambda t: t.exit):
        cum += t.r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return sum(per) / len(per) - lam * dd


def day_index(folds: list[Fold]) -> list[date]:
    if not folds:
        return []
    d0, d1 = folds[0].test_start.date(), day_of(max(f.run_end for f in folds))
    return [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]


def vector(deltas: dict[date, float], index: list[date]) -> list[float]:
    return [deltas.get(d, 0.0) for d in index]


def regime_phases(points: list[RegimePoint], windows: list[tuple[datetime, datetime]], regime: Regime, min_days: int) -> int:
    """Anzahl zusammenhängender Phasen mit ≥ min_days Tagen des Regimes innerhalb der Fenster."""
    phases = run = 0
    for p in points:
        inside = any(a < p.close_time <= b for a, b in windows)
        if inside and p.regime is regime:
            run += 1
            if run == min_days:
                phases += 1
        else:
            run = 0
    return phases


COST_SCENARIOS: dict[str, tuple[CostModel, int]] = {
    "Basis": (KRAKEN_SPOT_TIER1, 0),
    "Kosten × 1.5": (KRAKEN_SPOT_TIER1.scaled(Decimal("1.5")), 0),
    "Kosten × 2": (KRAKEN_SPOT_TIER1.scaled(Decimal(2)), 0),
    "Slippage +50 %": (CostModel(f"{KRAKEN_SPOT_TIER1.version}+slip50", KRAKEN_SPOT_TIER1.maker_bps, KRAKEN_SPOT_TIER1.taker_bps,
                                 KRAKEN_SPOT_TIER1.slippage_bps * Decimal("1.5")), 0),
    "Einstieg eine Kerze später": (KRAKEN_SPOT_TIER1, 1),
}


def fold_max_hold(params: dict[str, Any]) -> int:
    return max_hold(params)
