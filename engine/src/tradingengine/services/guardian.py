"""Guardian: laufende Überwachung der Verlustgrenzen und der Datenströme (docs/06, 2.2/2.3).

Je Paper-Konto in ACTIVE/ENTRIES_PAUSED (bei jedem Tick):
- Eigenkapital aus dem letzten `equity_snapshot`, Tages-/Wochenbeginn (00:00 Europe/Zurich, Montag) und
  Höchststand der Episode; Grenzen aus der Episoden-Policy über `core.risk.loss_limits`.
- Bewertung in CHF (Tageskurse aus `fx_rate`): eine reine USD/CHF-Bewegung erscheint als FX-Effekt und zählt zum
  Limit. Fehlt ein Kurs, wird in Kontowährung bewertet und das in der Meldung vermerkt.
- Vorwarnung bei 70 % einer Grenze (WARNING, je Konto + Grenze + Tag/Woche/Episode); löst sich selbst auf.
- Grenze erreicht: ACTIVE → ENTRIES_PAUSED (offene Einstiegsorders storniert, Reservierungen frei; Positionen und
  Schutz-Stops bleiben, Ausstiege laufen weiter) und CRITICAL mit Ist/Soll. Diese Meldung bleibt bis zur Bestätigung.
  Der Autopilot wird nicht automatisch wieder aktiviert (Fortsetzen ist eine Bedienhandlung).
- Drawdown ≥ Notfallstufe: CRITICAL mit Hinweis auf die Notfallpolicy «halten mit Schutz» (Paper schliesst nichts
  automatisch).

Zusätzlich: Autopilot im Zustand ERROR → CRITICAL; Datenstrom länger als 10 min nicht OK → WARNING je Feed.
Beides löst sich auf, sobald der Zustand verschwindet. Einzahlungen gibt es im Paper-Konto nicht (Episoden).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ..adapters.pg_paper import PaperRepo, policy_from_json
from ..core.candles import timeframe_delta
from ..core.paper import pause_entries
from ..core.risk import RiskContext, RiskPolicy, loss_limits
from ..notify.alerts import raise_alert, resolve_alert, resolve_matching
from .fx import fx_rate_for
from .paper import ZURICH, _day_and_week_start

log = logging.getLogger(__name__)
Conn = psycopg.Connection[dict[str, Any]]

ACTOR = "engine:guardian"
REPORTING = "CHF"
WARN_SHARE = Decimal("0.7")
FEED_GRACE = timedelta(minutes=10)
WATCHED = ("ACTIVE", "ENTRIES_PAUSED")
DISCLAIMER = ("Auslöse- und Kontrollregel, kein garantierter Maximalverlust. Kurslücken, Handelsunterbrüche und fehlende "
              "Liquidität können die Ausführung verschlechtern oder verhindern.")


def _local_date(t: datetime) -> date:
    return t.astimezone(ZURICH).date()


class _Valuer:
    """Rechnet Beträge der Kontowährung zum Tageskurs in CHF um (letztes Fixing am oder vor dem Datum)."""

    def __init__(self, conn: Conn, currency: str) -> None:
        self.conn = conn
        self.currency = currency
        self.cache: dict[date, tuple[Decimal, date] | None] = {}
        self.missing = False

    def rate(self, day: date) -> tuple[Decimal, date] | None:
        if day not in self.cache:
            self.cache[day] = fx_rate_for(self.conn, self.currency, REPORTING, day)
        return self.cache[day]

    def convert(self, amount: Decimal, day: date) -> Decimal:
        r = self.rate(day)
        if r is None:
            self.missing = True
            return amount
        return amount * r[0]


def _peak(conn: Conn, episode_id: int, currency: str, start_cash: Decimal, started: datetime, v: _Valuer) -> Decimal | None:
    """Bereinigter Höchststand der Episode in Berichtswährung (Maximum der zum jeweiligen Tageskurs bewerteten Stände).

    None, wenn für einen Stand kein Kurs bekannt ist."""
    if currency == REPORTING:
        row = conn.execute("select max(equity) as m from equity_snapshot where episode_id = %s", (episode_id,)).fetchone()
        return max(start_cash, row["m"]) if row and row["m"] is not None else start_cash
    start_rate = v.rate(_local_date(started))
    if start_rate is None:
        return None
    start = start_cash * start_rate[0]
    row = conn.execute(
        """
        select max(s.equity * r.rate) as m, bool_or(r.rate is null) as gaps from equity_snapshot s
        left join lateral (select rate from fx_rate where base = %s and quote = %s
                             and date <= to_char((s.ts at time zone 'Europe/Zurich')::date, 'YYYY-MM-DD')
                           order by date desc limit 1) r on true
        where s.episode_id = %s
        """,
        (currency, REPORTING, episode_id),
    ).fetchone()
    if row is None or row["m"] is None:
        return start
    if row["gaps"]:
        return None
    return max(start, Decimal(row["m"]))


def _fmt(x: Decimal) -> str:
    return f"{x:,.2f}".replace(",", "’")


def _pause(conn: Conn, repo: PaperRepo, account_id: str, reason: str, now: datetime) -> int:
    """ACTIVE → ENTRIES_PAUSED über dieselbe Logik wie der Bedienbefehl «Einstiege pausieren»."""
    state = repo.load_state(account_id)
    cancelled = pause_entries(state, now)
    repo.save_state(state, now)
    repo.set_autopilot(account_id, "ENTRIES_PAUSED", reason, now)
    conn.execute(
        "insert into audit_event (ts, actor, kind, object, data) values (%s, %s, %s, %s, %s)",
        (now, ACTOR, "guardian.entries_paused", f"account:{account_id}", Jsonb({"reason": reason, "cancelled_entry_orders": cancelled})),
    )
    return cancelled


def _check_account(conn: Conn, repo: PaperRepo, row: dict[str, Any], now: datetime) -> dict[str, Any]:
    account_id, episode_id, name, currency = row["id"], int(row["episode_id"]), row.get("name") or row["id"], row["currency"]
    policy: RiskPolicy = policy_from_json(row["policy"])
    start_cash: Decimal = row["start_cash"]
    ep = conn.execute("select started_at from episode where id = %s", (episode_id,)).fetchone()
    started: datetime = ep["started_at"] if ep else now
    latest = conn.execute("select equity, ts from equity_snapshot where episode_id = %s and ts <= %s order by ts desc limit 1",
                          (episode_id, now)).fetchone()
    equity_native: Decimal = latest["equity"] if latest else start_cash

    day_start, week_start = _day_and_week_start(now)
    day_native = repo.equity_at_or_before(episode_id, day_start) or start_cash
    week_native = repo.equity_at_or_before(episode_id, week_start) or start_cash
    today = _local_date(now)
    day_fx_date = _local_date(day_start) - timedelta(days=1)  # Kurs, der zum Tageswechsel galt
    week_fx_date = _local_date(week_start) - timedelta(days=1)

    v = _Valuer(conn, currency)
    eq = v.convert(equity_native, today)
    day_eq = v.convert(day_native, day_fx_date)
    week_eq = v.convert(week_native, week_fx_date)
    peak_chf = _peak(conn, episode_id, currency, start_cash, started, v)
    if peak_chf is None:
        v.missing = True
    unit = currency if v.missing else REPORTING
    if v.missing or peak_chf is None:  # ohne Kurs alles in Kontowährung, damit nichts gemischt wird
        eq, day_eq, week_eq = equity_native, day_native, week_native
        peak = max(start_cash, repo.peak_equity(episode_id) or start_cash)
    else:
        peak = peak_chf

    ctx = RiskContext(equity=eq, day_start_equity=day_eq, week_start_equity=week_eq, peak_equity=peak, entries_today=0,
                      consecutive_losses=0, entries_allowed=True, data_fresh=True)
    breaches = loss_limits(ctx, policy)

    now_rate, day_rate = v.rate(today), v.rate(day_fx_date)
    fx_note = f"Bewertung in {currency}: kein USD/CHF-Kurs verfügbar." if v.missing else (
        "" if currency == REPORTING or now_rate is None else f"Kurs {currency}/CHF {now_rate[0]:.4f} vom {now_rate[1]:%d.%m.%Y} (EZB)."
    )

    def ratio(start: Decimal) -> Decimal:
        return (start - eq) / start if start > 0 else Decimal(0)

    limits = [
        ("daily", "Tagesverlust", ratio(day_eq), policy.daily_loss_limit, today.isoformat(), day_eq, "Tagesbeginn"),
        ("weekly", "Wochenverlust", ratio(week_eq), policy.weekly_loss_limit, _local_date(week_start).isoformat(), week_eq, "Wochenbeginn"),
        ("drawdown", "Drawdown", ratio(peak), policy.drawdown_pause, f"ep{row['number']}", peak, "Höchststand"),
    ]
    breached = [lim for lim in limits if any(b.startswith(lim[1] + " ") for b in breaches)]
    result: dict[str, Any] = {"equity": str(eq), "unit": unit, "breached": [b[0] for b in breached], "warned": []}

    cancelled: int | None = None
    if breached and row["state"] == "ACTIVE":
        first = breached[0]
        reason = f"Guardian: {first[1]} {first[2]:.2%} ≥ Limit {first[3]:.2%} – Einstiege pausiert"
        cancelled = _pause(conn, repo, account_id, reason, now)
        result["paused"] = True
        result["cancelled_entry_orders"] = cancelled

    for key, label, value, limit, period, start, start_label in limits:
        warn_prefix = f"guardian:{account_id}:{key}:warn:"
        keep: set[str] = set()
        data = {"account_id": account_id, "limit": key, "ratio": f"{value:.6f}", "limit_value": str(limit), "equity": str(eq),
                "start": str(start), "unit": unit}
        if (key, label, value, limit, period, start, start_label) in breached:
            action = (f"Einstiege pausiert, {cancelled} offene Einstiegsorder(s) storniert." if cancelled is not None
                      else "Einstiege sind pausiert bzw. werden durch die Risikoprüfung blockiert.")
            trade_part = ""
            if currency != REPORTING and not v.missing and key == "daily" and now_rate and day_rate:
                trade = equity_native - day_native
                fx_effect = day_native * (now_rate[0] - day_rate[0])
                trade_part = f" Davon Handelsergebnis {trade:+.2f} {currency} (simuliert), FX-Effekt {fx_effect:+.2f} CHF."
            body = (f"Konto «{name}»: {label} {value:.2%} ({unit} {_fmt(start - eq)}) ≥ Limit {limit:.2%}. "
                    f"Eigenkapital {unit} {_fmt(eq)}, {start_label} {unit} {_fmt(start)}.{trade_part} {fx_note} {action} "
                    f"Bestehende Positionen bleiben mit Schutz-Stop; Ausstiege laufen weiter. {DISCLAIMER}").replace("  ", " ")
            raise_alert(conn, "CRITICAL", "PAPER", "LOSS_LIMIT", f"guardian:{account_id}:{key}:breach:{period}",
                        f"{label}-Grenze erreicht – Einstiege pausiert", body, data, now)
        elif limit > 0 and value >= limit * WARN_SHARE:
            dedup = warn_prefix + period
            keep.add(dedup)
            body = (f"Konto «{name}»: {label} {value:.2%} – {value / limit:.0%} der Grenze von {limit:.2%}. "
                    f"Eigenkapital {unit} {_fmt(eq)}, {start_label} {unit} {_fmt(start)}. {fx_note} Noch keine Handlung; "
                    f"bei Erreichen werden Einstiege pausiert.").replace("  ", " ")
            raise_alert(conn, "WARNING", "PAPER", "LOSS_LIMIT_WARN", dedup, f"{label} bei {value / limit:.0%} der Grenze", body, data, now)
            result["warned"].append(key)
        resolve_matching(conn, warn_prefix + "%", keep, now)

    dd = ratio(peak)
    if dd >= policy.drawdown_emergency:
        raise_alert(conn, "CRITICAL", "PAPER", "DRAWDOWN_EMERGENCY", f"guardian:{account_id}:dd_emergency:ep{row['number']}",
                    "Drawdown-Notfallstufe erreicht",
                    f"Konto «{name}»: Drawdown {dd:.2%} ≥ Notfallstufe {policy.drawdown_emergency:.2%}. Notfallpolicy «halten mit Schutz»: "
                    f"Positionen bleiben mit Schutz-Stop bestehen, es wird nichts automatisch geschlossen (Paper). Einstiege bleiben pausiert. "
                    f"{DISCLAIMER}",
                    {"account_id": account_id, "limit": "drawdown_emergency", "ratio": f"{dd:.6f}", "unit": unit}, now)
        result["emergency"] = True
    return result


def _feed_since(r: dict[str, Any]) -> datetime | None:
    """Seit wann der Feed (frühestens) nicht OK ist: Abrufe gibt es erst, wenn die nächste Kerze fällig ist."""
    candidates: list[datetime] = []
    if r["last_ok_at"] is not None:
        candidates.append(r["last_ok_at"])
    if r["last_candle_close"] is not None:
        try:
            candidates.append(r["last_candle_close"] + timeframe_delta(r["timeframe"]))
        except (KeyError, ValueError):
            pass
    return max(candidates) if candidates else None


def _check_feeds(conn: Conn, now: datetime) -> list[str]:
    stale: list[str] = []
    for r in conn.execute("select * from feed_status").fetchall():
        key = f"feed:{r['feed']}:{r['instrument_id']}|{r['timeframe']}"
        if r["status"] == "OK":
            resolve_alert(conn, key, now)
            continue
        since = _feed_since(r)
        if since is not None and now - since <= FEED_GRACE:
            continue
        stale.append(key)
        since_text = "unbekannt" if since is None else since.astimezone(ZURICH).strftime("%d.%m.%Y %H:%M")
        raise_alert(conn, "WARNING", "SYSTEM", "FEED_STALE", key, f"Datenstrom {r['instrument_id']} {r['timeframe']}: {r['status']}",
                    f"Status {r['status']} seit spätestens {since_text}. {r['detail'] or ''} Neue Einstiege auf diesem Instrument werden "
                    f"blockiert, bis die Daten wieder frisch sind.".replace("  ", " "),
                    {"instrument_id": r["instrument_id"], "timeframe": r["timeframe"], "status": r["status"]}, now)
    return stale


def run_guardian(conn: Conn, now: datetime) -> dict[str, Any]:
    """Ein Überwachungslauf über alle Paper-Konten und Datenströme. Rückgabe: Zusammenfassung je Konto."""
    repo = PaperRepo(conn)
    summary: dict[str, Any] = {}
    for row in repo.accounts():
        account_id = row["id"]
        error_key = f"guardian:{account_id}:error"
        if row["state"] == "ERROR":
            reason = conn.execute("select reason from autopilot where account_id = %s", (account_id,)).fetchone()
            raise_alert(conn, "CRITICAL", "PAPER", "AUTOPILOT_ERROR", error_key, "Autopilot im Zustand Fehler",
                        f"Konto «{row.get('name') or account_id}»: {(reason or {}).get('reason') or 'ohne Angabe'}. Einstiege sind blockiert; "
                        "bestehende Positionen werden weiter betreut.", {"account": account_id}, now)
        else:
            resolve_alert(conn, error_key, now)
        if row["state"] not in WATCHED:
            continue
        try:
            summary[account_id] = _check_account(conn, repo, row, now)
        except Exception:  # ein Konto darf die Überwachung der anderen nicht verhindern
            log.exception("Guardian: Konto %s nicht auswertbar", account_id)
            summary[account_id] = {"error": True}
    summary["_feeds_stale"] = _check_feeds(conn, now)
    return summary
