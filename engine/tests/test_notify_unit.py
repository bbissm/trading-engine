"""Benachrichtigung ohne Datenbank: Ruhezeiten, Wortlaut, Kanäle und Geheimnisschutz."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from notify_fakes import FAKE_ENV, SECRETS, FakeApis

from tradingengine.notify import config, wording
from tradingengine.notify.alerts import prefixed
from tradingengine.notify.channels import Channels, Telegram, redact

QUIET = {"start": "22:00", "end": "07:00", "critical_bypass": True}


def test_quiet_hours_over_midnight_in_zurich_time() -> None:
    # Winterzeit: Zürich = UTC+1
    assert config.quiet_state(datetime(2026, 1, 10, 22, 30, tzinfo=UTC), QUIET) == (True, datetime(2026, 1, 11, 6, 0, tzinfo=UTC))
    assert config.quiet_state(datetime(2026, 1, 10, 5, 0, tzinfo=UTC), QUIET) == (True, datetime(2026, 1, 10, 6, 0, tzinfo=UTC))
    assert config.quiet_state(datetime(2026, 1, 10, 6, 0, tzinfo=UTC), QUIET) == (False, None)  # 07:00 Zürich
    assert config.quiet_state(datetime(2026, 1, 10, 20, 59, tzinfo=UTC), QUIET) == (False, None)  # 21:59 Zürich
    # Sommerzeit: Zürich = UTC+2
    assert config.quiet_state(datetime(2026, 7, 10, 20, 0, tzinfo=UTC), QUIET) == (True, datetime(2026, 7, 11, 5, 0, tzinfo=UTC))
    # Fenster am Tag und «keine Ruhezeit»
    day = {"start": "12:00", "end": "13:00", "critical_bypass": False}
    assert config.quiet_state(datetime(2026, 1, 10, 11, 30, tzinfo=UTC), day) == (True, datetime(2026, 1, 10, 12, 0, tzinfo=UTC))
    assert config.quiet_state(datetime(2026, 1, 10, 11, 30, tzinfo=UTC), {"start": "00:00", "end": "00:00", "critical_bypass": True}) == (False, None)


def test_settings_shapes_are_validated() -> None:
    assert config.valid_quiet(QUIET)
    assert not config.valid_quiet({"start": "25:00", "end": "07:00", "critical_bypass": True})
    assert not config.valid_quiet({"start": "22:00", "end": "07:00"})
    assert not config.valid_quiet({**QUIET, "critical_bypass": "ja"})
    assert config.valid_channels({"TELEGRAM": True, "EMAIL": False})
    assert not config.valid_channels({"SMS": True})
    assert not config.valid_channels({"TELEGRAM": 1})


def test_wording_distinguishes_prepared_sent_and_filled() -> None:
    until = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
    assert wording.order_prepared(D("10"), "AAPL", until) == "Order vorbereitet – 10 AAPL, wartet auf deine Freigabe bis 15:00"
    assert wording.order_sent(D("10"), "AAPL", simulated=False) == "Order gesendet: 10 AAPL"
    assert wording.order_filled(D("10.000"), "BTC/USD", D("100.0200"), simulated=True) == "Ausgeführt: 10 BTC/USD zu 100.02 (simuliert)"
    assert prefixed("RESEARCH", "Lauf fertig") == "[FORSCHUNG] Lauf fertig"
    assert prefixed("PAPER", "[PAPER] schon da") == "[PAPER] schon da"


def test_unconfigured_channels_report_missing_names_only() -> None:
    ch = Channels.from_env({"TELEGRAM_BOT_TOKEN": "x"})
    assert not ch.telegram.configured and ch.telegram.missing() == ["TELEGRAM_CHAT_ID"]
    assert ch.pushover.missing() == ["PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY"]
    assert ch.email.missing() == ["RESEND_API_KEY", "ALERT_EMAIL_TO", "ALERT_EMAIL_FROM"]
    ch.close()


def test_callback_data_is_limited_to_64_bytes() -> None:
    assert Telegram.keyboard([("Bestätigen", "a:123456789")]) == {"inline_keyboard": [[{"text": "Bestätigen", "callback_data": "a:123456789"}]]}
    with pytest.raises(ValueError):
        Telegram.keyboard([("x", "p:" + "a" * 70)])


def test_telegram_payload_and_silent_flag() -> None:
    api = FakeApis()
    ch = api.channels()
    r = ch.telegram.send("[PAPER] Hallo", [("Bestätigen", "a:1")], silent=True)
    assert r.ok and r.ref == "101"
    body = json.loads(api.requests[0].content)
    assert body["chat_id"] == FAKE_ENV["TELEGRAM_CHAT_ID"] and body["disable_notification"] is True
    assert body["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "a:1"


def test_network_errors_never_leak_secrets(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    api = FakeApis()
    api.raise_on = "api."  # jede Anfrage scheitert mit einer Ausnahme, deren Text die URL samt Token enthält
    ch = api.channels()
    results = [ch.telegram.send("x"), ch.telegram.check(), ch.pushover.send("t", "m", 2), ch.pushover.receipt("r1"), ch.email.send("s", "t")]
    assert all(not r.ok and r.error for r in results)
    text = " ".join(r.error or "" for r in results) + caplog.text
    for secret in SECRETS:
        assert secret not in text
    assert "***" in text
    assert redact("token=abcdef1234", ["abcdef1234"]) == "token=***"
