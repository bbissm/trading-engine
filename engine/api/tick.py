"""Vercel-Cron-Einstieg: ein zustandsloser Arbeitsschritt der Engine pro Aufruf (vercel.json: jede Minute).

Geschützt über CRON_SECRET – Vercel sendet bei Cron-Aufrufen "Authorization: Bearer $CRON_SECRET".
Kein Orderweg, keine Handels-Zugangsdaten.
"""

import hmac
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tradingengine.cli import tick  # noqa: E402
from tradingengine.deployment import runs_worker  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


class handler(BaseHTTPRequestHandler):  # noqa: N801 (Name von der Vercel-Python-Laufzeit vorgegeben)
    def do_GET(self) -> None:  # noqa: N802
        secret = os.environ.get("CRON_SECRET", "")
        provided = self.headers.get("authorization", "")
        if not secret or not hmac.compare_digest(provided, f"Bearer {secret}"):
            self._send(401, {"error": "unauthorized"})
            return
        if not runs_worker():
            self._send(200, {"skipped": "ENGINE_ROLE=live – dieses Projekt führt nur den Live-Autopiloten aus"})
            return
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            self._send(503, {"error": "DATABASE_URL fehlt"})
            return
        try:
            self._send(200, tick(dsn, os.environ.get("HEALTHCHECK_URL")))
        except SystemExit as exc:  # Schema-Version passt nicht
            self._send(503, {"error": str(exc)})
        except Exception as exc:
            logging.exception("Tick fehlgeschlagen")
            self._send(500, {"error": type(exc).__name__})

    def _send(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
