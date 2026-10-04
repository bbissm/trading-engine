"""Versioniertes Kostenmodell je Handelsplatz (docs/09, Abschnitt 3). Sätze in Basispunkten (1 bp = 0.01 %)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

BPS = Decimal(10000)


@dataclass(frozen=True, slots=True)
class CostModel:
    version: str
    maker_bps: Decimal
    taker_bps: Decimal
    # Annahme für marktnahe Ausführungen (Stop, Market): Abschlag gegenüber dem Auslösepreis
    slippage_bps: Decimal

    def fee(self, notional: Decimal, taker: bool) -> Decimal:
        return notional * (self.taker_bps if taker else self.maker_bps) / BPS

    def order_fee(self, qty: Decimal, price: Decimal, taker: bool) -> Decimal:
        """Gebühr einer Ausführung über `qty` Einheiten. Modelle mit Gebühr je Stück oder Mindestgebühr überschreiben das."""
        return self.fee(qty * price, taker)

    @property
    def linear(self) -> bool:
        """True, wenn die Gebühr proportional zum Wert ist (keine Mindest- oder Stückgebühr)."""
        return True

    def scaled(self, factor: Decimal) -> CostModel:
        """Für Sensitivitätsläufe (Kosten × 1.5, × 2)."""
        return CostModel(f"{self.version}×{factor}", self.maker_bps * factor, self.taker_bps * factor, self.slippage_bps * factor)


# Kraken Pro Spot, unterste Volumenstufe, Stand 4.10.2026 (docs/03): 0.40 % Maker / 0.80 % Taker.
# Slippage 5 bp ist eine Annahme für liquide USD-Paare und wird erst im Live-Pilot überprüfbar.
KRAKEN_SPOT_TIER1 = CostModel("kraken-spot-tier1@2026-10", Decimal(40), Decimal(80), Decimal(5))


@dataclass(frozen=True, slots=True)
class PlanCosts:
    """Geschätzte Kosten eines geplanten Trades je Einheit (Handelswährung)."""

    entry_fee: Decimal
    stop_exit_cost: Decimal  # Gebühr + Slippage, falls der Stop auslöst
    target_exit_fee: Decimal | None
    risk_per_unit: Decimal  # Einstieg − Stop + Kosten beider Seiten
    reward_per_unit: Decimal | None  # Ziel − Einstieg − Kosten beider Seiten
    net_reward_risk: Decimal | None


def plan_costs(model: CostModel, entry: Decimal, stop: Decimal, target: Decimal | None) -> PlanCosts:
    """Einstieg als Limit (Maker), Stop-Ausstieg als Taker mit Slippage, Ziel-Ausstieg als Limit (Maker)."""
    entry_fee = model.fee(entry, taker=False)
    stop_fill = stop * (1 - model.slippage_bps / BPS)
    stop_exit_cost = (stop - stop_fill) + model.fee(stop_fill, taker=True)
    risk = (entry - stop) + entry_fee + stop_exit_cost
    if target is None:
        return PlanCosts(entry_fee, stop_exit_cost, None, risk, None, None)
    target_fee = model.fee(target, taker=False)
    reward = (target - entry) - entry_fee - target_fee
    return PlanCosts(entry_fee, stop_exit_cost, target_fee, risk, reward, reward / risk if risk > 0 else None)
