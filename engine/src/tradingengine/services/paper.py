"""Paper-Autopilot: schreibt jedes virtuelle Konto pro Tick fort (docs/08, E2-7/E2-8).

Reihenfolge je Konto: (1) Simulation über neu abgeschlossene 4h-Kerzen, (2) neue BUY-Signale → Orders
(nur im Zustand ACTIVE), (3) Abschluss einer Abwicklung. Alles ohne Handelskonto; Fills sind simuliert.

Autopilot-Zustände (docs/01, 3.1): READY → ACTIVE ⇄ ENTRIES_PAUSED → WINDING_DOWN → STOPPED.
Solange Positionen bestehen, werden sie in jedem Zustand ausser READY weiter betreut.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from ..adapters.pg_paper import PaperRepo, policy_from_json
from ..core import indicators as ind
from ..core.candles import Candle, floor_time
from ..core.costs import KRAKEN_SPOT_TIER1, CostModel
from ..core.paper import Management, PaperState, RiskInputs, close_all, pause_entries, step_candle, submit_entries
from ..core.regime import RegimePoint, daily_regimes, regime_at
from ..core.risk import RiskPolicy
from ..core.simulator import SimConfig
from ..core.strategies import ACTIVE, StrategyDef
from ..ports import Command

log = logging.getLogger(__name__)

EXEC_TIMEFRAME = "4h"
REGIME_TIMEFRAME = "1d"
LOOKBACK = 600
ZURICH = ZoneInfo("Europe/Zurich")
COST_MODELS: dict[str, CostModel] = {KRAKEN_SPOT_TIER1.version: KRAKEN_SPOT_TIER1}
SIM = SimConfig()
MANAGED_STATES = {"ACTIVE", "ENTRIES_PAUSED", "WINDING_DOWN", "STOPPED", "ERROR"}


def _day_and_week_start(now: datetime) -> tuple[datetime, datetime]:
    """Tages- und Wochenwechsel in Europe/Zurich (docs/06, 2.2), als UTC-Zeitpunkte."""
    local = now.astimezone(ZURICH)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    week = day - timedelta(days=day.weekday())
    return day.astimezone(UTC), week.astimezone(UTC)


def _atr_at(candles: list[Candle]) -> float | None:
    if not candles:
        return None
    return ind.atr([float(c.high) for c in candles], [float(c.low) for c in candles], [float(c.close) for c in candles], 14)[-1]


def _strategies(ids: list[str]) -> list[StrategyDef]:
    return [s for s in ACTIVE if s.version.id in ids]


def _advance(repo: PaperRepo, state: PaperState, model: CostModel, ticks: dict[str, Decimal | None], now: datetime) -> int:
    """Simulation über alle neu abgeschlossenen 4h-Kerzen, in zeitlicher Reihenfolge."""
    steps = 0
    regimes: dict[str, list[RegimePoint]] = {}
    for close_time in repo.new_closes(EXEC_TIMEFRAME, state.sim_through, now):
        involved = sorted({o.order.instrument_id for o in state.orders.values()} | set(state.trades))
        candles = repo.candles_at(EXEC_TIMEFRAME, close_time, involved)
        management: dict[str, Management] = {}
        for instrument_id, trade in state.trades.items():
            if trade.timeframe != EXEC_TIMEFRAME and floor_time(close_time, trade.timeframe) != close_time:
                continue  # die Kerze der Signal-Zeitebene dieses Trades schliesst jetzt nicht
            history = repo.candles_until(instrument_id, trade.timeframe, close_time, LOOKBACK)
            if not history or history[-1].close_time != close_time:
                continue  # fällige Kerze fehlt (noch): Betreuung wird nachgeholt, sobald sie vorliegt
            if instrument_id not in regimes:
                regimes[instrument_id] = daily_regimes(repo.candles_until(instrument_id, REGIME_TIMEFRAME, now, LOOKBACK))
            management[instrument_id] = Management(history[-1], _atr_at(history), regime_at(regimes[instrument_id], close_time))
        step_candle(state, close_time, candles, management, repo.last_prices(EXEC_TIMEFRAME, close_time), model, SIM, ticks, state.account.currency)
        steps += 1
    return steps


def _risk_inputs(repo: PaperRepo, state: PaperState, row: dict[str, Any], policy: RiskPolicy, feed_ok: dict[str, bool], now: datetime) -> RiskInputs:
    day_start, week_start = _day_and_week_start(now)
    start_cash: Decimal = row["start_cash"]
    peak = repo.peak_equity(state.episode_id)
    losses: dict[str, int] = {}
    for strategy_id in row["strategy_version_ids"]:
        recent = repo.recent_closed(state.episode_id, strategy_id, policy.loss_streak_cooldown + 5)
        streak = 0
        for net, _ in recent:
            if net >= 0:
                break
            streak += 1
        # Wartezeit abgelaufen: die Serie beginnt neu
        if streak >= policy.loss_streak_cooldown and recent and now >= recent[0][1] + timedelta(hours=policy.loss_streak_cooldown_hours):
            streak = 0
        losses[strategy_id] = streak
    return RiskInputs(
        day_start_equity=repo.equity_at_or_before(state.episode_id, day_start) or start_cash,
        week_start_equity=repo.equity_at_or_before(state.episode_id, week_start) or start_cash,
        peak_equity=max(peak, start_cash) if peak is not None else start_cash,
        entries_today=repo.entries_since(state.episode_id, day_start),
        losses_by_strategy=losses,
        entries_allowed=True,
        fresh=feed_ok,
    )


def run_accounts(repo: PaperRepo, feed_status: dict[str, str], now: datetime) -> dict[str, Any]:
    """Ein Tick für alle Paper-Konten. `feed_status`: Status je "instrument|timeframe"."""
    summary: dict[str, Any] = {}
    specs, ticks, _ = repo.specs()
    for row in repo.accounts():
        account_id, ap_state = row["id"], row["state"]
        if ap_state not in MANAGED_STATES:
            repo.mark_signals_seen(row["episode_id"], now)  # READY: nichts handeln, alte Signale nicht nachholen
            continue
        model = COST_MODELS.get(row["cost_model"], KRAKEN_SPOT_TIER1)
        policy = policy_from_json(row["policy"])
        state = repo.load_state(account_id)
        steps = _advance(repo, state, model, ticks, now)

        submitted = 0
        if ap_state == "ACTIVE":
            pending = repo.new_buy_signals(row["signals_through"], row["strategy_version_ids"])
            if pending:
                prices = repo.last_prices(EXEC_TIMEFRAME, now)
                feed_ok = {i: feed_status.get(f"{i}|{EXEC_TIMEFRAME}") == "OK" for i in ticks}
                before = len(state.outcomes)
                submit_entries(state, [s for s, _ in pending], now, prices, _risk_inputs(repo, state, row, policy, feed_ok, now), policy, model,
                               specs, _strategies(row["strategy_version_ids"]))
                submitted = sum(1 for o in state.outcomes[before:] if o.status == "ORDERED")
        repo.save_state(state, now)
        repo.mark_signals_seen(state.episode_id, now)

        if ap_state == "WINDING_DOWN" and not state.trades and not state.orders:
            repo.set_autopilot(account_id, "STOPPED", "Abwicklung abgeschlossen: kein Bestand, keine offenen Orders", now)
            ap_state = "STOPPED"
        summary[account_id] = {"state": ap_state, "steps": steps, "orders": submitted, "open_trades": len(state.trades)}
    return summary


# --- Bedienbefehle ---------------------------------------------------------------------------------

MAX_START_CASH = Decimal(100_000_000)


def _start_cash(params: dict[str, Any]) -> Decimal | None:
    try:
        value = Decimal(str(params.get("start_cash")))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() and 0 < value <= MAX_START_CASH else None


def handle_command(repo: PaperRepo, cmd: Command, now: datetime) -> tuple[str, dict[str, Any]]:
    """Verarbeitet PAPER_*-Befehle. Rückgabe: (DONE | REJECTED, Ergebnis)."""
    sim_through = floor_time(now, EXEC_TIMEFRAME)
    if cmd.type == "PAPER_CREATE":
        cash = _start_cash(cmd.params)
        name = str(cmd.params.get("name") or "").strip()[:60]
        if cash is None or not name:
            return "REJECTED", {"reason": "Name und Startkapital (> 0) erforderlich"}
        existing = {a["id"] for a in repo.accounts()}
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "konto"
        account_id = next(c for c in (f"paper-{slug}" if n == 1 else f"paper-{slug}-{n}" for n in range(1, 1000)) if c not in existing)
        repo.create_account(account_id, name, "USD", cash, RiskPolicy(), [s.version.id for s in ACTIVE], KRAKEN_SPOT_TIER1.version, SIM.version,
                            now, sim_through)
        return "DONE", {"account_id": account_id, "state": "READY"}

    rows = {a["id"]: a for a in repo.accounts()}
    row = rows.get(cmd.target or "")
    if row is None:
        return "REJECTED", {"reason": f"Paper-Konto {cmd.target} nicht gefunden"}
    account_id, current = row["id"], row["state"]

    if cmd.type == "PAPER_START":
        if current not in ("READY", "STOPPED", "ENTRIES_PAUSED"):
            return "REJECTED", {"reason": f"Start aus Zustand {current} nicht möglich"}
        if current != "ENTRIES_PAUSED":
            repo.set_sim_through(row["episode_id"], sim_through)  # ab jetzt simulieren, nichts rückwirkend
        repo.mark_signals_seen(row["episode_id"], now)  # nur Signale ab jetzt
        repo.set_autopilot(account_id, "ACTIVE", "Gestartet", now)
        return "DONE", {"state": "ACTIVE"}

    if cmd.type in ("PAPER_PAUSE", "PAPER_STOP", "PAPER_CLOSE_ALL"):
        if current not in ("ACTIVE", "ENTRIES_PAUSED", "WINDING_DOWN"):
            return "REJECTED", {"reason": f"Im Zustand {current} nicht möglich"}
        state = repo.load_state(account_id)
        if cmd.type == "PAPER_CLOSE_ALL":
            affected = close_all(state, now)
            new_state, reason = "WINDING_DOWN", f"Positionen schliessen: {affected} Market-Exit(s) erteilt"
        else:
            affected = pause_entries(state, now)
            new_state = "ENTRIES_PAUSED" if cmd.type == "PAPER_PAUSE" else "WINDING_DOWN"
            reason = f"{'Einstiege pausiert' if cmd.type == 'PAPER_PAUSE' else 'Geordnet stoppen'}: {affected} Einstiegsorder(s) storniert"
        repo.save_state(state, now)
        repo.set_autopilot(account_id, new_state, reason, now)
        return "DONE", {"state": new_state, "affected": affected, "open_trades": len(state.trades)}

    if cmd.type == "PAPER_RESET":
        cash = _start_cash(cmd.params)
        if cash is None:
            return "REJECTED", {"reason": "Startkapital (> 0) erforderlich"}
        open_trades, open_orders = repo.open_counts(row["episode_id"])
        if open_trades or open_orders:
            return "REJECTED", {"reason": f"Reset erst ohne Bestand möglich ({open_trades} Position(en), {open_orders} Order(s) offen)"}
        number = repo.reset_account(account_id, cash, RiskPolicy(), [s.version.id for s in ACTIVE], KRAKEN_SPOT_TIER1.version, SIM.version,
                                    now, sim_through)
        repo.set_autopilot(account_id, "READY", f"Neue Episode {number}", now)
        return "DONE", {"state": "READY", "episode": number}

    return "REJECTED", {"reason": f"Unbekannter Befehl {cmd.type}"}
