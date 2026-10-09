"""Vercel-Cron-Einstieg des Lernlabors (Cron vorerst aus, maxDuration 300 s).

Ein zustandsloser Schritt: ein wenig Forschungsdaten nachladen (Bitstamp, höflich), dann laufende Experimente
innerhalb des Zeitbudgets fortsetzen (Fortschritt in `experiment.progress`). Geschützt über CRON_SECRET wie
`api/tick.py`. Kein Orderweg, keine Handels-Zugangsdaten.

Umgebung: DATABASE_URL (Pflicht), CRON_SECRET (Pflicht), LAB_TIME_BUDGET_S (optional, Standard 240),
LAB_SYNC_REQUESTS (optional, Standard 4 Abrufe je Aufruf).
"""

import hmac
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from tradingengine.adapters.bitstamp_public import BitstampPublic  # noqa: E402
from tradingengine.deployment import runs_worker  # noqa: E402
from tradingengine.research import data, runner  # noqa: E402
from tradingengine.schema_version import SCHEMA_VERSION  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


def lab_step(dsn: str) -> dict[str, object]:
    started = time.perf_counter()
    budget = float(os.environ.get("LAB_TIME_BUDGET_S", "240"))
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        row = conn.execute("select version from schema_meta where id = 1").fetchone()
        if row is None or int(row["version"]) != SCHEMA_VERSION:
            raise SystemExit(f"Schema-Version {None if row is None else row['version']} ≠ erwartet {SCHEMA_VERSION}")
        sync = data.sync_research_data(conn, BitstampPublic(), datetime.now(UTC), int(os.environ.get("LAB_SYNC_REQUESTS", "4")))
        remaining = max(10.0, budget - (time.perf_counter() - started))
        step = runner.run_step(conn, datetime.now(UTC), remaining)
        conn.execute(
            """insert into heartbeat (service, last_seen, schema_version, detail) values ('lab', %s, %s, %s)
               on conflict (service) do update set last_seen = excluded.last_seen, schema_version = excluded.schema_version, detail = excluded.detail""",
            (datetime.now(UTC), SCHEMA_VERSION, Jsonb(json.loads(json.dumps({"sync_requests": sync.requests, "step": step}, default=str)))),
        )
    return {"sync": {"requests": sync.requests, "inserted": sync.inserted, "errors": sync.errors}, "step": step,
            "seconds": round(time.perf_counter() - started, 1)}


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
            self._send(200, lab_step(dsn))
        except SystemExit as exc:
            self._send(503, {"error": str(exc)})
        except Exception as exc:
            logging.exception("Laborschritt fehlgeschlagen")
            self._send(500, {"error": type(exc).__name__})

    def _send(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
