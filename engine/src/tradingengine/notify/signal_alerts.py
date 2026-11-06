"""Meldung aufs Handy bei jedem neuen Kaufsignal (Stufe SIGNAL → Telegram, docs/06 4.2).

Läuft nach dem Paper-Autopiloten im selben Durchlauf, damit die Meldung gleich sagt, ob das Testkonto virtuell
gekauft hat oder warum nicht. Jedes Signal meldet sich genau einmal (Schlüssel `signal:<id>`, auch nach dem
Auflösen nie wieder). Nur Signale der letzten 12 Stunden: nach einem Ausfall kommt keine Flut alter Signale.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from ..core.strategies import INVEST
from .alerts import Conn, raise_alert

KIND = "SIGNAL_BUY"
MAX_AGE = timedelta(hours=12)
# Invest-Kaufsignale wiederholen sich täglich, solange die Bedingung gilt: höchstens einmal pro Woche je Coin melden
INVEST_REPEAT = timedelta(days=7)
QUIET = [s.version.id for s in INVEST if not s.notify]  # Vergleichsmassstab «Halten»: nie melden
INVEST_IDS = [s.version.id for s in INVEST]

STRATEGY_LABEL = {
    "s1-trend-pullback": "Trend-Rücksetzer",
    "s2-volume-breakout": "Ausbruch mit Volumen",
    "s3-mean-reversion": "Gegenbewegung",
    "i1-core-trend": "Invest Kern BTC/ETH",
    "i2-satellite-strength": "Invest Satellit (stärker als BTC, Volumen)",
    "i3-satellite-news": "Invest Satellit (stärker als BTC, Nachrichten)",
}

_NEW_BUYS = """
select s.id, s.instrument_id, s.timeframe, s.strategy_version_id, s.candle_close, s.score, s.entry, s.stop,
       s.target, s.triggers, s.counter, i.name,
       (select json_agg(json_build_object('status', o.status, 'reasons', o.reasons) order by o.created_at)
          from signal_outcome o where o.signal_id = s.id) as outcomes
from signal s join instrument i on i.id = s.instrument_id
where s.action = 'BUY' and s.created_at >= %s and not (s.strategy_version_id = any(%s))
  and not exists (select 1 from alert a where a.dedup_key = 'signal:' || s.id::text)
  and not (s.strategy_version_id = any(%s) and exists (
        select 1 from alert a where a.kind = 'SIGNAL_BUY' and a.data->>'instrument_id' = s.instrument_id
          and a.data->>'strategy' = s.strategy_version_id and a.created_at >= %s))
order by s.created_at
"""


def _price(v: Decimal) -> str:
    q = Decimal("0.01") if v >= 100 else Decimal("0.0001") if v >= 1 else Decimal("0.000001")
    return f"{v.quantize(q):,}".replace(",", "'")


def _strategy(version_id: str) -> str:
    name = version_id.split("@", 1)[0]
    return f"{STRATEGY_LABEL.get(name, name)} ({version_id})"


def _outcome(outcomes: list[dict[str, Any]] | None) -> str:
    if not outcomes:
        return "Testkonto: noch nicht entschieden."
    ordered = [o for o in outcomes if o.get("status") == "ORDERED"]
    if ordered:
        return "Testkonto: virtueller Kauf ausgelöst (Einstieg zur nächsten Kerze, mit Stopp)."
    reasons = [str(r) for o in outcomes for r in (o.get("reasons") or [])]
    return "Testkonto: nicht gekauft – " + ("; ".join(dict.fromkeys(reasons)) if reasons else "von der Risikoprüfung blockiert") + "."


def signal_text(row: dict[str, Any]) -> tuple[str, str]:
    """Titel und Text der Meldung (rein, getestet)."""
    pair = str(row["instrument_id"]).split(":", 1)[-1]
    title = f"Kaufsignal {pair} ({row['timeframe']})"
    entry, stop = Decimal(row["entry"]), Decimal(row["stop"])
    stop_pct = (entry - stop) / entry * 100 if entry else Decimal(0)
    lines = [
        f"{row['name']} · Strategie {_strategy(str(row['strategy_version_id']))}",
        f"Einstieg {_price(entry)} · Stopp {_price(stop)} (−{stop_pct.quantize(Decimal('0.1'))} %)"
        + (f" · Ziel {_price(Decimal(row['target']))}" if row.get("target") is not None else " · Ausstieg über nachgezogenen Stopp"),
        f"Bewertung {row['score']}/100 (Anzahl erfüllter Kriterien, keine Wahrscheinlichkeit)",
    ]
    if row.get("triggers"):
        lines.append("Dafür: " + "; ".join(str(t) for t in row["triggers"]))
    if row.get("counter"):
        lines.append("Dagegen: " + "; ".join(str(c) for c in row["counter"]))
    lines.append(_outcome(row.get("outcomes")))
    lines.append("Simulation mit virtuellem Geld – keine Anlageempfehlung.")
    return title, "\n".join(lines)


def raise_signal_alerts(conn: Conn, now: datetime) -> int:
    # nach einem Tag sind Kaufsignale erledigt (zugestellt, Kerze längst abgelaufen): aus der Liste offener Meldungen
    conn.execute(
        "update alert set status = 'RESOLVED', resolved_at = %s, updated_at = %s, next_escalation_at = null "
        "where kind = %s and status <> 'RESOLVED' and created_at < %s",
        (now, now, KIND, now - timedelta(days=1)),
    )
    rows = conn.execute(_NEW_BUYS, (now - MAX_AGE, QUIET, INVEST_IDS, now - INVEST_REPEAT)).fetchall()
    for row in rows:
        title, body = signal_text(row)
        raise_alert(conn, "SIGNAL", "PAPER", KIND, f"signal:{row['id']}", title, body,
                    {"signal_id": str(row["id"]), "instrument_id": row["instrument_id"], "timeframe": row["timeframe"],
                     "strategy": row["strategy_version_id"]}, now)
    return len(rows)
