"""Kanal-Sender: Telegram (Bot API), Pushover, E-Mail (Resend).

Jeder Sender liest seine Umgebungsvariablen; fehlt eine, ist der Kanal «nicht eingerichtet» (`configured = False`)
und es wird nichts gesendet. Geheimnisse (Tokens, Schlüssel) stehen nie in Meldungstexten oder Logs: Fehlertexte
werden vor dem Speichern/Loggen bereinigt (`redact`), und die URL-Protokollierung von httpx ist abgeschaltet,
weil die Telegram-URL den Bot-Token enthält.

Kurze Timeouts: die Zustellung läuft im Minuten-Tick der Engine mit.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

# httpx protokolliert jede Anfrage mit vollständiger URL (bei Telegram inklusive Bot-Token) – nie ins Log.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(6.0, connect=3.0)
TELEGRAM_API = "https://api.telegram.org"
PUSHOVER_API = "https://api.pushover.net/1"
RESEND_API = "https://api.resend.com"

TELEGRAM_ENV = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
PUSHOVER_ENV = ("PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY")
EMAIL_ENV = ("RESEND_API_KEY", "ALERT_EMAIL_TO", "ALERT_EMAIL_FROM")
CHANNEL_ENV: dict[str, tuple[str, ...]] = {"TELEGRAM": TELEGRAM_ENV, "PUSHOVER": PUSHOVER_ENV, "EMAIL": EMAIL_ENV}
SECRET_ENV = ("TELEGRAM_BOT_TOKEN", "PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY", "RESEND_API_KEY", "TELEGRAM_WEBHOOK_SECRET", "CRON_SECRET")

TELEGRAM_MAX = 4000
PUSHOVER_MAX = 1000


@dataclass(slots=True)
class SendResult:
    ok: bool
    ref: str | None = None  # Nachrichten-ID (Telegram), Quittung (Pushover), E-Mail-ID (Resend)
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


def redact(text: str, secrets: list[str]) -> str:
    """Entfernt bekannte Geheimnisse aus einem Text (z. B. Fehlermeldungen mit URL)."""
    out = text
    for s in sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True):
        out = out.replace(s, "***")
    return out


class _Sender:
    name = ""
    env_names: tuple[str, ...] = ()

    def __init__(self, env: Mapping[str, str], client: httpx.Client) -> None:
        self.env = {k: (env.get(k) or "").strip() for k in self.env_names}
        self.client = client
        self.secrets = [(env.get(k) or "").strip() for k in SECRET_ENV]

    @property
    def configured(self) -> bool:
        return all(self.env.values())

    def missing(self) -> list[str]:
        """Namen fehlender Umgebungsvariablen (nie deren Werte)."""
        return [k for k, v in self.env.items() if not v]

    def check(self) -> SendResult:
        """Technische Prüfung (Erreichbarkeit, gültige Zugangsdaten), ohne etwas zuzustellen."""
        raise NotImplementedError

    def _fail(self, exc: Exception | str) -> SendResult:
        text = redact(str(exc) if isinstance(exc, str) else f"{type(exc).__name__}: {exc}", self.secrets)[:300]
        log.warning("%s: %s", self.name, text)
        return SendResult(False, error=text)

    def _json(self, resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}


class Telegram(_Sender):
    name = "TELEGRAM"
    env_names = TELEGRAM_ENV

    def _call(self, method: str, payload: dict[str, Any]) -> SendResult:
        try:
            resp = self.client.post(f"{TELEGRAM_API}/bot{self.env['TELEGRAM_BOT_TOKEN']}/{method}", json=payload, timeout=TIMEOUT)
            body = self._json(resp)
        except httpx.HTTPError as exc:
            return self._fail(exc)
        if not body.get("ok"):
            return self._fail(f"HTTP {resp.status_code}: {body.get('description', 'keine Beschreibung')}")
        result = body.get("result")
        ref = str(result["message_id"]) if isinstance(result, dict) and "message_id" in result else None
        return SendResult(True, ref=ref, data=result if isinstance(result, dict) else {})

    @staticmethod
    def keyboard(buttons: list[tuple[str, str]]) -> dict[str, Any] | None:
        if not buttons:
            return None
        for _, data in buttons:
            if len(data.encode()) > 64:
                raise ValueError("callback_data darf höchstens 64 Bytes lang sein")
        return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in buttons]]}

    def send(self, text: str, buttons: list[tuple[str, str]] | None = None, silent: bool = False) -> SendResult:
        payload: dict[str, Any] = {"chat_id": self.env["TELEGRAM_CHAT_ID"], "text": text[:TELEGRAM_MAX], "disable_notification": silent,
                                   "link_preview_options": {"is_disabled": True}}
        markup = self.keyboard(buttons or [])
        if markup:
            payload["reply_markup"] = markup
        return self._call("sendMessage", payload)

    def edit(self, message_id: str, text: str, buttons: list[tuple[str, str]] | None = None) -> SendResult:
        payload: dict[str, Any] = {"chat_id": self.env["TELEGRAM_CHAT_ID"], "message_id": int(message_id), "text": text[:TELEGRAM_MAX]}
        markup = self.keyboard(buttons or [])
        if markup:
            payload["reply_markup"] = markup
        r = self._call("editMessageText", payload)
        if r.ok and not r.ref:
            r.ref = message_id
        return r

    def check(self) -> SendResult:
        try:
            resp = self.client.get(f"{TELEGRAM_API}/bot{self.env['TELEGRAM_BOT_TOKEN']}/getMe", timeout=TIMEOUT)
            body = self._json(resp)
        except httpx.HTTPError as exc:
            return self._fail(exc)
        if not body.get("ok"):
            return self._fail(f"getMe: HTTP {resp.status_code}")
        result = body.get("result") or {}
        return SendResult(True, ref=None, data={"username": result.get("username")} if isinstance(result, dict) else {})


class Pushover(_Sender):
    name = "PUSHOVER"
    env_names = PUSHOVER_ENV

    def _auth(self) -> dict[str, str]:
        return {"token": self.env["PUSHOVER_APP_TOKEN"], "user": self.env["PUSHOVER_USER_KEY"]}

    def send(self, title: str, message: str, priority: int = 0, retry: int = 60, expire: int = 10800) -> SendResult:
        data: dict[str, Any] = {**self._auth(), "title": title[:250], "message": message[:PUSHOVER_MAX], "priority": priority}
        if priority == 2:
            data["retry"] = max(30, retry)
            data["expire"] = min(10800, expire)
        try:
            resp = self.client.post(f"{PUSHOVER_API}/messages.json", data=data, timeout=TIMEOUT)
            body = self._json(resp)
        except httpx.HTTPError as exc:
            return self._fail(exc)
        if body.get("status") != 1:
            return self._fail(f"HTTP {resp.status_code}: {'; '.join(map(str, body.get('errors') or [])) or 'abgelehnt'}")
        return SendResult(True, ref=str(body["receipt"]) if body.get("receipt") else None)

    def receipt(self, receipt: str) -> SendResult:
        """Quittungs-API: `acknowledged` = 1, sobald auf dem Gerät bestätigt wurde."""
        try:
            resp = self.client.get(f"{PUSHOVER_API}/receipts/{receipt}.json", params={"token": self.env["PUSHOVER_APP_TOKEN"]}, timeout=TIMEOUT)
            body = self._json(resp)
        except httpx.HTTPError as exc:
            return self._fail(exc)
        if body.get("status") != 1:
            return self._fail(f"Quittung: HTTP {resp.status_code}")
        return SendResult(True, ref=receipt, data=body)

    def cancel(self, receipt: str) -> SendResult:
        try:
            resp = self.client.post(f"{PUSHOVER_API}/receipts/{receipt}/cancel.json", data={"token": self.env["PUSHOVER_APP_TOKEN"]}, timeout=TIMEOUT)
            body = self._json(resp)
        except httpx.HTTPError as exc:
            return self._fail(exc)
        return SendResult(body.get("status") == 1, ref=receipt, error=None if body.get("status") == 1 else f"HTTP {resp.status_code}")

    def check(self) -> SendResult:
        try:
            resp = self.client.post(f"{PUSHOVER_API}/users/validate.json", data=self._auth(), timeout=TIMEOUT)
            body = self._json(resp)
        except httpx.HTTPError as exc:
            return self._fail(exc)
        if body.get("status") != 1:
            return self._fail(f"users/validate: HTTP {resp.status_code}")
        return SendResult(True)


class Email(_Sender):
    name = "EMAIL"
    env_names = EMAIL_ENV

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.env['RESEND_API_KEY']}"}

    def send(self, subject: str, text: str) -> SendResult:
        to = [a.strip() for a in self.env["ALERT_EMAIL_TO"].split(",") if a.strip()]
        try:
            resp = self.client.post(f"{RESEND_API}/emails", headers=self._headers(),
                                    json={"from": self.env["ALERT_EMAIL_FROM"], "to": to, "subject": subject[:200], "text": text}, timeout=TIMEOUT)
            body = self._json(resp)
        except httpx.HTTPError as exc:
            return self._fail(exc)
        if resp.status_code >= 300 or not body.get("id"):
            return self._fail(f"HTTP {resp.status_code}: {body.get('message', 'abgelehnt')}")
        return SendResult(True, ref=str(body["id"]))

    def check(self) -> SendResult:
        """Günstiger authentifizierter Aufruf. Ein Schlüssel nur mit Senderecht antwortet 401 «restricted_api_key» – gültig."""
        try:
            resp = self.client.get(f"{RESEND_API}/domains", headers=self._headers(), timeout=TIMEOUT)
            body = self._json(resp)
        except httpx.HTTPError as exc:
            return self._fail(exc)
        if resp.status_code == 200 or body.get("name") == "restricted_api_key":
            return SendResult(True)
        return self._fail(f"domains: HTTP {resp.status_code}")


@dataclass(slots=True)
class Channels:
    telegram: Telegram
    pushover: Pushover
    email: Email
    owned_client: httpx.Client | None = None

    def by_name(self) -> dict[str, _Sender]:
        return {"TELEGRAM": self.telegram, "PUSHOVER": self.pushover, "EMAIL": self.email}

    def close(self) -> None:
        if self.owned_client is not None:
            self.owned_client.close()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, client: httpx.Client | None = None) -> Channels:
        source = os.environ if env is None else env
        http = client or httpx.Client(timeout=TIMEOUT)
        return cls(Telegram(source, http), Pushover(source, http), Email(source, http), None if client else http)
