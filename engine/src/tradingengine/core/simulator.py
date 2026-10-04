"""Ausführungssimulator auf Kerzenbasis für Backtest und Paper (docs/01 Abschnitt 9 des Auftrags, docs/07 T21).

Konservative, dokumentierte Annahmen – ein Modell, kein Nachweis echter Ausführungsqualität:
- Eine Limit-Order gilt nicht als gefüllt, wenn der Kurs das Limit nur berührt; er muss es durchhandeln.
- Eröffnet die Kerze jenseits des Limits, wird zum Eröffnungskurs gefüllt (nie besser als der Markt).
- Stop-Orders füllen zum schlechteren aus Stop und Eröffnung (Kurslücke), abzüglich Slippage, als Taker.
- Liegen Stop und Ziel in derselben Kerze, gilt der Stop als zuerst erreicht (Aufrufer prüft Stop zuerst).
- Einstiege füllen höchstens einen Anteil des Kerzenvolumens (Teilfüllung möglich).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from .candles import Candle
from .costs import BPS, CostModel
from .execution import Fill, Order, OrderType, Side


@dataclass(frozen=True, slots=True)
class SimConfig:
    version: str = "sim@1"
    participation: Decimal = Decimal("0.01")  # max. Anteil am Kerzenvolumen je Einstiegsorder
    qty_step: Decimal = Decimal("0.00000001")
    # Eine Order, die bis zu dieser Frist nach Kerzenbeginn erteilt wurde, nimmt an der Kerze teil
    # (Signal kurz nach Kerzenschluss → Order in der Folgekerze). Später erteilte warten auf die nächste Kerze.
    entry_grace: timedelta = timedelta(minutes=5)


# Realismus-Karte: was der Simulator abbildet und was fehlt (wird im Paper-Lab angezeigt).
REALISM: dict[str, str] = {
    "Gebühren beider Seiten": "simuliert (Kostenmodell des Handelsplatzes)",
    "Limit nur bei Durchhandeln": "simuliert",
    "Kurslücken bei Stops": "simuliert (schlechterer aus Stop und Eröffnung)",
    "Slippage bei Stop/Market": "simuliert (fester Abschlag in Basispunkten – Annahme)",
    "Teilfüllungen": "simuliert (Anteil am Kerzenvolumen)",
    "Reihenfolge Stop/Ziel in einer Kerze": "konservativ: Stop zuerst",
    "Bid/Ask-Spread": "fehlt – es werden Kerzen (letzte Preise) verwendet, keine Quotes",
    "Latenz und Warteschlangenposition": "fehlt",
    "Marktwirkung eigener Orders": "fehlt",
    "Wartungsfenster und Handelsunterbrüche": "fehlt",
    "Mindestmengen und Präzision": "simuliert über die Instrument-Spezifikation",
}


def _floor(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _fill(order: Order, candle: Candle, qty: Decimal, price: Decimal, model: CostModel, taker: bool, currency: str) -> Fill:
    seq = len(order.fills) + 1
    return Fill(
        id=f"{order.id}:{seq}",
        order_id=order.id,
        qty=qty,
        price=price,
        fee=model.fee(qty * price, taker),
        fee_currency=currency,
        time=candle.close_time,
        simulated=True,
    )


def simulate(order: Order, candle: Candle, model: CostModel, cfg: SimConfig, currency: str) -> Fill | None:
    """Möglicher Fill einer arbeitenden Order innerhalb einer Kerze, die *nach* der Ordererteilung begann."""
    if candle.open_time + cfg.entry_grace < order.created_at:
        return None  # eine Order handelt nie rückwirkend vor ihrer Entstehung
    remaining = order.remaining_qty
    if remaining <= 0:
        return None

    if order.side is Side.BUY and order.type is OrderType.LIMIT:
        limit = order.limit_price
        assert limit is not None
        if candle.open <= limit:
            price, taker = candle.open, True  # marktfähig bei Eröffnung
        elif candle.low < limit:
            price, taker = limit, False
        else:
            return None  # nur berührt oder nie erreicht
        qty = min(remaining, _floor(candle.volume * cfg.participation, cfg.qty_step))
        return _fill(order, candle, qty, price, model, taker, currency) if qty > 0 else None

    if order.side is Side.SELL and order.type is OrderType.STOP:
        stop = order.stop_price
        assert stop is not None
        if candle.low > stop:
            return None
        price = min(stop, candle.open) * (1 - model.slippage_bps / BPS)
        return _fill(order, candle, remaining, price, model, True, currency)

    if order.side is Side.SELL and order.type is OrderType.MARKET:
        price = candle.open * (1 - model.slippage_bps / BPS)
        return _fill(order, candle, remaining, price, model, True, currency)

    if order.side is Side.SELL and order.type is OrderType.LIMIT:
        limit = order.limit_price
        assert limit is not None
        if candle.open >= limit:
            return _fill(order, candle, remaining, candle.open, model, False, currency)
        if candle.high > limit:
            return _fill(order, candle, remaining, limit, model, False, currency)
        return None

    raise ValueError(f"Nicht unterstützte Order: {order.side.value} {order.type.value}")
