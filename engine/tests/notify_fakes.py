"""Hilfen für die Benachrichtigungstests: frische Datenbank aus den Drizzle-Migrationen und gefälschte Kanal-APIs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import psycopg
from psycopg.rows import dict_row

from tradingengine.notify.channels import Channels

DSN = os.environ.get("TE_TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[2] / "web" / "drizzle"

# Gefälschte Zugangsdaten – sie dürfen nie in Meldungstexten, Zustellprotokollen oder Logs auftauchen.
FAKE_ENV = {
    "TELEGRAM_BOT_TOKEN": "987654:FAKE-TG-TOKEN-xyzSECRET",
    "TELEGRAM_CHAT_ID": "424242",
    "PUSHOVER_APP_TOKEN": "aFAKEpushoverAPPtokenSECRET1",
    "PUSHOVER_USER_KEY": "uFAKEpushoverUSERkeySECRET2",
    "RESEND_API_KEY": "re_FAKE_resend_KEY_SECRET3",
    "ALERT_EMAIL_TO": "ich@example.com",
    "ALERT_EMAIL_FROM": "engine@example.com",
}
SECRETS = [FAKE_ENV[k] for k in ("TELEGRAM_BOT_TOKEN", "PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY", "RESEND_API_KEY")]


def fresh_db() -> psycopg.Connection[dict[str, Any]]:
    assert DSN
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("drop schema if exists drizzle cascade; drop schema public cascade; create schema public;")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            for statement in path.read_text(encoding="utf-8").split("--> statement-breakpoint"):
                if statement.strip():
                    conn.execute(statement)  # type: ignore[arg-type]
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


class FakeApis:
    """Telegram, Pushover und Resend als httpx.MockTransport – kein echtes Netz in Tests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.telegram_ok = True
        self.pushover_ok = True
        self.acknowledged: set[str] = set()
        self.next_message = 100
        self.next_receipt = 1
        self.raise_on: str | None = None  # Teil der URL, bei dem eine Netzwerk-Ausnahme simuliert wird

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        url = str(req.url)
        if self.raise_on and self.raise_on in url:
            raise httpx.ConnectError(f"Verbindung fehlgeschlagen: {url}", request=req)
        if "api.telegram.org" in url:
            method = url.rsplit("/", 1)[1]
            if method == "getMe":
                return httpx.Response(200 if self.telegram_ok else 401, json={"ok": self.telegram_ok, "result": {"username": "te_bot"}})
            if not self.telegram_ok:
                return httpx.Response(502, json={"ok": False, "description": "Bad Gateway"})
            body = json.loads(req.content)
            if method == "editMessageText":
                return httpx.Response(200, json={"ok": True, "result": {"message_id": body["message_id"]}})
            self.next_message += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.next_message}})
        if "api.pushover.net" in url:
            if "/receipts/" in url:
                receipt = url.split("/receipts/")[1].split("/")[0].split(".json")[0]
                if url.split("?")[0].endswith("cancel.json"):
                    return httpx.Response(200, json={"status": 1})
                return httpx.Response(200, json={"status": 1, "acknowledged": 1 if receipt in self.acknowledged else 0})
            if url.endswith("users/validate.json"):
                return httpx.Response(200 if self.pushover_ok else 400, json={"status": 1 if self.pushover_ok else 0})
            if not self.pushover_ok:
                return httpx.Response(500, json={"status": 0, "errors": ["Dienst nicht verfügbar"]})
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            if form.get("priority") == "2":
                receipt = f"rcpt{self.next_receipt}"
                self.next_receipt += 1
                return httpx.Response(200, json={"status": 1, "request": "x", "receipt": receipt})
            return httpx.Response(200, json={"status": 1, "request": "x"})
        if "api.resend.com" in url:
            if url.endswith("/domains"):
                return httpx.Response(200, json={"data": []})
            return httpx.Response(200, json={"id": "email-1"})
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def channels(self, env: dict[str, str] | None = None) -> Channels:
        return Channels.from_env(FAKE_ENV if env is None else env, self.client())

    def calls(self, part: str) -> list[httpx.Request]:
        return [r for r in self.requests if part in str(r.url)]

    def telegram_texts(self) -> list[str]:
        return [json.loads(r.content)["text"] for r in self.requests if "api.telegram.org" in str(r.url) and r.content]

    def form(self, req: httpx.Request) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}

    def clear(self) -> None:
        self.requests.clear()
