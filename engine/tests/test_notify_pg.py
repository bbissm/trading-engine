"""Benachrichtigung gegen echtes Postgres: Entdoppelung, Stufen, Ruhezeiten, Eskalation, Quittungen, Testalarm,
Kanalausfall (T15), Telegram-Rückrufe und Geheimnisschutz. Gefälschte Kanal-APIs, kein echtes Netz."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from notify_fakes import DSN, FAKE_ENV, SECRETS, FakeApis, fresh_db
from psycopg.types.json import Jsonb

from tradingengine.notify.alerts import acknowledge, raise_alert, resolve_alert
from tradingengine.notify.deliver import deliver_pending as _deliver_pending
from tradingengine.notify.health import check_channels, send_test_alert
from tradingengine.ports import Command
from tradingengine.services import notify_commands

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not DSN, reason="TE_TEST_DATABASE_URL nicht gesetzt")]

DAY = datetime(2026, 3, 4, 10, 0, tzinfo=UTC)  # 11:00 Zürich, keine Ruhezeit
NIGHT = datetime(2026, 3, 4, 22, 0, tzinfo=UTC)  # 23:00 Zürich
MIN = timedelta(minutes=1)
Conn = psycopg.Connection[dict[str, Any]]


@pytest.fixture()
def conn() -> Iterator[Conn]:
    c = fresh_db()
    yield c
    c.close()


def deliver_pending(conn: Conn, now: datetime, channels: Any, daily_report: bool = False, **kw: Any) -> dict[str, int]:
    """Ohne Tagesbericht, ausser ein Test verlangt ihn ausdrücklich."""
    return _deliver_pending(conn, now, channels, daily_report=daily_report, **kw)


def rows(conn: Conn, q: str, p: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return conn.execute(q, p).fetchall()  # type: ignore[arg-type]


def alert(conn: Conn, alert_id: int) -> dict[str, Any]:
    return rows(conn, "select * from alert where id = %s", (alert_id,))[0]


def deliveries(conn: Conn, alert_id: int) -> list[tuple[str, str]]:
    return [(r["channel"], r["status"]) for r in rows(conn, "select channel, status from alert_delivery where alert_id = %s order by id", (alert_id,))]


def cmd(type_: str, **params: Any) -> Command:
    return Command(1, type_, None, params, "user:test")


def test_dedup_updates_instead_of_flooding(conn: Conn) -> None:
    ids = {raise_alert(conn, "WARNING", "SYSTEM", "FEED", "feed:x", "Feed x", f"Beobachtung {n}", now=DAY + n * MIN) for n in range(5)}
    assert len(ids) == 1
    a = alert(conn, ids.pop())
    assert (a["occurrences"], a["body"], a["status"]) == (5, "Beobachtung 4", "OPEN")
    assert len(rows(conn, "select 1 from alert")) == 1
    assert resolve_alert(conn, "feed:x", DAY + 10 * MIN)
    new_id = raise_alert(conn, "WARNING", "SYSTEM", "FEED", "feed:x", "Feed x", "wieder", now=DAY + 20 * MIN)
    assert new_id != a["id"] and len(rows(conn, "select 1 from alert")) == 2  # neuer Zustand nach Auflösung = neue Meldung


def test_level_can_only_rise_and_rise_restarts_delivery(conn: Conn) -> None:
    api = FakeApis()
    aid = raise_alert(conn, "WARNING", "PAPER", "X", "k", "Titel", "b", {"account_id": "paper-a"}, DAY)
    deliver_pending(conn, DAY, api.channels())
    acknowledge(conn, aid, "user:test", DAY + MIN)
    raise_alert(conn, "INFO", "PAPER", "X", "k", "Titel", "b2", {"account_id": "paper-a"}, DAY + 2 * MIN)
    a = alert(conn, aid)
    assert (a["level"], a["status"]) == ("WARNING", "ACKNOWLEDGED")  # tiefer: keine Wirkung auf Stufe/Status
    raise_alert(conn, "CRITICAL", "PAPER", "X", "k", "Titel", "b3", {"account_id": "paper-a"}, DAY + 3 * MIN)
    a = alert(conn, aid)
    assert (a["level"], a["status"], a["escalation_level"], a["acknowledged_at"]) == ("CRITICAL", "OPEN", 0, None)
    api.clear()
    deliver_pending(conn, DAY + 3 * MIN, api.channels())
    assert len(api.calls("pushover.net/1/messages")) == 1 and len(api.calls("sendMessage")) == 1


def test_unconfigured_channels_are_skipped_without_http(conn: Conn) -> None:
    api = FakeApis()
    aid = raise_alert(conn, "CRITICAL", "PAPER", "X", "k", "Titel", "b", now=DAY)
    stats = deliver_pending(conn, DAY, api.channels({}))
    assert not api.requests and stats["calls"] == 0
    assert deliveries(conn, aid) == [("IN_APP", "SENT"), ("PUSHOVER", "SKIPPED_DISABLED"), ("TELEGRAM", "SKIPPED_DISABLED"), ("EMAIL", "SKIPPED_DISABLED")]
    errors = [r["error"] for r in rows(conn, "select error from alert_delivery where status = 'SKIPPED_DISABLED'")]
    assert "TELEGRAM_BOT_TOKEN" in errors[1] and FAKE_ENV["TELEGRAM_CHAT_ID"] not in "".join(errors)


def test_critical_escalation_timeline_sent_does_not_stop_only_ack_does(conn: Conn) -> None:
    api = FakeApis()
    aid = raise_alert(conn, "CRITICAL", "PAPER", "LOSS", "loss:a", "Tagesverlust-Grenze erreicht", "Ist 1.6 % ≥ 1.5 %", {"account_id": "paper-a"}, DAY)

    def at(minutes: int) -> dict[str, int]:
        api.clear()
        return deliver_pending(conn, DAY + minutes * MIN, api.channels())

    at(0)  # sofort: Pushover Prio 2 und Telegram
    po = api.calls("pushover.net/1/messages")
    assert len(po) == 1 and len(api.calls("sendMessage")) == 1 and not api.calls("resend")
    form = api.form(po[0])
    assert (form["priority"], form["retry"], form["expire"]) == ("2", "60", "10800")
    assert form["title"].startswith("[PAPER] ")
    text = api.telegram_texts()[0]
    assert text.startswith("[PAPER] Tagesverlust-Grenze erreicht") and "genehmigt aber keinen Trade" in text
    keyboard = json.loads(api.calls("sendMessage")[0].content)["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in keyboard] == [f"a:{aid}", f"p:{aid}"]
    assert rows(conn, "select provider_ref from alert_delivery where channel = 'PUSHOVER'")[0]["provider_ref"] == "rcpt1"

    at(5)
    assert not [r for r in api.requests if "receipts" not in str(r.url)]  # nur Quittungsabfrage, kein Versand
    at(15)  # 15 min ohne Bestätigung: Telegram erneut + E-Mail
    assert len(api.calls("sendMessage")) == 1 and len(api.calls("api.resend.com/emails")) == 1 and not api.calls("messages.json")
    assert api.telegram_texts()[0].startswith("[PAPER] Erinnerung 1, nicht bestätigt: Tagesverlust")
    at(30)
    assert len(api.calls("sendMessage")) == 1 and not api.calls("resend")
    for m in range(45, 180, 15):
        at(m)
    at(180)  # nach 3 h: neuer Zyklus mit Pushover
    assert len(api.calls("pushover.net/1/messages")) == 1 and api.telegram_texts()[0].startswith("[PAPER] Neuer Alarmzyklus")
    assert alert(conn, aid)["status"] == "OPEN"  # alles «SENT», trotzdem offen
    sent_count = len(rows(conn, "select 1 from alert_delivery where alert_id = %s and status = 'SENT'", (aid,)))

    assert acknowledge(conn, aid, "user:test", DAY + 181 * MIN)
    for m in (195, 210, 400):
        at(m)
    assert not api.calls("sendMessage") and not api.calls("messages.json")
    assert len(rows(conn, "select 1 from alert_delivery where alert_id = %s and status = 'SENT' and coalesce(provider_ref, '') not like 'cancel:%%'",
                    (aid,))) == sent_count


def test_pushover_receipt_acknowledges_alert(conn: Conn) -> None:
    api = FakeApis()
    aid = raise_alert(conn, "CRITICAL", "SYSTEM", "X", "k", "Engine", "b", now=DAY)
    deliver_pending(conn, DAY, api.channels())
    api.acknowledged.add("rcpt1")
    deliver_pending(conn, DAY + MIN, api.channels())
    a = alert(conn, aid)
    assert (a["status"], a["acknowledged_by"]) == ("ACKNOWLEDGED", "pushover")
    api.clear()
    deliver_pending(conn, DAY + 20 * MIN, api.channels())
    assert not api.calls("sendMessage")


def test_in_app_ack_cancels_pushover_repetition(conn: Conn) -> None:
    api = FakeApis()
    aid = raise_alert(conn, "CRITICAL", "SYSTEM", "X", "k", "Engine", "b", now=DAY)
    deliver_pending(conn, DAY, api.channels())
    acknowledge(conn, aid, "user:test", DAY + MIN)
    api.clear()
    deliver_pending(conn, DAY + 2 * MIN, api.channels())
    assert len(api.calls("receipts/rcpt1/cancel.json")) == 1
    api.clear()
    deliver_pending(conn, DAY + 3 * MIN, api.channels())
    assert not api.calls("cancel.json")  # nur einmal


def test_warning_reminds_once_after_30_minutes(conn: Conn) -> None:
    api = FakeApis()
    aid = raise_alert(conn, "WARNING", "SYSTEM", "FEED", "feed:x", "Datenstrom BTC 4h: STALE", "seit 10:00", now=DAY)
    for m in (0, 10, 29, 30, 31, 60, 120):
        deliver_pending(conn, DAY + m * MIN, api.channels())
    texts = api.telegram_texts()
    assert len(texts) == 2 and texts[0].startswith("[SYSTEM] Datenstrom") and texts[1].startswith("[SYSTEM] Erinnerung: Datenstrom")
    assert alert(conn, aid)["escalation_level"] == 2


def test_signal_is_sent_once_then_edited(conn: Conn) -> None:
    api = FakeApis()
    raise_alert(conn, "SIGNAL", "PAPER", "SIGNAL", "sig:1", "BUY BTC/USD 4h", "Einstieg 100", now=DAY)
    deliver_pending(conn, DAY, api.channels())
    deliver_pending(conn, DAY + MIN, api.channels())
    assert len(api.calls("sendMessage")) == 1
    raise_alert(conn, "SIGNAL", "PAPER", "SIGNAL", "sig:1", "BUY BTC/USD 4h", "Order gesendet: 12 BTC/USD (simuliert)", now=DAY + 2 * MIN)
    deliver_pending(conn, DAY + 2 * MIN, api.channels())
    deliver_pending(conn, DAY + 3 * MIN, api.channels())
    edits = api.calls("editMessageText")
    assert len(api.calls("sendMessage")) == 1 and len(edits) == 1
    body = json.loads(edits[0].content)
    assert body["message_id"] == 101 and body["text"].startswith("[PAPER] BUY BTC/USD 4h") and "Order gesendet" in body["text"]


def test_quiet_hours_per_level(conn: Conn) -> None:
    api = FakeApis()
    info = raise_alert(conn, "INFO", "RESEARCH", "LAB", "lab:1", "Lauf fertig", "x", now=NIGHT)
    sig = raise_alert(conn, "SIGNAL", "PAPER", "SIGNAL", "sig:1", "BUY", "x", now=NIGHT)
    warn = raise_alert(conn, "WARNING", "SYSTEM", "FEED", "feed:1", "Feed", "x", now=NIGHT)
    crit = raise_alert(conn, "CRITICAL", "PAPER", "LOSS", "loss:1", "Verlust", "x", now=NIGHT)
    deliver_pending(conn, NIGHT, api.channels())
    assert deliveries(conn, info) == [("IN_APP", "SENT")]
    assert deliveries(conn, sig) == [("IN_APP", "SENT"), ("TELEGRAM", "SKIPPED_QUIET_HOURS")]
    assert ("TELEGRAM", "SENT") in deliveries(conn, warn) and ("PUSHOVER", "SENT") in deliveries(conn, crit)  # kritisch übergeht
    sent = {json.loads(r.content)["text"].split("\n")[0]: json.loads(r.content) for r in api.calls("sendMessage")}
    assert sent["[SYSTEM] Feed"]["disable_notification"] is True and sent["[PAPER] Verlust"]["disable_notification"] is False
    assert len(sent) == 2

    # mehrere Läufe in der Nacht: keine neuen Einträge für die zurückgehaltene Meldung
    deliver_pending(conn, NIGHT + 30 * MIN, api.channels())
    assert deliveries(conn, sig) == [("IN_APP", "SENT"), ("TELEGRAM", "SKIPPED_QUIET_HOURS")]
    # 07:00 Zürich: Signal wird zugestellt
    api.clear()
    morning = datetime(2026, 3, 5, 6, 0, tzinfo=UTC)
    deliver_pending(conn, morning, api.channels())
    assert ("TELEGRAM", "SENT") in deliveries(conn, sig)


def test_critical_bypass_can_be_switched_off(conn: Conn) -> None:
    api = FakeApis()
    status, _ = notify_commands.handle(conn, cmd("SETTINGS_SET", key="notify.quiet_hours", value={"start": "22:00", "end": "07:00", "critical_bypass": False}), NIGHT)
    assert status == "DONE"
    crit = raise_alert(conn, "CRITICAL", "PAPER", "LOSS", "loss:1", "Verlust", "x", now=NIGHT)
    deliver_pending(conn, NIGHT, api.channels())
    assert not api.requests
    assert deliveries(conn, crit) == [("IN_APP", "SENT"), ("PUSHOVER", "SKIPPED_QUIET_HOURS"), ("TELEGRAM", "SKIPPED_QUIET_HOURS")]
    deliver_pending(conn, datetime(2026, 3, 5, 6, 0, tzinfo=UTC), api.channels())
    assert len(api.calls("messages.json")) == 1


def test_info_is_bundled_into_daily_report_at_0730(conn: Conn) -> None:
    api = FakeApis()
    evening = datetime(2026, 3, 4, 17, 0, tzinfo=UTC)
    a = raise_alert(conn, "INFO", "RESEARCH", "LAB", "lab:1", "Backtest abgeschlossen", "zu wenig Evidenz", now=evening)
    b = raise_alert(conn, "INFO", "PAPER", "PAPER", "paper:1", "Episode 2 gestartet", "", now=evening)
    deliver_pending(conn, evening, api.channels())
    deliver_pending(conn, datetime(2026, 3, 5, 6, 0, tzinfo=UTC), api.channels(), daily_report=True)  # 07:00 – noch nicht
    assert not api.calls("sendMessage")
    report_time = datetime(2026, 3, 5, 6, 30, tzinfo=UTC)  # 07:30 Zürich
    deliver_pending(conn, report_time, api.channels(), daily_report=True)
    deliver_pending(conn, report_time + MIN, api.channels(), daily_report=True)
    texts = api.telegram_texts()
    assert len(texts) == 1 and len(api.calls("api.resend.com/emails")) == 1
    assert texts[0].startswith("[SYSTEM] Tagesbericht 05.03.2026")
    assert "[FORSCHUNG] Backtest abgeschlossen" in texts[0] and "[PAPER] Episode 2 gestartet" in texts[0]
    assert alert(conn, a)["status"] == alert(conn, b)["status"] == "RESOLVED"
    assert len(rows(conn, "select 1 from alert where kind = 'DAILY_REPORT'")) == 1


def test_test_alarm_flow_and_acknowledgement_via_telegram(conn: Conn) -> None:
    api = FakeApis()
    status, result = notify_commands.handle(conn, cmd("NOTIFY_TEST"), NIGHT)  # auch in der Ruhezeit
    assert status == "DONE"
    aid = result["alert_id"]
    deliver_pending(conn, NIGHT, api.channels())
    assert len(api.calls("sendMessage")) == 1 and len(api.calls("messages.json")) == 1 and len(api.calls("resend.com/emails")) == 1
    assert api.form(api.calls("messages.json")[0])["priority"] == "2"
    cs = {r["channel"]: r for r in rows(conn, "select * from channel_status")}
    assert cs["TELEGRAM"]["last_test_sent_at"] == NIGHT and cs["TELEGRAM"]["last_test_ack_at"] is None
    status, result = notify_commands.handle(conn, cmd("TELEGRAM_CALLBACK", action="ack", alert_id=aid), NIGHT + 5 * MIN)
    assert status == "DONE" and "genehmigt keinen Trade" in result["note"]
    cs = {r["channel"]: r for r in rows(conn, "select * from channel_status")}
    assert all(r["last_test_ack_at"] == NIGHT + 5 * MIN for r in cs.values())
    assert alert(conn, aid)["acknowledged_by"] == "telegram"


def test_channel_outage_policy(conn: Conn) -> None:
    api = FakeApis()
    out = check_channels(conn, DAY, api.channels({}))
    assert out["live_entries_blocked"] and not api.requests
    warning = rows(conn, "select * from alert where dedup_key = 'notify:channel-outage'")[0]
    assert (warning["level"], warning["mode"], warning["status"]) == ("WARNING", "SYSTEM", "OPEN")
    cs = {r["channel"]: r for r in rows(conn, "select * from channel_status")}
    assert not cs["TELEGRAM"]["configured"] and "TELEGRAM_CHAT_ID" in cs["TELEGRAM"]["detail"]

    # Kanäle eingerichtet und erreichbar → frei, Warnung aufgelöst (die Prüfung läuft sofort, weil sich «eingerichtet» geändert hat)
    out = check_channels(conn, DAY + MIN, api.channels())
    assert not out["live_entries_blocked"] and sorted(out["checked"]) == ["EMAIL", "PUSHOVER", "TELEGRAM"]
    assert len(api.calls("getMe")) == 1 and len(api.calls("users/validate")) == 1 and len(api.calls("resend.com/domains")) == 1
    assert rows(conn, "select status from alert where dedup_key = 'notify:channel-outage'")[0]["status"] == "RESOLVED"
    # innerhalb von 15 min keine weitere Prüfung
    api.clear()
    check_channels(conn, DAY + 10 * MIN, api.channels())
    assert not api.requests

    # Pushover und Telegram fallen aus → bei der nächsten fälligen Prüfung erkannt
    api.telegram_ok = api.pushover_ok = False
    out = check_channels(conn, DAY + 16 * MIN, api.channels())
    assert out["live_entries_blocked"]
    api.telegram_ok = api.pushover_ok = True
    assert not check_channels(conn, DAY + 32 * MIN, api.channels())["live_entries_blocked"]

    # Testalarm 24 h unbestätigt → blockiert, Bestätigung gibt frei
    test_id = send_test_alert(conn, DAY + 33 * MIN)
    assert not check_channels(conn, DAY + 34 * MIN, api.channels())["live_entries_blocked"]
    out = check_channels(conn, DAY + 33 * MIN + timedelta(hours=24), api.channels())
    assert out["live_entries_blocked"] and "Testalarm" in out["reasons"][0]
    acknowledge(conn, test_id, "user:test", DAY + timedelta(hours=25))
    assert not check_channels(conn, DAY + timedelta(hours=25, minutes=1), api.channels())["live_entries_blocked"]


def test_critical_falls_back_to_email_when_pushover_fails(conn: Conn) -> None:
    api = FakeApis()
    api.pushover_ok = False
    aid = raise_alert(conn, "CRITICAL", "SYSTEM", "X", "k", "Engine", "b", now=DAY)
    deliver_pending(conn, DAY, api.channels())
    assert deliveries(conn, aid) == [("IN_APP", "SENT"), ("PUSHOVER", "FAILED"), ("TELEGRAM", "SENT"), ("EMAIL", "SENT")]


def test_delivery_is_bounded_per_run(conn: Conn) -> None:
    api = FakeApis()
    for n in range(10):
        raise_alert(conn, "CRITICAL", "SYSTEM", "X", f"k{n}", f"Alarm {n}", "b", now=DAY)
    stats = deliver_pending(conn, DAY, api.channels())
    assert stats["calls"] <= 6 and len(api.requests) <= 6 and stats["deferred"] > 0
    for m in range(1, 6):
        deliver_pending(conn, DAY + m * MIN, api.channels())
    assert len(rows(conn, "select 1 from alert where escalation_level >= 1")) == 10  # im Lauf der nächsten Ticks nachgeholt


def _paper_account(conn: Conn, account_id: str, mode: str = "PAPER") -> None:
    conn.execute("insert into account (id, mode, name, currency) values (%s, %s, %s, 'USD')", (account_id, mode, account_id))


def test_telegram_callback_can_only_ack_or_pause_paper(conn: Conn) -> None:
    _paper_account(conn, "paper-a")
    _paper_account(conn, "live-a", "LIVE")
    paper_alert = raise_alert(conn, "CRITICAL", "PAPER", "LOSS", "loss:a", "Verlust", "x", {"account_id": "paper-a"}, DAY)
    live_alert = raise_alert(conn, "CRITICAL", "LIVE", "LOSS", "loss:l", "Verlust", "x", {"account_id": "live-a"}, DAY)

    status, result = notify_commands.handle(conn, cmd("TELEGRAM_CALLBACK", action="pause", alert_id=paper_alert), DAY)
    assert status == "DONE"
    pause = rows(conn, "select * from command where id = %s", (result["paper_pause_command_id"],))[0]
    assert (pause["type"], pause["target"], pause["status"], pause["issued_by"]) == ("PAPER_PAUSE", "paper-a", "PENDING", "telegram")

    assert notify_commands.handle(conn, cmd("TELEGRAM_CALLBACK", action="pause", alert_id=live_alert), DAY)[0] == "REJECTED"
    assert notify_commands.handle(conn, cmd("TELEGRAM_CALLBACK", action="pause", account_id="live-a"), DAY)[0] == "REJECTED"
    for action in ("start", "approve", "close_all", "resume", None):
        assert notify_commands.handle(conn, cmd("TELEGRAM_CALLBACK", action=action, alert_id=paper_alert, account_id="paper-a"), DAY)[0] == "REJECTED"
    assert len(rows(conn, "select 1 from command")) == 1  # nur die eine Pause
    # Bestätigen genehmigt nichts: keine Order, kein Befehl
    assert notify_commands.handle(conn, cmd("TELEGRAM_CALLBACK", action="ack", alert_id=live_alert), DAY)[0] == "DONE"
    assert len(rows(conn, "select 1 from command")) == 1 and not rows(conn, "select 1 from trade_order")
    assert notify_commands.handle(conn, cmd("ALERT_ACK", alert_id=live_alert), DAY)[0] == "REJECTED"  # schon bestätigt


def test_settings_set_accepts_only_known_keys(conn: Conn) -> None:
    assert notify_commands.handle(conn, cmd("SETTINGS_SET", key="notify.channels", value={"EMAIL": False}), DAY)[0] == "DONE"
    assert notify_commands.handle(conn, cmd("SETTINGS_SET", key="notify.sms_enabled", value=True), DAY)[0] == "DONE"
    assert notify_commands.handle(conn, cmd("SETTINGS_SET", key="risk.daily_loss_limit", value="0.5"), DAY)[0] == "REJECTED"
    assert notify_commands.handle(conn, cmd("SETTINGS_SET", key="notify.channels", value={"TELEGRAM": "ja"}), DAY)[0] == "REJECTED"
    s = {r["key"]: r for r in rows(conn, "select * from setting")}
    assert set(s) == {"notify.channels", "notify.sms_enabled"} and s["notify.channels"]["updated_by"] == "user:test"
    # abgeschaltete E-Mail: kritischer Alarm nach 15 min ohne E-Mail, als abgeschaltet protokolliert
    api = FakeApis()
    aid = raise_alert(conn, "CRITICAL", "SYSTEM", "X", "k", "Engine", "b", now=DAY)
    deliver_pending(conn, DAY, api.channels())
    deliver_pending(conn, DAY + 15 * MIN, api.channels())
    assert not api.calls("resend") and ("EMAIL", "SKIPPED_DISABLED") in deliveries(conn, aid)


def test_secrets_never_appear_in_messages_rows_or_logs(conn: Conn, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    api = FakeApis()
    conn.execute("insert into setting (key, value, updated_by) values ('x', %s, 'test')", (Jsonb({}),))
    raise_alert(conn, "CRITICAL", "PAPER", "LOSS", "loss:a", "Verlust", "b", {"account_id": "paper-a"}, DAY)
    raise_alert(conn, "WARNING", "SYSTEM", "FEED", "feed:a", "Feed", "b", now=DAY)
    send_test_alert(conn, DAY)
    deliver_pending(conn, DAY, api.channels(), max_calls=20)
    api.telegram_ok = api.pushover_ok = False
    api.raise_on = "resend"
    raise_alert(conn, "CRITICAL", "SYSTEM", "X", "k2", "Zweiter", "b", now=DAY + MIN)
    deliver_pending(conn, DAY + 16 * MIN, api.channels(), max_calls=20)
    check_channels(conn, DAY + 16 * MIN, api.channels())
    stored = json.dumps([rows(conn, f"select * from {t}") for t in ("alert", "alert_delivery", "channel_status")], default=str)
    bodies = " ".join(r.content.decode() for r in api.requests if "api.telegram.org" in str(r.url) or "resend" in str(r.url))
    bodies += " ".join(api.form(r).get("message", "") + api.form(r).get("title", "") for r in api.calls("messages.json"))
    assert "FAILED" in stored
    for secret in SECRETS:
        assert secret not in stored and secret not in caplog.text and secret not in bodies
