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
| `TE_PASSWORD` | Passwort des einzigen Benutzers (massgebend; der gespeicherte Hash wird beim Login nachgezogen). Ohne Passwort ist die App lokal offen; auf Vercel (production/preview) antwortet sie mit 503 |
| `TE_ADMIN_EMAIL` | Einzige zugelassene E-Mail-Adresse. Erster Login damit legt den Benutzer an; Registrierung sonst gesperrt |
| `BETTER_AUTH_SECRET` | Geheimnis von Better Auth (Cookie-Signatur, Verschlüsselung von TOTP-Geheimnis und Backup-Codes), ≥ 32 zufällige Zeichen, z. B. `openssl rand -base64 32`. Ändern macht TOTP unlesbar → nie ändern ohne Neueinrichtung |
| `BETTER_AUTH_URL` | Öffentliche Basis-URL, z. B. `https://trading-engine.example.ch`. Bestimmt Passkey-Origin und RP-ID (Host) |
| `TE_API_TOKEN` (optional) | Maschinenzugang für `/api/ops/*`: `Authorization: Bearer <Token>`, mind. 32 Zeichen. Ohne Token kein Skriptzugang |
| `TE_PASSKEY_RP_ID` (optional) | RP-ID der Passkeys, falls abweichend vom Host aus `BETTER_AUTH_URL` (z. B. registrierbare Domain). Ändern macht bestehende Passkeys unbrauchbar |
| `TE_STEP_UP_MAX_AGE_SECONDS` (optional, nur Tests) | Verkürzt das Step-up-Fenster (Standard 300 s); kann es nie verlängern |
| `TE_USER` (optional) | Kennung in `command.issued_by` und fachlichen Audit-Einträgen (`user:<name>`); Standard: lokaler Teil von `TE_ADMIN_EMAIL`, sonst `admin`. Anmeldeereignisse nutzen `user:<email>` |
| `DATABASE_POOL_MAX` (optional) | Poolgrösse für `pg`, Standard 5 |
| `TE_ENGINE_DB_PASSWORD` (optional) | Passwort der Datenbankrolle `te_engine` (mind. 32 alphanumerische Zeichen). Ist es gesetzt, legt der Vercel-Build die Rolle mit minimalen Rechten an |

## Skripte

| Skript | Zweck |
|---|---|
| `pnpm dev` / `pnpm build` / `pnpm start` | Next.js. **`build` führt keine Migrationen aus** |
| `pnpm vercel-build` | Build auf Vercel: Migrationen → Engine-Datenbankrolle (`scripts/roles.mjs`) → `next build`. Die Neon-Zugangsdaten sind als «sensitive» hinterlegt und nur dort verfügbar |
| `pnpm db:generate` | Erzeugt aus `src/db/schema.ts` eine neue SQL-Migration in `drizzle/` |
| `pnpm db:migrate` | Spielt die Migrationen ein (`scripts/migrate.mjs`), als eigener Deploy-Schritt |
| `pnpm typecheck` / `pnpm lint` / `pnpm test` | TypeScript, ESLint, Vitest (DB-Tests laufen gegen PGlite mit den echten Migrationen) |

## Schema und Migrationen

Die Schema-Hoheit liegt in `src/db/schema.ts`. Die Engine liest und schreibt dieselben Tabellen, prüft beim Start `schema_meta.version` und spielt in ihren Tests dieselben SQL-Dateien ein, indem sie an `--> statement-breakpoint` trennt. Darum:

- Migrationen bleiben reines SQL. Von Hand ergänzte Statements (z. B. das Setzen von `schema_meta`) stehen hinter einem eigenen `--> statement-breakpoint`.
- Nur additive Änderungen. Bei jeder Migration `SCHEMA_VERSION` erhöhen, hier und in `engine/src/tradingengine/schema_version.py`, und in der Migration `schema_meta.version` nachziehen.
- Preise und Mengen sind `numeric` und kommen als String an. Anzeige über `decimal()`/`price()` in `src/lib/format.ts`, keine Float-Rechnung mit Geld. Einzige Ausnahme: der Chart erhält Zahlen zum Zeichnen.

## Anmeldung (E0-4, Better Auth)

- Genau ein Benutzer (`TE_ADMIN_EMAIL` + `TE_PASSWORD`). Beim ersten Login wird **TOTP** eingerichtet (QR-Code, Backup-Codes einmalig), vorher ist nichts anderes erreichbar (`/setup`). Danach wird ein **Passkey** empfohlen; mit Passkey genügt er allein, sonst Passwort + TOTP oder Backup-Code.
- Sitzungen: HttpOnly-, Secure-, SameSite=Lax-Cookies, 30 Tage gleitend, in der DB (`auth_session`); der Proxy (`src/proxy.ts`, Node.js-Runtime) prüft jede Anfrage. Rate-Limits von Better Auth in `auth_rate_limit`.
- **Step-up** für heikle Aktionen: serverseitig `requireStepUp()` aus `src/lib/auth/step-up.ts`, im Client `useStepUp()` / `<StepUpDialog>` aus `src/components/step-up.tsx` («Bestätigen mit Passkey oder TOTP», gilt 5 Minuten je Sitzung).
- `/settings/security`: Passkeys (Entfernen mit Step-up), TOTP-Status und neue Backup-Codes (Step-up), aktive Sitzungen mit «Beenden» und «Überall abmelden», letzte Anmeldungen aus `audit_event`.
- Skripte: `/api/ops/*` mit `Authorization: Bearer $TE_API_TOKEN`. Basic Auth gibt es nicht mehr. `/api/auth/*`, `/api/telegram` und `/api/cron/*` schützt der Proxy nicht (authentisieren sich selbst).
- Datenbank: Tabellen `auth_*` (`src/db/auth-schema.ts`), Drizzle-Adapter ohne Transaktionen (Neon-HTTP). Die Engine-Rolle hat darauf keine Rechte.
- Details und Abweichungen vom Plan: `docs/11-entscheide.md`, Abschnitt E0-4.

## Aufbau

- `src/app` – Seiten: Übersicht `/`, Signale `/signals`, Scanner `/scanner`, Instrumentanalyse `/instruments/[id]`, Verbindungen & Betrieb `/operations`; Platzhalter für Autopilot, Portfolio, Paper-Lab, Strategien & Lernlabor, Journal (sagen, was in welcher Etappe kommt, ohne Beispieldaten).
- `src/lib/data` – Datenzugriff (server-only). `src/lib/health.ts` – Regeln für Lebenszeichen und «Braucht Aufmerksamkeit».
- `src/components` – Shell (untere Tab-Leiste mobil, obere Navigation auf dem Desktop), `ModeBadge` (PAPER / LIVE · Echtgeld / FORSCHUNG, immer mit Wort), Kerzenchart.
- Chart: [`lightweight-charts`](https://github.com/tradingview/lightweight-charts) (Apache-2.0, © TradingView). Die Attribution erfolgt über die Option `attributionLogo` (Logo mit Link zu tradingview.com im Chart). Es wird nur die Bibliothek verwendet, keine TradingView-Daten. Marker stammen ausschliesslich aus gespeicherten Zeilen der Tabelle `signal`.

## Deployment

`vercel.json`: Region `fra1`, noch keine Crons. Migrationen vor dem Deploy mit `pnpm db:migrate` gegen die Zieldatenbank ausführen.
