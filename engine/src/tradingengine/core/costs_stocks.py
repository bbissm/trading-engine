"""Kostenmodell für US-Aktien/ETFs über Interactive Brokers (IBKR Pro, «Fixed»), Stand 4.10.2026 (docs/03, 2.4).

Gebühr je Order: USD 0.005 je Aktie, mindestens USD 1.00, höchstens 1 % des Handelswerts. Im Fixed-Tarif sind
Börsen- und Regulierungsgebühren enthalten (laut IBKR-Preisseite; Ausnahmen wie die FINRA-TAF auf Verkäufe sind
hier vernachlässigt). Die Höchstgrenze gewinnt gegen die Mindestgebühr: eine Order über USD 50 kostet USD 0.50.

Slippage: Annahme 5 bp gegenüber dem Auslöse- bzw. Eröffnungspreis für Stop- und Market-Ausführungen in den
liquiden ETFs und Large Caps des Start-Universums. Wird erst mit echten Ausführungen (IBKR Paper/Live)
überprüfbar. Der Bid/Ask-Spread fehlt wie im Krypto-Modell (Kerzen statt Quotes).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .costs import CostModel


@dataclass(frozen=True, slots=True)
class PerShareCostModel(CostModel):
    """Gebühr je Stück mit Mindest- und Höchstbetrag je Order.

    `maker_bps`/`taker_bps` tragen die Höchstgrenze (1 % = 100 bp). Sie dienen nur Aufrufern, die einen
    Gebührensatz für Reservierungen brauchen (`core.paper.submit_entries`), und sind dort eine obere Schranke.
    """

    per_share: Decimal
    min_fee: Decimal
    max_rate: Decimal  # Anteil des Handelswerts, z. B. 0.01

    def order_fee(self, qty: Decimal, price: Decimal, taker: bool) -> Decimal:
        if qty <= 0:
            return Decimal(0)
        return min(max(self.per_share * qty, self.min_fee), self.max_rate * qty * price)

    def fee(self, notional: Decimal, taker: bool) -> Decimal:
        """Grenzgebühr für *eine* Aktie zum Preis `notional`, ohne Mindestgebühr (für Kosten je Einheit in
        `plan_costs`). Ausführungen rechnen mit `order_fee`; die Mindestgebühr berücksichtigt `size_position`."""
        return min(self.per_share, self.max_rate * notional)

    @property
    def linear(self) -> bool:
        return False

    def scaled(self, factor: Decimal) -> PerShareCostModel:
        return PerShareCostModel(
            f"{self.version}×{factor}", self.maker_bps, self.taker_bps, self.slippage_bps * factor,
            self.per_share * factor, self.min_fee * factor, self.max_rate * factor,
        )


IBKR_FIXED_US = PerShareCostModel(
    version="ibkr-fixed-us@2026-10",
    maker_bps=Decimal(100),
    taker_bps=Decimal(100),
    slippage_bps=Decimal(5),
    per_share=Decimal("0.005"),
    min_fee=Decimal("1.00"),
    max_rate=Decimal("0.01"),
)

STOCK_COST_MODELS: dict[str, CostModel] = {IBKR_FIXED_US.version: IBKR_FIXED_US}
