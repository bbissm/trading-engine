"""Vercel-Cron-Einstieg des Live-Autopiloten (Cron vorerst aus, maxDuration 60).

Ohne `LIVE_TRADING_ENABLED == "true"` kehrt der Aufruf sofort mit {"live": "disabled"} zurück – kein
Datenbankzugriff, kein Anbieteraufruf. Geschützt über CRON_SECRET wie api/tick.py. Antworten und Logs enthalten
nie Zugangsdaten.
"""

import hmac
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tradingengine.deployment import runs_live  # noqa: E402
from tradingengine.live.locks import live_enabled  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


class handler(BaseHTTPRequestHandler):  # noqa: N801 (Name von der Vercel-Python-Laufzeit vorgegeben)
    def do_GET(self) -> None:  # noqa: N802
        secret = os.environ.get("CRON_SECRET", "")
        provided = self.headers.get("authorization", "")
        if not secret or not hmac.compare_digest(provided, f"Bearer {secret}"):
            self._send(401, {"error": "unauthorized"})
            return
        if not runs_live():
            self._send(200, {"live": "disabled", "reason": "Live läuft nur im Projekt mit ENGINE_ROLE=live"})
            return
        if not live_enabled():
            self._send(200, {"live": "disabled"})
            return
        try:
            from tradingengine.live.session import run_live

            result = run_live()
            self._send(503 if result.get("live") == "error" else 200, result)
        except Exception as exc:
            logging.error("Live-Durchlauf fehlgeschlagen: %s", type(exc).__name__)
            self._send(500, {"live": "error", "error": type(exc).__name__})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 – keine Anfragezeilen ins Log (Header bleiben draussen)
        return

    def _send(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
