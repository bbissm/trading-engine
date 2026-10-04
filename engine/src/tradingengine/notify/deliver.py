"""Zustellung mit Eskalation (docs/06, 4.2) – zustandslos je Tick; der Fortschritt steht an der Meldung.

| Stufe    | Verhalten |
|----------|-----------|
| INFO     | nur In-App; gebündelt im Tagesbericht um 07:30 Europe/Zurich (Telegram, E-Mail), nie einzeln |
| SIGNAL   | Telegram einmal; spätere Aktualisierung (gleicher Schlüssel) bearbeitet dieselbe Nachricht |
| WARNING  | Telegram; eine Erinnerung nach 30 min, falls noch offen |
| CRITICAL | Pushover Prio 2 (retry 60 s, expire 3 h) und Telegram sofort; Telegram alle 15 min erneut; nach 15 min ohne
|          | Bestätigung E-Mail; nach 3 h neuer Zyklus, solange nicht bestätigt bzw. aufgelöst |

Ruhezeit (Einstellung `notify.quiet_hours`, Standard 22:00–07:00): INFO/SIGNAL zurückgehalten, WARNING stumm
zugestellt, CRITICAL übergeht sie (abschaltbar über `critical_bypass`).

Fortschritt an der Meldung: `escalation_level` = Anzahl ausgeführter Schritte, `next_escalation_at` = nächster
fälliger Schritt (oder Ende einer Zurückhaltung), `last_sent_at` = letzte erfolgreiche Übergabe an einen Kanal.
Jeder Versuch steht in `alert_delivery`. «Vom Kanal angenommen» (SENT) beendet die Eskalation nie – nur
`acknowledge` (In-App, Telegram-Schaltfläche, Pushover-Quittung).

Zeitbudget: höchstens `max_calls` HTTP-Aufrufe und `max_seconds` je Lauf; was nicht drankommt, folgt im nächsten Tick.
"""

from __future__ import annotations

import logging
import time as clock
from collections.abc import Callable
from datetime import datetime, time, timedelta
from typing import Any

import psycopg

from . import config
from .alerts import PREFIX, TEST_KIND, acknowledge, prefixed
from .channels import Channels, SendResult

log = logging.getLogger(__name__)
Conn = psycopg.Connection[dict[str, Any]]

REPORT_AT = time(7, 30)
REPORT_KIND = "DAILY_REPORT"
WARNING_REMINDER = timedelta(minutes=30)
CRITICAL_STEP = timedelta(minutes=15)
CYCLE_STEPS = 12  # 12 × 15 min = 3 h, dann neuer Zyklus (Pushover-expire ist ebenfalls 3 h)
PUSHOVER_RETRY_S = 60
PUSHOVER_EXPIRE_S = 10800
TEST_EXPIRE_S = 3600
MAX_CALLS = 6
MAX_SECONDS = 25.0
LEVEL_LABEL = {"INFO": "Information", "SIGNAL": "Handelssignal", "WARNING": "Warnung", "CRITICAL": "Kritisch"}
ACK_NOTE = "Bestätigen stoppt die Wiederholung, genehmigt aber keinen Trade und keine Freigabe."


def _strip_prefix(title: str) -> str:
    for p in PREFIX.values():
        if title.startswith(p + " "):
            return title[len(p) + 1:]
    return title


class _Run:
    def __init__(self, conn: Conn, now: datetime, channels: Channels, max_calls: int, max_seconds: float) -> None:
        self.conn = conn
        self.now = now
        self.channels = channels
        self.enabled = config.enabled_channels(conn)
        self.calls_left = max_calls
        self.deadline = clock.monotonic() + max_seconds
        self.stats = {"sent": 0, "failed": 0, "skipped": 0, "deferred": 0, "calls": 0}

    def usable(self, channel: str) -> bool:
        return self.enabled.get(channel, False) and self.channels.by_name()[channel].configured

    def can_afford(self, channels: list[str]) -> bool:
        need = sum(1 for c in channels if self.usable(c))
        return need <= self.calls_left and (need == 0 or clock.monotonic() < self.deadline)

    def record(self, alert_id: int, channel: str, status: str, ref: str | None = None, error: str | None = None) -> None:
        self.conn.execute(
            "insert into alert_delivery (alert_id, channel, at, status, provider_ref, error) values (%s, %s, %s, %s, %s, %s)",
            (alert_id, channel, self.now, status, ref, error),
        )
        key = {"SENT": "sent", "FAILED": "failed"}.get(status, "skipped")
        self.stats[key] += 1

    def attempt(self, alert_id: int, channel: str, send: Callable[[], SendResult]) -> SendResult | None:
        """Ein Zustellversuch; nicht eingerichtete oder abgeschaltete Kanäle werden als SKIPPED_DISABLED protokolliert."""
        if not self.usable(channel):
            self.record(alert_id, channel, "SKIPPED_DISABLED", error=self._why_disabled(channel))
            return None
        if self.calls_left <= 0 or clock.monotonic() >= self.deadline:
            return None
        self.calls_left -= 1
        self.stats["calls"] += 1
        try:
            result = send()
        except Exception as exc:  # ein Kanalfehler darf den Tick nicht abbrechen
            result = self.channels.by_name()[channel]._fail(exc)
        self.record(alert_id, channel, "SENT" if result.ok else "FAILED", result.ref, None if result.ok else result.error)
        return result

    def _why_disabled(self, channel: str) -> str:
        if not self.enabled.get(channel, False):
            return "In den Einstellungen abgeschaltet"
        return "Nicht eingerichtet – fehlt: " + ", ".join(self.channels.by_name()[channel].missing())

    def update(self, alert_id: int, **fields: Any) -> None:
        cols = ", ".join(f"{k} = %s" for k in fields)
        self.conn.execute(f"update alert set {cols} where id = %s", (*fields.values(), alert_id))


def _buttons(a: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if a["level"] in ("WARNING", "CRITICAL") or a["kind"] == TEST_KIND:
        out.append(("Bestätigen", f"a:{a['id']}"))
    data = a.get("data") or {}
    if a["mode"] == "PAPER" and a["level"] in ("WARNING", "CRITICAL") and data.get("account_id"):
        out.append(("Einstiege pausieren", f"p:{a['id']}"))
    return out


def _text(a: dict[str, Any], lead: str | None = None) -> str:
    title = _strip_prefix(a["title"])
    head = prefixed(a["mode"], f"{lead}: {title}" if lead else title)
    meta = f"{LEVEL_LABEL.get(a['level'], a['level'])} · Meldung #{a['id']}"
    if a["occurrences"] > 1:
        meta += f" · {a['occurrences']}× festgestellt"
    lines = [head, "", a["body"], "", meta]
    if a["level"] in ("WARNING", "CRITICAL") or a["kind"] == TEST_KIND:
        lines.append(ACK_NOTE)
    return "\n".join(lines)


def _subject(a: dict[str, Any], lead: str | None = None) -> str:
    title = _strip_prefix(a["title"])
    return prefixed(a["mode"], f"{lead}: {title}" if lead else title)


def _hold(run: _Run, a: dict[str, Any], channels: list[str], until: datetime) -> None:
    for ch in channels:
        run.record(a["id"], ch, "SKIPPED_QUIET_HOURS", error="Ruhezeit – zugestellt ab " + until.astimezone(config.ZURICH).strftime("%H:%M"))
    run.update(a["id"], next_escalation_at=until)


def _process(run: _Run, a: dict[str, Any], quiet: bool, quiet_end: datetime | None, bypass: bool) -> None:
    aid, level, step = a["id"], a["level"], a["escalation_level"]
    tg, po, em = run.channels.telegram, run.channels.pushover, run.channels.email
    if step == 0 and a["next_escalation_at"] is None:
        run.record(aid, "IN_APP", "SENT")
        run.update(aid, next_escalation_at=run.now)  # ab jetzt «verarbeitet», auch wenn der Versand aufgeschoben wird

    if a["kind"] == TEST_KIND:  # vom Nutzer ausgelöst: sofort über alle Kanäle, ohne Ruhezeit
        if not run.can_afford(["TELEGRAM", "PUSHOVER", "EMAIL"]):
            run.stats["deferred"] += 1
            return
        text, subject = _text(a), _subject(a)
        sent = [run.attempt(aid, "TELEGRAM", lambda: tg.send(text, _buttons(a))),
                   run.attempt(aid, "PUSHOVER", lambda: po.send(subject, a["body"], 2, PUSHOVER_RETRY_S, TEST_EXPIRE_S)),
                   run.attempt(aid, "EMAIL", lambda: em.send(subject, text))]
        _done(run, a, 1, None, sent)
        return

    if level == "INFO":
        run.update(aid, escalation_level=1, next_escalation_at=None)  # nur Tagesbericht
        return

    if level == "SIGNAL":
        if step == 0:
            if quiet and quiet_end is not None:
                _hold(run, a, ["TELEGRAM"], quiet_end)
                return
            if not run.can_afford(["TELEGRAM"]):
                run.stats["deferred"] += 1
                return
            r = run.attempt(aid, "TELEGRAM", lambda: tg.send(_text(a)))
            _done(run, a, 1, None, [r])
            return
        # Aktualisierung: dieselbe Nachricht bearbeiten statt neu senden
        row = run.conn.execute(
            "select provider_ref from alert_delivery where alert_id = %s and channel = 'TELEGRAM' and status = 'SENT' "
            "and provider_ref is not null order by at, id limit 1", (aid,),
        ).fetchone()
        if row is None or not run.usable("TELEGRAM"):
            run.update(aid, last_sent_at=run.now)
            return
        if not run.can_afford(["TELEGRAM"]):
            run.stats["deferred"] += 1
            return
        ref = str(row["provider_ref"])
        r = run.attempt(aid, "TELEGRAM", lambda: tg.edit(ref, _text(a)))
        run.update(aid, last_sent_at=run.now if r is None or r.ok else a["last_sent_at"])
        return

    if level == "WARNING":
        if step >= 2:
            run.update(aid, next_escalation_at=None)
            return
        if not run.can_afford(["TELEGRAM"]):
            run.stats["deferred"] += 1
            return
        lead = "Erinnerung" if step == 1 else None
        r = run.attempt(aid, "TELEGRAM", lambda: tg.send(_text(a, lead), _buttons(a), silent=quiet))
        _done(run, a, step + 1, run.now + WARNING_REMINDER if step == 0 else None, [r])
        return

    # CRITICAL
    if quiet and not bypass and quiet_end is not None:
        _hold(run, a, ["PUSHOVER", "TELEGRAM"], quiet_end)
        return
    position = step % CYCLE_STEPS  # 0 = Zyklusbeginn
    planned = {0: ["PUSHOVER", "TELEGRAM", "EMAIL"], 1: ["TELEGRAM", "EMAIL"]}.get(position, ["TELEGRAM"])
    if not run.can_afford(planned):
        run.stats["deferred"] += 1
        return
    lead = None if step == 0 else ("Neuer Alarmzyklus" if position == 0 else f"Erinnerung {step}, nicht bestätigt")
    text, subject = _text(a, lead), _subject(a, lead)
    results: list[SendResult | None] = []
    pushed: SendResult | None = None
    if position == 0:
        pushed = run.attempt(aid, "PUSHOVER", lambda: po.send(subject, a["body"], 2, PUSHOVER_RETRY_S, PUSHOVER_EXPIRE_S))
        results.append(pushed)
    results.append(run.attempt(aid, "TELEGRAM", lambda: tg.send(text, _buttons(a))))
    # E-Mail: nach 15 min ohne Bestätigung, und sofort als Ersatz, wenn Pushover nicht annimmt
    if position == 1 or (position == 0 and (pushed is None or not pushed.ok)):
        results.append(run.attempt(aid, "EMAIL", lambda: em.send(subject, text)))
    _done(run, a, step + 1, run.now + CRITICAL_STEP, results)


def _done(run: _Run, a: dict[str, Any], step: int, next_at: datetime | None, results: list[SendResult | None]) -> None:
    sent = any(r is not None and r.ok for r in results)
    run.update(a["id"], escalation_level=step, next_escalation_at=next_at, last_sent_at=run.now if sent else a["last_sent_at"])


def _poll_receipts(run: _Run) -> None:
    """Pushover-Quittung: auf dem Gerät bestätigt → Meldung bestätigt. Bestätigte/aufgelöste → Wiederholung beenden."""
    if not run.usable("PUSHOVER"):
        return
    po = run.channels.pushover
    since = run.now - timedelta(seconds=PUSHOVER_EXPIRE_S)
    rows = run.conn.execute(
        """
        select d.alert_id, d.provider_ref from alert_delivery d join alert a on a.id = d.alert_id
        where d.channel = 'PUSHOVER' and d.status = 'SENT' and d.provider_ref is not null and d.provider_ref not like 'cancel:%%'
          and a.status = 'OPEN' and d.at > %s
        order by d.at desc limit 2
        """,
        (since,),
    ).fetchall()
    for r in rows:
        if run.calls_left <= 0:
            return
        run.calls_left -= 1
        run.stats["calls"] += 1
        res = po.receipt(r["provider_ref"])
        if res.ok and int(res.data.get("acknowledged") or 0) == 1:
            acknowledge(run.conn, int(r["alert_id"]), "pushover", run.now)
    stale = run.conn.execute(
        """
        select d.alert_id, d.provider_ref from alert_delivery d join alert a on a.id = d.alert_id
        where d.channel = 'PUSHOVER' and d.status = 'SENT' and d.provider_ref is not null and d.provider_ref not like 'cancel:%%'
          and a.status <> 'OPEN' and d.at > %s
          and not exists (select 1 from alert_delivery x where x.alert_id = d.alert_id and x.provider_ref = 'cancel:' || d.provider_ref)
        order by d.at desc limit 1
        """,
        (since,),
    ).fetchall()
    for r in stale:
        if run.calls_left <= 0:
            return
        run.calls_left -= 1
        run.stats["calls"] += 1
        res = po.cancel(r["provider_ref"])
        run.record(int(r["alert_id"]), "PUSHOVER", "SENT" if res.ok else "FAILED", f"cancel:{r['provider_ref']}",
                   None if res.ok else res.error)


def _report_text(run: _Run, day: str, infos: list[dict[str, Any]]) -> str:
    lines = [f"[SYSTEM] Tagesbericht {day}", ""]
    accounts = run.conn.execute(
        """
        select a.id, a.name, a.currency, ap.state,
               (select s.equity from equity_snapshot s join episode e on e.id = s.episode_id
                 where e.account_id = a.id and e.ended_at is null order by s.ts desc limit 1) as equity
        from account a left join autopilot ap on ap.account_id = a.id where a.mode = 'PAPER' order by a.id
        """
    ).fetchall()
    if accounts:
        lines.append("Paper-Konten (simuliert, je Konto getrennt):")
        for acc in accounts:
            eq = "—" if acc["equity"] is None else f"{acc['equity']:.2f} {acc['currency']}"
            lines.append(f"[PAPER] {acc['name']}: {acc['state'] or '—'}, Eigenkapital {eq}")
        lines.append("")
    open_counts = run.conn.execute(
        "select level, count(*) as n from alert where status = 'OPEN' and level in ('WARNING','CRITICAL') group by level"
    ).fetchall()
    counts = {r["level"]: int(r["n"]) for r in open_counts}
    lines.append(f"Offen: {counts.get('CRITICAL', 0)} kritisch, {counts.get('WARNING', 0)} Warnung(en).")
    if infos:
        lines += ["", "Informationen seit dem letzten Bericht:"]
        for i in infos[:30]:
            lines.append(f"• {prefixed(i['mode'], _strip_prefix(i['title']))}" + (f" – {i['body'][:160]}" if i["body"] else ""))
        if len(infos) > 30:
            lines.append(f"… und {len(infos) - 30} weitere in der App unter «Meldungen».")
    return "\n".join(lines)


def _daily_report(run: _Run, quiet: bool) -> None:
    local = run.now.astimezone(config.ZURICH)
    if local.time() < REPORT_AT or quiet:
        return
    day = local.date().isoformat()
    key = f"report:{day}"
    report = run.conn.execute("select id from alert where dedup_key = %s order by id limit 1", (key,)).fetchone()
    if report is not None:
        done = run.conn.execute(
            "select count(*) filter (where status in ('SENT','SKIPPED_DISABLED')) as ok, count(*) filter (where status = 'FAILED') as failed "
            "from alert_delivery where alert_id = %s and channel = 'TELEGRAM'", (report["id"],),
        ).fetchone()
        assert done is not None
        if done["ok"] or done["failed"] >= 3:
            return
    if not run.can_afford(["TELEGRAM", "EMAIL"]):
        run.stats["deferred"] += 1
        return
    infos = run.conn.execute(
        """
        select * from alert where level = 'INFO' and kind not in (%s, %s) and status <> 'RESOLVED' and created_at > %s and created_at <= %s
          and not exists (select 1 from alert_delivery d where d.alert_id = alert.id and d.channel = 'TELEGRAM')
        order by created_at
        """,
        (REPORT_KIND, TEST_KIND, run.now - timedelta(days=2), run.now),
    ).fetchall()
    text = _report_text(run, local.strftime("%d.%m.%Y"), infos)
    if report is None:
        row = run.conn.execute(
            "insert into alert (created_at, updated_at, level, mode, kind, dedup_key, title, body, status, resolved_at, escalation_level) "
            "values (%s, %s, 'INFO', 'SYSTEM', %s, %s, %s, %s, 'RESOLVED', %s, 1) returning id",
            (run.now, run.now, REPORT_KIND, key, f"Tagesbericht {local:%d.%m.%Y}", text, run.now),
        ).fetchone()
        assert row is not None
        report_id = int(row["id"])
    else:
        report_id = int(report["id"])
    tg = run.attempt(report_id, "TELEGRAM", lambda: run.channels.telegram.send(text, silent=False))
    run.attempt(report_id, "EMAIL", lambda: run.channels.email.send(f"[SYSTEM] Tagesbericht {local:%d.%m.%Y}", text))
    status = "SKIPPED_DISABLED" if tg is None else ("SENT" if tg.ok else "FAILED")
    if status == "FAILED":
        return  # wird im nächsten Tick erneut versucht (höchstens dreimal)
    for i in infos:  # im Bericht enthalten: als zugestellt vermerken und abschliessen
        run.conn.execute(
            "insert into alert_delivery (alert_id, channel, at, status, provider_ref) values (%s, 'TELEGRAM', %s, %s, %s)",
            (i["id"], run.now, status, key),
        )
        run.conn.execute("update alert set status = 'RESOLVED', resolved_at = %s, updated_at = %s where id = %s and status <> 'RESOLVED'",
                         (run.now, run.now, i["id"]))


def deliver_pending(conn: Conn, now: datetime, channels: Channels | None = None, max_calls: int = MAX_CALLS,
                    max_seconds: float = MAX_SECONDS, daily_report: bool = True) -> dict[str, int]:
    """Ein Zustelllauf: fällige Meldungen nach Stufe (kritisch zuerst), Pushover-Quittungen, Tagesbericht."""
    own = channels is None
    ch = channels or Channels.from_env()
    try:
        return _deliver(conn, now, ch, max_calls, max_seconds, daily_report)
    finally:
        if own:
            ch.close()


def _deliver(conn: Conn, now: datetime, channels: Channels, max_calls: int, max_seconds: float, daily_report: bool) -> dict[str, int]:
    run = _Run(conn, now, channels, max_calls, max_seconds)
    qcfg = config.quiet_hours(conn)
    quiet, quiet_end = config.quiet_state(now, qcfg)
    due = conn.execute(
        """
        select * from alert where status = 'OPEN' and (
            (escalation_level = 0 and (next_escalation_at is null or next_escalation_at <= %s))
            or (next_escalation_at is not null and next_escalation_at <= %s)
            or (level = 'SIGNAL' and escalation_level > 0 and last_sent_at is not null and updated_at > last_sent_at))
        order by array_position(array['INFO','SIGNAL','WARNING','CRITICAL']::text[], level) desc, id
        limit 25
        """,
        (now, now),
    ).fetchall()
    for a in due:
        try:
            _process(run, a, quiet, quiet_end, bool(qcfg["critical_bypass"]))
        except Exception:
            log.exception("Zustellung der Meldung %s fehlgeschlagen", a["id"])
    try:
        _poll_receipts(run)  # nach dem Versand: neue kritische Meldungen haben Vorrang vor Quittungsabfragen
    except Exception:
        log.exception("Pushover-Quittungen nicht abfragbar")
    try:
        if daily_report:
            _daily_report(run, quiet)
    except Exception:
        log.exception("Tagesbericht fehlgeschlagen")
    return run.stats
