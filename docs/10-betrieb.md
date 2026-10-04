# 10 · Betrieb und Einrichtung

Stand: erster vertikaler Schnitt (Signalbetrieb ohne Orders). Diese Schritte brauchen Konten bzw. Käufe
und sind deshalb **von dir** auszuführen; im Repo ist alles dafür vorbereitet.

## 1. Lokal ausprobieren (ohne Cloud)

```
docker compose --profile local up -d db                       # Postgres auf 127.0.0.1:55432
cd web && pnpm install
DATABASE_URL=postgresql://postgres:dev@localhost:55432/postgres pnpm db:migrate
cd ../engine && uv sync
DATABASE_URL=postgresql://postgres:dev@localhost:55432/postgres uv run tradingengine run
cd ../web && pnpm dev                                          # .env.local gemäss web/.env.example
```

## 2. Datenbank (Neon)

1. Im Vercel-Dashboard über Marketplace → Neon ein Projekt in der EU (Frankfurt) anlegen und mit dem
   Vercel-Projekt `trading-engine` verbinden; `DATABASE_URL` wird als Umgebungsvariable gesetzt.
2. Plan **Launch** wählen und Scale-to-zero deaktivieren – die Engine ist dauerhaft verbunden
   (Kosten: docs/09).
3. Migrationen laufen im Vercel-Build (`pnpm vercel-build`), weil die Neon-Zugangsdaten als «sensitive»
   hinterlegt und ausserhalb von Vercel nicht lesbar sind. Bei Schemaänderungen die Engine direkt nach
   dem Web-Deploy mit der neuen Schema-Version ausrollen.
4. Die Engine verbindet sich mit der eigenen Rolle `te_engine` (nur die nötigen Rechte; `signal`, `candle`,
   `feature_snapshot`, `audit_event` sind für sie nur einfügbar). Der Build legt sie an, wenn
   `TE_ENGINE_DB_PASSWORD` gesetzt ist, und schreibt Host und Datenbankname ins Build-Log. Weitere Rollen
   (`engine_paper`, `engine_live`, `engine_lab`) folgen mit den Orders (Etappe 2/4).

## 3. Web-App (Vercel)

1. Neues Vercel-Projekt aus dem Repo `bbissm/trading-engine`, **Root Directory `web`**, Region `fra1`.
2. Umgebungsvariablen gemäss `web/.env.example` setzen (Datenbank, Anmeldung).
3. Die Anmeldung ist derzeit die einfache Ein-Benutzer-Sitzung wie im Control Center. Passkey + TOTP und
   Step-up (Task E0-4) müssen vor jeder Live-Funktion folgen.

## 4. Engine (Vercel-Projekt `trading-engine-worker`)

Die Engine läuft als eigenes Vercel-Projekt mit Root Directory `engine` (Entscheid E-1 in docs/11).
`engine/vercel.json` definiert einen Cron, der jede Minute `/api/tick` aufruft.

| Variable (Production) | Zweck |
|---|---|
| `DATABASE_URL` | `postgresql://te_engine:<TE_ENGINE_DB_PASSWORD>@<host>/<datenbank>?sslmode=require` – Host und Datenbank liefert der geschützte Endpunkt `/api/ops/database` der Web-App |
| `CRON_SECRET` | Vercel sendet es bei Cron-Aufrufen als Bearer-Token; ohne gültiges Token antwortet die Funktion mit 401 |
| `HEALTHCHECK_URL` (optional) | Ping-URL eines externen Heartbeat-Dienstes (z. B. healthchecks.io, Periode 1 min, Karenz 3 min) |

Push auf `main` rollt Web und Engine aus. Bei Schemaänderungen läuft die Migration im Web-Build; die Engine
verweigert die Arbeit, solange ihre `SCHEMA_VERSION` nicht zur Datenbank passt.

Lokal bzw. auf einem späteren Server: `docker compose up -d --build engine` mit `DATABASE_URL` in `.env`.
Ein Server wird erst für den Live-Handel über Interactive Brokers nötig (IB Gateway).

Lokale Geheimnisse liegen in `.env.secrets` und `.env` im Repo-Ordner (beide gitignored).

Noch offen aus Etappe 0: externer Heartbeat-Dienst (`HEALTHCHECK_URL` ist leer), zweiter unabhängiger
Watchdog (E0-6), Passkey-Anmeldung (E0-4), Eintrag im Control Center (E0-8).

## 5. Was im Betrieb zu sehen ist

- **Übersicht:** Heartbeat der Engine (grün, wenn jünger als 3 min), Feed-Status je Instrument/Zeitebene.
- **Signale:** Entscheidungen der Strategie `s1-trend-pullback@1` zu jeder abgeschlossenen 4h- und
  Tageskerze. Der Hinweis «ungeprüft – kein Qualitätsnachweis» gilt, bis die Gates aus docs/04 bestanden sind.
- **Verbindungen & Betrieb:** «Engine-Ping» prüft den Weg Web → Datenbank → Engine → Datenbank.
