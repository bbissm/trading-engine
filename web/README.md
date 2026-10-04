# TradingEngine · Web

Next.js-App (App Router) des TradingEngine-Monorepos. Erster vertikaler Schnitt: **nur lesendes Dashboard** über die Daten, die die Python-Engine nach Postgres schreibt, plus genau ein Befehl (`PING`). Keine Handelsbedienung.

Die Web-App ruft die Engine nie direkt auf. Bedienhandlungen sind Zeilen in der Tabelle `command`, die Engine quittiert sie (Dokument 05, Abschnitt 3.2).

## Setup

```bash
pnpm install
cp .env.example .env.local      # DATABASE_URL setzen
pnpm db:migrate                 # spielt ./drizzle ein (Neon oder normales Postgres)
pnpm dev
```

Ohne `DATABASE_URL` starten die Seiten trotzdem und zeigen einen Einrichtungshinweis.

Lokales Postgres zum Ausprobieren:

```bash
docker run -d --name te-pg -e POSTGRES_PASSWORD=dev -p 5432:5432 postgres:17
DATABASE_URL=postgres://postgres:dev@localhost:5432/postgres pnpm db:migrate
```

## Env-Variablen

| Variable | Zweck |
|---|---|
| `DATABASE_URL` (ersatzweise `POSTGRES_URL`) | Postgres. Neon-URLs nutzen den HTTP-Treiber, alle anderen `pg` |
| `TE_PASSWORD` | Passwort des Übergangs-Logins. Ohne Passwort ist die App lokal offen; auf Vercel antwortet sie mit 503 |
| `TE_USER` (optional, Standard `admin`) | Benutzername; erscheint als `user:<name>` in `command.issued_by` und `audit_event.actor` |
| `SESSION_SECRET` (optional) | Zusätzliches Geheimnis für die Session-Signatur. Ändern meldet alle Geräte ab |
| `DATABASE_POOL_MAX` (optional) | Poolgrösse für `pg`, Standard 5 |

## Skripte

| Skript | Zweck |
|---|---|
| `pnpm dev` / `pnpm build` / `pnpm start` | Next.js. **`build` führt keine Migrationen aus** |
| `pnpm db:generate` | Erzeugt aus `src/db/schema.ts` eine neue SQL-Migration in `drizzle/` |
| `pnpm db:migrate` | Spielt die Migrationen ein (`scripts/migrate.mjs`), als eigener Deploy-Schritt |
| `pnpm typecheck` / `pnpm lint` / `pnpm test` | TypeScript, ESLint, Vitest (DB-Tests laufen gegen PGlite mit den echten Migrationen) |

## Schema und Migrationen

Die Schema-Hoheit liegt in `src/db/schema.ts`. Die Engine liest und schreibt dieselben Tabellen, prüft beim Start `schema_meta.version` und spielt in ihren Tests dieselben SQL-Dateien ein, indem sie an `--> statement-breakpoint` trennt. Darum:

- Migrationen bleiben reines SQL. Von Hand ergänzte Statements (z. B. das Setzen von `schema_meta`) stehen hinter einem eigenen `--> statement-breakpoint`.
- Nur additive Änderungen. Bei jeder Migration `SCHEMA_VERSION` erhöhen, hier und in `engine/src/tradingengine/schema_version.py`, und in der Migration `schema_meta.version` nachziehen.
- Preise und Mengen sind `numeric` und kommen als String an. Anzeige über `decimal()`/`price()` in `src/lib/format.ts`, keine Float-Rechnung mit Geld. Einzige Ausnahme: der Chart erhält Zahlen zum Zeichnen.

## Anmeldung (Übergangslösung)

**TODO (Plan-Task E0-4):** Der Login ist derselbe Ein-Benutzer-Mechanismus wie im Control Center (Passwort aus der Umgebung, signiertes HttpOnly-Cookie, Schutz über `src/proxy.ts`). Er **muss durch Passkey + TOTP + Step-up (Better Auth) ersetzt werden, bevor irgendein Bedienelement für den Live-Handel existiert.** Bis dahin darf die App nur lesen und den harmlosen `PING` senden.

## Aufbau

- `src/app` – Seiten: Übersicht `/`, Signale `/signals`, Scanner `/scanner`, Instrumentanalyse `/instruments/[id]`, Verbindungen & Betrieb `/operations`; Platzhalter für Autopilot, Portfolio, Paper-Lab, Strategien & Lernlabor, Journal (sagen, was in welcher Etappe kommt, ohne Beispieldaten).
- `src/lib/data` – Datenzugriff (server-only). `src/lib/health.ts` – Regeln für Lebenszeichen und «Braucht Aufmerksamkeit».
- `src/components` – Shell (untere Tab-Leiste mobil, obere Navigation auf dem Desktop), `ModeBadge` (PAPER / LIVE · Echtgeld / FORSCHUNG, immer mit Wort), Kerzenchart.
- Chart: [`lightweight-charts`](https://github.com/tradingview/lightweight-charts) (Apache-2.0, © TradingView). Die Attribution erfolgt über die Option `attributionLogo` (Logo mit Link zu tradingview.com im Chart). Es wird nur die Bibliothek verwendet, keine TradingView-Daten. Marker stammen ausschliesslich aus gespeicherten Zeilen der Tabelle `signal`.

## Deployment

`vercel.json`: Region `fra1`, noch keine Crons. Migrationen vor dem Deploy mit `pnpm db:migrate` gegen die Zieldatenbank ausführen.
