"""US-Börsenkalender (NYSE/Nasdaq, reguläre Handelszeit) für Tageskerzen von Aktien und ETFs.

Massgeblich ist der Alpaca-Kalender (`GET /v2/calendar`, Zeiten in America/New_York). Ohne Abruf gilt ein
regelbasierter Kalender mit den NYSE-Feiertagen, Halbtagen (Schluss 13:00) und den ausserordentlichen
Schliessungen seit 2016. Der Alpaca-Kalender überschreibt die Regeln für den Zeitraum, den er abdeckt.

Alle Zeitpunkte sind UTC; Sitzungsbeginn und -schluss werden über America/New_York umgerechnet, damit die
Sommerzeitwechsel der USA (zweiter Sonntag im März, erster im November) gelten – nicht jene der Schweiz.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# Ausserordentliche Schliessungen (Staatstrauer) seit Beginn der Alpaca-Historie 2016.
SPECIAL_CLOSURES: frozenset[date] = frozenset({date(2018, 12, 5), date(2025, 1, 9)})


@dataclass(frozen=True, slots=True)
class Session:
    day: date
    open: datetime  # UTC
    close: datetime  # UTC

    @property
    def early_close(self) -> bool:
        return self.close.astimezone(NEW_YORK).time() < REGULAR_CLOSE


def make_session(day: date, open_local: time = REGULAR_OPEN, close_local: time = REGULAR_CLOSE) -> Session:
    o = datetime.combine(day, open_local, NEW_YORK).astimezone(UTC)
    c = datetime.combine(day, close_local, NEW_YORK).astimezone(UTC)
    return Session(day, o, c)


def ny_date(t: datetime) -> date:
    return t.astimezone(NEW_YORK).date()


# --- Regeln ------------------------------------------------------------------------------------------


def _easter(year: int) -> date:
    """Ostersonntag (gregorianisch, anonymer Algorithmus)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = (h + m - 7 * n + 114) % 31 + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """Samstag → Freitag davor, Sonntag → Montag danach."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def nyse_holidays(year: int) -> frozenset[date]:
    days = {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),
    }
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:  # fällt Neujahr auf einen Samstag, bleibt der Freitag davor offen (NYSE-Regel)
        days.add(_observed(new_year))
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth
    days |= {d for d in SPECIAL_CLOSURES if d.year == year}
    return frozenset(d for d in days if d.year == year)


@lru_cache(maxsize=64)
def nyse_early_closes(year: int) -> frozenset[date]:
    """Halbtage mit Schluss 13:00: Tag vor dem 4. Juli, Tag nach Thanksgiving, Heiligabend."""
    holidays = nyse_holidays(year)
    days = {_nth_weekday(year, 11, 3, 4) + timedelta(days=1)}
    july3 = date(year, 7, 3)
    if july3.weekday() < 4 and date(year, 7, 4).weekday() < 5:  # 4. Juli Di–Fr → 3. Juli Mo–Do
        days.add(july3)
    dec24 = date(year, 12, 24)
    if dec24.weekday() < 4:
        days.add(dec24)
    return frozenset(d for d in days if d not in holidays)


def rule_sessions(start: date, end: date) -> list[Session]:
    out: list[Session] = []
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in nyse_holidays(d.year):
            out.append(make_session(d, close_local=EARLY_CLOSE if d in nyse_early_closes(d.year) else REGULAR_CLOSE))
        d += timedelta(days=1)
    return out


# --- Kalender ----------------------------------------------------------------------------------------


class UsCalendar:
    """Sitzungen über einen abgedeckten Datumsbereich [first, last]."""

    def __init__(self, sessions: Iterable[Session], first: date, last: date) -> None:
        self.first, self.last = first, last
        self._by_day = {s.day: s for s in sessions if first <= s.day <= last}
        self._sessions = sorted(self._by_day.values(), key=lambda s: s.day)
        self._closes = [s.close for s in self._sessions]

    @classmethod
    def from_rules(cls, start: date, end: date) -> UsCalendar:
        return cls(rule_sessions(start, end), start, end)

    @classmethod
    def from_alpaca(cls, rows: list[dict[str, Any]], start: date, end: date) -> UsCalendar:
        """Zeilen von `GET /v2/calendar`: {"date": "YYYY-MM-DD", "open": "HH:MM", "close": "HH:MM", ...}."""
        sessions = [
            make_session(date.fromisoformat(r["date"]), time.fromisoformat(r["open"]), time.fromisoformat(r["close"]))
            for r in rows
        ]
        return cls(sessions, start, end)

    def overlay(self, other: UsCalendar) -> UsCalendar:
        """`other` gilt in seinem Bereich, ausserhalb bleiben die eigenen Sitzungen."""
        kept = [s for s in self._sessions if not other.first <= s.day <= other.last]
        return UsCalendar([*kept, *other._sessions], min(self.first, other.first), max(self.last, other.last))

    def covers(self, day: date) -> bool:
        return self.first <= day <= self.last

    def session(self, day: date) -> Session | None:
        return self._by_day.get(day)

    def is_session(self, day: date) -> bool:
        return day in self._by_day

    def sessions_between(self, start: date, end: date) -> list[Session]:
        """Sitzungen mit start ≤ Tag ≤ end."""
        days = [s.day for s in self._sessions]
        return self._sessions[bisect_left(days, start) : bisect_right(days, end)]

    def last_closed(self, t: datetime) -> Session | None:
        """Jüngste Sitzung, deren Schluss ≤ t liegt."""
        idx = bisect_right(self._closes, t) - 1
        return self._sessions[idx] if idx >= 0 else None

    def session_closing_at(self, close: datetime) -> Session | None:
        idx = bisect_left(self._closes, close)
        return self._sessions[idx] if idx < len(self._sessions) and self._closes[idx] == close else None

    def next_session(self, after: Session) -> Session | None:
        idx = bisect_right(self._closes, after.close)
        return self._sessions[idx] if idx < len(self._sessions) else None

    def in_session(self, t: datetime) -> bool:
        s = self.session(ny_date(t))
        return s is not None and s.open <= t < s.close


RULES_START = date(2015, 12, 1)


def default_calendar(now: datetime) -> UsCalendar:
    """Regelkalender von vor der Alpaca-Historie bis gut ein Jahr nach `now`."""
    return _rules_until(ny_date(now).year + 1)


@lru_cache(maxsize=4)
def _rules_until(year: int) -> UsCalendar:
    return UsCalendar.from_rules(RULES_START, date(year + 1, 1, 31))
