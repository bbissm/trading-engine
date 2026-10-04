"""Harte Sperren des Live-Orderwegs. Jede risikosteigernde Live-Order verlangt, dass **alle** gelten:

1. `LIVE_TRADING_ENABLED == "true"` (setzt nur der Nutzer; ohne Variable läuft nichts Live),
2. `KRAKEN_API_KEY`/`KRAKEN_API_SECRET` vorhanden (nur im Live-Prozess gelesen),
3. Schlüsselrechte geprüft (kein Auszahlungsrecht) und in `account.permissions` gespeichert, passend zum Schlüssel,
4. aktives Mandat mit `activated_by` und `step_up_at`, Budget, Instrumenten; Stufe 3 automatisch, Stufe 2 nur
   mit Freigabe (die verfällt), Stufe 1 nie,
5. Strategieversion mit Entscheid `APPROVED_LIVE` und G1–G3 bestanden,
6. letzter Abgleich OK und ≤ 5 min alt, keine Order UNKNOWN, Daten frisch, Testalarm ≤ 7 Tage bestätigt,
   Risikoprüfung bestanden (`core.risk.check_entry`, Mandatsbudget als Kontogrenze).

Risikosenkende Handlungen (Schutz, Exits) hängen nur an 1 und 2 – sie laufen auch, wenn Einstiege gesperrt sind.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

ENV_FLAG = "LIVE_TRADING_ENABLED"
ENV_KEY = "KRAKEN_API_KEY"
ENV_SECRET = "KRAKEN_API_SECRET"

RECON_MAX_AGE = timedelta(minutes=5)
TEST_ALARM_MAX_AGE = timedelta(days=7)
STEP_UP_MAX_AGE = timedelta(minutes=5)
FX_MAX_AGE_DAYS = 5


def live_enabled(env: Mapping[str, str] | None = None) -> bool:
    return (env if env is not None else os.environ).get(ENV_FLAG) == "true"


def keys_present(env: Mapping[str, str] | None = None) -> bool:
    e = env if env is not None else os.environ
    return bool(e.get(ENV_KEY)) and bool(e.get(ENV_SECRET))


def key_fingerprint(api_key: str) -> str:
    """Kennung des öffentlichen Schlüssels (SHA-256, gekürzt) – erkennt Schlüsselwechsel, verrät nichts."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Mandate:
    id: int
    account_id: str
    autonomy_level: int
    strategy_version_ids: tuple[str, ...]
    instrument_ids: tuple[str, ...]
    budget: Decimal
    policy: dict[str, Any]
    status: str
    activated_at: datetime | None
    activated_by: str | None
    step_up_at: datetime | None
    valid_until: datetime | None

    @property
    def emergency_policy(self) -> str:
        return "CLOSE" if str(self.policy.get("emergency", "HOLD_PROTECTED")).upper() == "CLOSE" else "HOLD_PROTECTED"

    @property
    def protection(self) -> str:
        return str(self.policy.get("protection", "STOP_AT_EXCHANGE"))


@dataclass(frozen=True, slots=True)
class AccountLocks:
    """Kontoweite Sperren (für alle Signale gleich), ausgewertet einmal je Tick."""

    enabled: bool
    keys: bool
    permissions: dict[str, Any] | None
    fingerprint: str | None
    mandate: Mandate | None
    autopilot_state: str
    recovery_tick: bool
    last_recon_status: str | None
    last_recon_at: datetime | None
    unknown_orders: int
    test_ack_at: datetime | None
    fx_ok: bool
    supported_protection: frozenset[str] = field(default_factory=frozenset)


def account_lock_reasons(locks: AccountLocks, now: datetime) -> list[str]:
    reasons: list[str] = []
    if not locks.enabled:
        reasons.append("Sperre 1: LIVE_TRADING_ENABLED ist nicht «true»")
    if not locks.keys:
        reasons.append("Sperre 2: Kraken-Schlüssel fehlen im Live-Prozess")
    p = locks.permissions or {}
    if not p.get("checked_at"):
        reasons.append("Sperre 3: Schlüsselrechte nicht geprüft")
    elif p.get("withdraw") is not False:
        reasons.append("Sperre 3: Auszahlungsrecht nicht sicher ausgeschlossen")
    elif p.get("trade") is not True:
        reasons.append("Sperre 3: Handelsrecht nicht bestätigt")
    elif locks.fingerprint is not None and p.get("key_fingerprint") != locks.fingerprint:
        reasons.append("Sperre 3: Schlüssel wurde gewechselt – Rechte neu prüfen (Konto neu registrieren)")
    m = locks.mandate
    if m is None or m.status != "ACTIVE":
        reasons.append("Sperre 4: kein aktives Mandat")
    else:
        if not m.activated_by or m.step_up_at is None or m.activated_at is None:
            reasons.append("Sperre 4: Mandat ohne Aktivierung mit Step-up")
        if m.budget <= 0:
            reasons.append("Sperre 4: Mandat ohne Budget")
        if not m.instrument_ids or not m.strategy_version_ids:
            reasons.append("Sperre 4: Mandat ohne Instrumente oder Strategieversionen")
        if m.valid_until is not None and m.valid_until <= now:
            reasons.append("Sperre 4: Mandat abgelaufen")
        if m.autonomy_level < 2:
            reasons.append("Sperre 4: Autonomiestufe 1 – nur informieren, keine Orders")
        if m.protection not in locks.supported_protection:
            reasons.append(f"Schutzpolicy {m.protection} wird von Kraken nicht unterstützt")
    if locks.autopilot_state != "ACTIVE":
        reasons.append(f"Autopilot im Zustand {locks.autopilot_state}: keine Einstiege")
    if locks.recovery_tick:
        reasons.append("Wiederherstellung: erst Abgleich und Schutz, Einstiege ab dem nächsten Durchlauf")
    if locks.last_recon_status != "OK":
        reasons.append(f"Sperre 6: letzter Abgleich nicht OK ({locks.last_recon_status or 'keiner'})")
    elif locks.last_recon_at is None or now - locks.last_recon_at > RECON_MAX_AGE:
        reasons.append("Sperre 6: letzter Abgleich älter als 5 min")
    if locks.unknown_orders:
        reasons.append(f"Sperre 6: {locks.unknown_orders} Order(s) mit unbekanntem Status")
    if locks.test_ack_at is None or now - locks.test_ack_at > TEST_ALARM_MAX_AGE:
        reasons.append("Sperre 6: kein bestätigter Testalarm in den letzten 7 Tagen")
    if not locks.fx_ok:
        reasons.append("Sperre 6: FX-Kurs USD/CHF fehlt oder ist veraltet")
    return reasons


def strategy_lock_reasons(strategy_version_id: str, instrument_id: str, mandate: Mandate | None, approved_live: bool, gates: dict[str, str]) -> list[str]:
    reasons: list[str] = []
    if mandate is not None:
        if strategy_version_id not in mandate.strategy_version_ids:
            reasons.append(f"Strategieversion {strategy_version_id} nicht im Mandat")
        if instrument_id not in mandate.instrument_ids:
            reasons.append(f"Instrument {instrument_id} nicht im Mandat")
    if not approved_live:
        reasons.append("Sperre 5: Strategieversion ohne Freigabe APPROVED_LIVE")
    missing = [g for g in ("G1", "G2", "G3") if gates.get(g) != "PASSED"]
    if missing:
        reasons.append(f"Sperre 5: Gates nicht bestanden: {', '.join(missing)}")
    return reasons
