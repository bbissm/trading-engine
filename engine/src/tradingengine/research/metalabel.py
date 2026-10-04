"""Meta-Labeling (docs/04, Lernweg 2): Die Regelstrategie erzeugt Kandidaten; eine regularisierte logistische
Regression schätzt, ob ein Kandidat netto lohnt. Das Modell darf nur **filtern** (Kandidat auslassen), nie eine
Position über die Regelgrösse hinaus vergrössern.

- **Label:** Netto-Ergebnis nach Kosten beim Plan-Exit (Stop/Trailing/Zeitlimit/Regime) > 0. Ein Beispiel ist erst
  trainierbar, wenn sein Exit vor dem Trainingsende liegt (`label_verfügbar_ab` = Exitzeit). Die Trainingsläufe
  des Walk-forward schliessen offene Positionen am Fensterende nicht – sie fehlen, statt ein Label vorwegzunehmen.
- **Features zur Signalzeit** (kausal aus Kerzen berechnet, dieselben Grössen wie `feature_snapshot`, das für die
  Historie nicht vorliegt): Abstand zur SMA200 (Tag), ADX(14) (Tag), Volatilitäts-Perzentil (Tag), RSI(14),
  ATR(14)/Preis, Rendite 20 Kerzen, Volumen / Median(20).
- **Pipeline je Fold:** Standardisierung und Gewichte nur auf dem Trainingsfenster, Vorhersage nur im Testfenster
  (out-of-fold). Das Pipeline-Protokoll hält Trainingsende, spätestes Label und Anzahl Fit-Beispiele fest.
- **Kalibrierung (5.4):** Eine Prozentanzeige wäre nur erlaubt bei ≥ 300 out-of-fold-Fällen, ECE ≤ 0.05 und
  Brier-Score besser als die Basisrate. Sonst bleibt es beim Setup-Score «nicht kalibriert».
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..core import indicators as ind
from ..core.candles import timeframe_delta
from .stats import TradeRec, paired_bootstrap
from .walkforward import Fold, FoldOut, Prepared

MODEL_VERSION = "metalabel-logreg-l2@1"
FEATURES = ["dist_sma200", "adx14_d", "vol_pctl_d", "rsi14", "atr_pct", "ret20", "vol_ratio"]
MIN_OOF = 300
MAX_ECE = 0.05
MIN_TRAIN = 30


def feature_table(prep: Prepared, inst: str) -> dict[datetime, list[float]]:
    """Featurevektor je Schlusszeit einer Kerze der Signal-Zeitebene (nur vollständige Vektoren)."""
    candles = prep.ds.candles[inst]
    close = [float(c.close) for c in candles]
    high = [float(c.high) for c in candles]
    low = [float(c.low) for c in candles]
    vol = [float(c.volume) for c in candles]
    rsi = ind.rsi(close, 14)
    atr = ind.atr(high, low, close, 14)
    med = ind.rolling_median(vol, 20)
    points = prep.own[inst]
    times = [p.close_time for p in points]
    out: dict[datetime, list[float]] = {}
    for i, c in enumerate(candles):
        j = bisect_right(times, c.close_time) - 1
        if j < 0 or i < 21:
            continue
        f = points[j].features
        sma200, adx, pctl = f.get("sma200"), f.get("adx14"), f.get("vol_pctl")
        r, a, m = rsi[i], atr[i], med[i - 1]
        if sma200 is None or adx is None or pctl is None or r is None or a is None or m is None or m <= 0 or close[i] <= 0:
            continue
        out[c.close_time] = [float(f.get("close") or close[i]) / sma200 - 1, adx, pctl, r, a / close[i], close[i] / close[i - 20] - 1, vol[i] / m]
    return out


@dataclass
class Example:
    x: list[float]
    y: int
    r: float
    label_at: datetime
    fold: int


def examples(prep: Prepared, trades: list[TradeRec], tables: dict[str, dict[datetime, list[float]]]) -> list[Example]:
    step = timeframe_delta(prep.ds.timeframe)
    out = []
    for t in trades:
        x = tables[t.instrument].get(t.entry - step)  # Signalkerze = Kerze vor der Füllkerze
        if x is not None:
            out.append(Example(x, int(t.net > 0), t.r, t.exit, t.fold))
    return out


@dataclass
class Model:
    mu: list[float]
    sd: list[float]
    w: list[float]
    b: float

    def predict(self, x: list[float]) -> float:
        z = self.b + sum(wi * (xi - m) / s for wi, xi, m, s in zip(self.w, x, self.mu, self.sd, strict=True))
        return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def fit(xs: list[list[float]], ys: list[int], l2: float = 0.01, lr: float = 0.1, iters: int = 400) -> Model:
    """L2-regularisierte logistische Regression, Batch-Gradientenabstieg, Standardisierung aus denselben Daten."""
    n, k = len(xs), len(xs[0])
    mu = [sum(x[j] for x in xs) / n for j in range(k)]
    sd = [math.sqrt(sum((x[j] - mu[j]) ** 2 for x in xs) / n) or 1.0 for j in range(k)]
    zs = [[(x[j] - mu[j]) / sd[j] for j in range(k)] for x in xs]
    w, b = [0.0] * k, 0.0
    for _ in range(iters):
        gw, gb = [0.0] * k, 0.0
        for z, y in zip(zs, ys, strict=True):
            p = 1 / (1 + math.exp(-max(-30.0, min(30.0, b + sum(wi * zi for wi, zi in zip(w, z, strict=True))))))
            e = p - y
            gb += e
            for j in range(k):
                gw[j] += e * z[j]
        b -= lr * gb / n
        w = [w[j] - lr * (gw[j] / n + l2 * w[j]) for j in range(k)]
    return Model(mu, sd, w, b)


def ece(ps: list[float], ys: list[int], bins: int = 10) -> float:
    total = 0.0
    for b in range(bins):
        idx = [i for i, p in enumerate(ps) if (b / bins <= p < (b + 1) / bins) or (b == bins - 1 and p == 1.0)]
        if idx:
            conf = sum(ps[i] for i in idx) / len(idx)
            acc = sum(ys[i] for i in idx) / len(idx)
            total += abs(acc - conf) * len(idx) / len(ps)
    return total


@dataclass
class MetaResult:
    n_oof: int
    n_folds_used: int
    ece: float | None
    brier: float | None
    brier_base: float | None
    calibrated: bool
    keep_share: float | None
    improvement: tuple[float, float, float] | None  # mittlere Verbesserung je Kandidat (R) mit 95 %-KI
    pipeline: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ZU_WENIG_DATEN"


def run(prep: Prepared, folds: list[Fold], outs: list[FoldOut], seed: int) -> MetaResult:
    tables = {inst: feature_table(prep, inst) for inst in prep.ds.instruments}
    ps: list[float] = []
    ys: list[int] = []
    base: list[float] = []
    rs: list[float] = []
    pipeline: list[dict[str, Any]] = []
    used = 0
    by_fold = {f.index: f for f in folds}
    for fo in outs:
        fold = by_fold[fo.fold]
        train = [e for e in examples(prep, fo.train_trades or [], tables) if e.label_at <= fold.train_end]
        test = examples(prep, fo.trades, tables)
        entry: dict[str, Any] = {"fold": fo.fold, "train_end": fold.train_end.isoformat(), "n_train": len(train), "n_test": len(test),
                 "max_label_at": max((e.label_at for e in train), default=None), "scaler_fitted_on": "train"}
        if entry["max_label_at"] is not None:
            entry["max_label_at"] = entry["max_label_at"].isoformat()
        if len(train) < MIN_TRAIN or not test or len({e.y for e in train}) < 2:
            entry["skipped"] = "zu wenige Trainingsbeispiele"
            pipeline.append(entry)
            continue
        model = fit([e.x for e in train], [e.y for e in train])
        rate = sum(e.y for e in train) / len(train)
        for e in test:
            ps.append(model.predict(e.x))
            ys.append(e.y)
            base.append(rate)
            rs.append(e.r)
        used += 1
        pipeline.append(entry)
    n = len(ps)
    if n == 0:
        return MetaResult(0, used, None, None, None, False, None, None, pipeline)
    calib_err = ece(ps, ys)
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys, strict=True)) / n
    brier_base = sum((p - y) ** 2 for p, y in zip(base, ys, strict=True)) / n
    calibrated = n >= MIN_OOF and calib_err <= MAX_ECE and brier < brier_base
    keep = [p >= 0.5 for p in ps]
    diffs = [0.0 if k else -r for k, r in zip(keep, rs, strict=True)]  # Auslassen = Ergebnis 0 statt r
    imp = paired_bootstrap(diffs, seed)
    status = "ZU_WENIG_DATEN" if n < MIN_OOF else ("KANDIDAT" if calibrated and imp is not None and imp[1] > 0 else "KEIN_BELASTBARER_FORTSCHRITT")
    return MetaResult(n, used, calib_err, brier, brier_base, calibrated, sum(keep) / n, imp, pipeline, status)
