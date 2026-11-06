"""Invest-Topf (docs/11, E-12): Kern, Satelliten mit und ohne Nachrichten, Halten, Gewichtung und Profile."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from helpers import make_candles

from tradingengine.adapters.pg_paper import policy_from_json
from tradingengine.core.news import NewsPoint, news_asset, news_score
from tradingengine.core.regime import daily_regimes
from tradingengine.core.signals import Action
from tradingengine.core.strategies import ACTIVE, INVEST
from tradingengine.core.strategies import invest as inv
from tradingengine.services.paper import HOLD_POLICY, INVEST_POLICY, PROFILES, profile_of

N = 320
MONDAY = datetime(2026, 11, 2, tzinfo=UTC)  # Montag 00:00 UTC = Schluss der Sonntagskerze
START = MONDAY - timedelta(days=N)
BTC, ETH, QNT = "KRAKEN:BTC/USD", "KRAKEN:ETH/USD", "KRAKEN:QNT/USD"


def _trend(n: int, start: float, daily: float) -> list[float]:
    return [start * (1 + daily) ** i * (1 + 0.004 * ((-1) ** i)) for i in range(n)]


def _btc():
    return make_candles(_trend(N, 50_000, 0.001), "1d", BTC, START)


def _qnt(volume_jump: bool = True):
    closes = _trend(N - 90, 80, 0.0005) + _trend(90, 80 * 1.0005 ** (N - 90), 0.008)
    volumes = [100.0] * (N - 20) + ([200.0] * 20 if volume_jump else [100.0] * 20)
    return make_candles(closes, "1d", QNT, START, volumes=volumes)


def test_core_buys_btc_above_sma200_and_ignores_others() -> None:
    btc = _btc()
    d = inv.core_decide(btc, daily_regimes(btc), daily_regimes(btc))[-1]
    assert d.action is Action.BUY and d.stop is not None and d.stop < d.entry  # type: ignore[operator]
    assert inv.core_exit_plan(d).exit_unless_regime == inv.ALL_REGIMES_BUT_DOWN
    falling = make_candles(_trend(N, 50_000, -0.002), "1d", BTC, START)
    assert inv.core_decide(falling, daily_regimes(falling), daily_regimes(falling))[-1].action is Action.NO_TRADE
    qnt = _qnt()
    assert "nur BTC und ETH" in inv.core_decide(qnt, daily_regimes(qnt), daily_regimes(_btc()))[-1].triggers[0]


def test_satellite_needs_strength_against_btc_rising_volume_and_the_weekly_close() -> None:
    btc, qnt = _btc(), _qnt()
    lead = daily_regimes(btc)
    d = inv.satellite_decide(qnt, daily_regimes(qnt), lead)[-1]
    assert d.action is Action.BUY, d.triggers
    assert any("gegenüber BTC" in t for t in d.triggers) and any("Volumen" in t for t in d.triggers)
    flat_volume = _qnt(volume_jump=False)
    assert "Volumen steigt nicht genug" in inv.satellite_decide(flat_volume, daily_regimes(flat_volume), lead)[-1].triggers[0]
    # ein Tag vorher (Sonntag): kein Entscheid
    assert "Wochenschluss" in inv.satellite_decide(qnt[:-1], daily_regimes(qnt[:-1]), lead)[-1].triggers[0]
    assert "ausser BTC/ETH" in inv.satellite_decide(btc, lead, lead)[-1].triggers[0]


def test_news_satellite_uses_only_news_stored_before_the_close() -> None:
    btc, qnt = _btc(), _qnt(volume_jump=False)
    lead, own = daily_regimes(btc), daily_regimes(qnt)
    good = [NewsPoint(MONDAY - timedelta(days=3), 2, 80, "OFFICIAL", "COIN")]
    assert news_score(good, MONDAY) == 1.6
    d = inv.news_satellite_decide(qnt, own, lead, good)[-1]
    assert d.action is Action.BUY and any("Nachrichten-Rückenwind 1.6" in t for t in d.triggers)
    late = [NewsPoint(MONDAY + timedelta(minutes=5), 2, 80, "OFFICIAL", "COIN")]  # nach dem Kerzenschluss eingeordnet
    assert inv.news_satellite_decide(qnt, own, lead, late)[-1].action is Action.NO_TRADE
    rumor = [NewsPoint(MONDAY - timedelta(days=1), 2, 90, "RUMOR", "COIN")]
    assert inv.news_satellite_decide(qnt, own, lead, rumor)[-1].action is Action.NO_TRADE
    mixed = good + [NewsPoint(MONDAY - timedelta(days=2), -2, 60, "OFFICIAL", "COIN")]
    assert "Negative Nachrichten" in inv.news_satellite_decide(qnt, own, lead, mixed)[-1].triggers[0] or \
        inv.news_satellite_decide(qnt, own, lead, mixed)[-1].action is Action.NO_TRADE
    assert news_asset("XBT") == "BTC" and news_asset("XDG") == "DOGE" and news_asset("qnt") == "QNT"


def test_hold_buys_core_with_far_stop() -> None:
    btc = _btc()
    d = inv.hold_decide(btc, daily_regimes(btc), daily_regimes(btc))[-1]
    assert d.action is Action.BUY and d.stop is not None and d.stop <= d.entry * D("0.05")  # type: ignore[operator]


def test_profiles_policies_and_registration() -> None:
    ids = {s.version.id: s for s in INVEST}
    assert all(s.timeframes == frozenset({"1d"}) and s.target_weight for s in INVEST)
    assert not ids["h1-hold@1"].notify and ids["i3-satellite-news@1"].decide_news is not None
    assert not set(ids) & {s.version.id for s in ACTIVE}
    assert PROFILES["INVEST_NEWS"][1] == ["i1-core-trend@1", "i3-satellite-news@1"]
    assert profile_of("risk-invest@1", ["i1-core-trend@1", "i2-satellite-strength@1"]) == "INVEST_RULES"
    assert profile_of("risk@2", ["s1-trend-pullback@1"]) == "TRADING"
    # die gespeicherte Policy kommt unverändert zurück
    from dataclasses import asdict

    for p in (INVEST_POLICY, HOLD_POLICY):
        stored = {k: (str(v) if isinstance(v, D) else v) for k, v in asdict(p).items()}
        assert policy_from_json(stored) == p
