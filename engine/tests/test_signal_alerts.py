"""Text der Kaufsignal-Meldung (rein, ohne Datenbank)."""

from __future__ import annotations

from decimal import Decimal

from tradingengine.notify.signal_alerts import signal_text

ROW = {
    "instrument_id": "KRAKEN:ETH/USD",
    "timeframe": "4h",
    "strategy_version_id": "s1-trend-pullback@1",
    "name": "Ether / US-Dollar",
    "score": 90,
    "entry": Decimal("2726.49"),
    "stop": Decimal("2678.70"),
    "target": None,
    "triggers": ["Regime Aufwärtstrend", "Rücksetzer an EMA20"],
    "counter": ["Kosten prüfen"],
}


def test_signal_text_names_pair_levels_and_paper_outcome() -> None:
    title, body = signal_text({**ROW, "outcomes": [{"status": "ORDERED", "reasons": []}]})
    assert title == "Kaufsignal ETH/USD (4h)"
    assert "Trend-Rücksetzer (s1-trend-pullback@1)" in body
    assert "Einstieg 2'726.49 · Stopp 2'678.70 (−1.8 %) · Ausstieg über nachgezogenen Stopp" in body
    assert "keine Wahrscheinlichkeit" in body
    assert "Dafür: Regime Aufwärtstrend; Rücksetzer an EMA20" in body
    assert "virtueller Kauf ausgelöst" in body
    assert body.endswith("keine Anlageempfehlung.")


def test_signal_text_explains_blocked_or_open_outcome() -> None:
    blocked = [{"status": "BLOCKED", "reasons": ["Gesamtrisiko 248.49 > 247.46", "Gesamtrisiko 248.49 > 247.46"]}]
    _, body = signal_text({**ROW, "outcomes": blocked})
    assert "nicht gekauft – Gesamtrisiko 248.49 > 247.46." in body
    _, body = signal_text({**ROW, "outcomes": None, "entry": Decimal("1.52016"), "stop": Decimal("1.48098")})
    assert "Einstieg 1.5202 · Stopp 1.4810" in body
    assert "noch nicht entschieden" in body
