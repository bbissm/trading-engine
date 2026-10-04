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
3. Migration ausführen: `cd web && DATABASE_URL=… pnpm db:migrate`. Migrationen laufen bewusst nicht im
   Build; Reihenfolge bei Schemaänderungen: Migration → Engine ausrollen → Web ausrollen.
4. Getrennte Datenbankrollen (`web`, `engine_paper`, `engine_live`, `engine_lab`) werden eingeführt,
   sobald es Orders gibt (Etappe 2/4). Bis dahin nutzen Web und Engine denselben Zugang.

## 3. Web-App (Vercel)

1. Neues Vercel-Projekt aus dem Repo `bbissm/trading-engine`, **Root Directory `web`**, Region `fra1`.
2. Umgebungsvariablen gemäss `web/.env.example` setzen (Datenbank, Anmeldung).
3. Die Anmeldung ist derzeit die einfache Ein-Benutzer-Sitzung wie im Control Center. Passkey + TOTP und
   Step-up (Task E0-4) müssen vor jeder Live-Funktion folgen.

## 4. Engine-Server

1. Kleiner VPS in der EU (z. B. Hetzner CX23; Verfügbarkeit prüfen), Docker installieren, Firewall nur SSH.
2. Repo klonen, `.env` neben `docker-compose.yml` anlegen:
   ```
   DATABASE_URL=postgresql://…          # Neon, direkte (nicht gepoolte) Verbindung
   HEALTHCHECK_URL=https://hc-ping.com/…
   ```
3. `docker compose up -d --build engine`. Der Container startet nach Neustarts selbst wieder.
4. Externer Heartbeat: bei healthchecks.io eine Prüfung mit Periode 1 min und Karenz 3 min anlegen,
   Ping-URL als `HEALTHCHECK_URL` eintragen und dort einen Benachrichtigungskanal (E-Mail, später
   Pushover/Telegram) hinterlegen. Test: `docker compose stop engine` → Alarm innert ca. 5 min.

Noch offen aus Etappe 0: automatisches Ausrollen per GitHub Action (E0-5), zweiter unabhängiger
Watchdog als Vercel-Cron (E0-6), Passkey-Anmeldung (E0-4), Eintrag im Control Center (E0-8).

## 5. Was im Betrieb zu sehen ist

- **Übersicht:** Heartbeat der Engine (grün, wenn jünger als 3 min), Feed-Status je Instrument/Zeitebene.
- **Signale:** Entscheidungen der Strategie `s1-trend-pullback@1` zu jeder abgeschlossenen 4h- und
  Tageskerze. Der Hinweis «ungeprüft – kein Qualitätsnachweis» gilt, bis die Gates aus docs/04 bestanden sind.
- **Verbindungen & Betrieb:** «Engine-Ping» prüft den Weg Web → Datenbank → Engine → Datenbank.
