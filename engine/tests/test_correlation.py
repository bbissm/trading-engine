"""Gleichlauf der Coins und Gruppen-Grenze der Risikoprüfung (docs/11, E-11)."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from tradingengine.core.account import Account
from tradingengine.core.correlation import correlation, exposure, group_label, log_returns
from tradingengine.core.risk import EntryRequest, RiskContext, RiskPolicy, check_entry

T0 = datetime(2026, 7, 1, tzinfo=UTC)
NOW = T0 + timedelta(days=100)
BTC, ETH, SOL, PAXG, NEW = "KRAKEN:BTC/USD", "KRAKEN:ETH/USD", "KRAKEN:SOL/USD", "KRAKEN:PAXG/USD", "KRAKEN:NEW/USD"


def _series(returns: list[float], start: float = 100.0, offset: int = 0) -> list[tuple[datetime, D]]:
    price, out = start, [(T0 + timedelta(days=offset), D(str(start)))]
    for n, r in enumerate(returns, start=1):
        price *= math.exp(r)
        out.append((T0 + timedelta(days=offset + n), D(f"{price:.6f}")))
    return out


def _market() -> dict[str, list[tuple[datetime, D]]]:
    rng = random.Random(7)
    btc = [rng.gauss(0, 0.03) for _ in range(99)]
    eth = [1.2 * b + rng.gauss(0, 0.008) for b in btc]  # läuft eng mit BTC
    sol = [1.5 * e + rng.gauss(0, 0.01) for e in eth]  # eng mit ETH, damit auch mit BTC verkettet
    gold = [rng.gauss(0, 0.01) for _ in range(99)]  # unabhängig
    return {BTC: _series(btc), ETH: _series(eth), SOL: _series(sol), PAXG: _series(gold), NEW: _series(btc[:10], offset=89)}


LEADERS = {BTC: None, ETH: BTC, SOL: BTC, PAXG: BTC, NEW: BTC}


def test_correlation_groups_lockstep_coins_and_keeps_independent_ones_apart() -> None:
    m = _market()
    rets = {k: log_returns(v, NOW) for k, v in m.items()}
    c = correlation(rets[BTC], rets[ETH])
    assert c is not None and c > 0.9
    gold = correlation(rets[BTC], rets[PAXG])
    assert gold is not None and abs(gold) < 0.4

    ex = exposure(m, LEADERS, NOW)
    assert ex.group_of[BTC] == ex.group_of[ETH] == ex.group_of[SOL]
    assert ex.group_of[PAXG] != ex.group_of[BTC]
    # zu kurze Geschichte: vorsichtig zur Gruppe des Leitinstruments
    assert ex.group_of[NEW] == ex.group_of[BTC]
    assert ex.members(ETH) == sorted([BTC, ETH, SOL, NEW])
    b = ex.beta[ETH]
    assert b is not None and 1.1 < b < 1.3
    assert ex.beta[BTC] == 1.0
    assert group_label(ex.members(ETH)) == "BTC/ETH/NEW/SOL"


def test_only_closed_candles_until_now_count() -> None:
    m = _market()
    early = exposure(m, LEADERS, T0 + timedelta(days=20))
    # vor genug gemeinsamer Geschichte gibt es keine Korrelation: alle hängen vorsichtig an BTC
    assert len({early.group_of[i] for i in (BTC, ETH, SOL, PAXG)}) == 1


def _req(instrument: str, risk: str = "50") -> EntryRequest:
    return EntryRequest(f"k-{instrument}", instrument, "s1-trend-pullback@1", D(1), D(100), D(risk), D(101))


def _ctx(groups: dict[str, str] | None) -> RiskContext:
    return RiskContext(equity=D(10000), day_start_equity=D(10000), week_start_equity=D(10000), peak_equity=D(10000),
                       entries_today=0, consecutive_losses=0, entries_allowed=True, data_fresh=True, groups=groups)


def test_group_limit_blocks_a_third_bet_on_the_same_move_but_not_an_independent_coin() -> None:
    groups = {BTC: BTC, ETH: BTC, SOL: BTC, PAXG: PAXG}
    acc = Account("a", "USD", D(10000))
    acc.reserve("k1", BTC, D(1), D(101), D(50))
    acc.buy_fill("k1", D(1), D(100), D(0), "s1")
    acc.reserve("k2", ETH, D(1), D(101), D(50))  # zweite Wette in derselben Gruppe, noch offen

    sol = check_entry(_req(SOL), acc, _ctx(groups), RiskPolicy())
    assert not sol.allowed
    assert any("Gleichläufige Gruppe BTC/ETH/SOL: schon 2 Position(en), höchstens 2" in r for r in sol.reasons)
    assert sol.values["gruppe"] == "BTC/ETH/SOL"

    gold = check_entry(_req(PAXG), acc, _ctx(groups), RiskPolicy())
    assert gold.allowed, gold.reasons

    # ohne Gruppen (Backtest, Live) gilt die alte Prüfung unverändert
    assert check_entry(_req(SOL), acc, _ctx(None), RiskPolicy()).allowed


def test_group_risk_limit() -> None:
    groups = {BTC: BTC, ETH: BTC}
    acc = Account("a", "USD", D(10000))
    acc.reserve("k1", BTC, D(1), D(101), D(60))
    d = check_entry(_req(ETH, "45"), acc, _ctx(groups), RiskPolicy())
    assert not d.allowed
    assert any(r.startswith("Gruppenrisiko BTC/ETH 105.00 > 100.00") for r in d.reasons)
    assert RiskPolicy().version == "risk@2"


def test_old_episode_policy_keeps_its_rules() -> None:
    from tradingengine.adapters.pg_paper import policy_from_json

    old = policy_from_json({"version": "risk@1", "max_positions": 6})
    assert old.version == "risk@1" and old.max_group_positions == 1_000_000 and old.max_group_risk == D(1)
    new = policy_from_json({"version": "risk@2", "max_group_positions": 2, "max_group_risk": "0.01"})
    assert new.max_group_positions == 2 and new.max_group_risk == D("0.01")
