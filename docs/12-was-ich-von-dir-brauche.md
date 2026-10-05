# 12 · Was von dir gebraucht wird

Stand: 5. Oktober 2026. Alles, was ohne deine Konten möglich ist, ist gebaut und deployt. Diese Liste nennt,
was nur du liefern kannst, wo es eingetragen wird und was dadurch freigeschaltet wird. Reihenfolge = Nutzen.

Variablen werden im Vercel-Dashboard unter *Project → Settings → Environment Variables* (Production) gesetzt,
als «Sensitive». Danach einmal neu deployen (oder mir sagen – dann setze ich sie per CLI, wenn du mir die Werte gibst).
Projekte: **web** = `trading-engine`, **worker** = `trading-engine-worker`, **live** = `trading-engine-live` (nur für Echtgeld).

## A. Sofort (5 Minuten, kostenlos)

| # | Was | Wo | Schaltet frei |
|---|---|---|---|
| A1 | **Erster Login:** `https://trading-engine-sable.vercel.app`, E-Mail und Passwort aus `.env.secrets`; TOTP einrichten (Authenticator-App), Backup-Codes sichern, danach Passkey hinzufügen | Browser | sichere Anmeldung; Step-up für spätere Live-Aktionen |
| A2 | **Telegram-Bot:** bei @BotFather `/newbot` → Token; dem Bot einmal schreiben; Chat-ID z. B. über @userinfobot | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` in web **und** worker; zusätzlich `TELEGRAM_WEBHOOK_SECRET` (beliebige lange Zufallszeichenfolge) in web | Meldungen aufs Handy, Schaltflächen «Bestätigen» / «Einstiege pausieren», `/status` |
| A3 | **Pushover** (einmalig USD 4.99 je Plattform): Konto + App anlegen | `PUSHOVER_APP_TOKEN`, `PUSHOVER_USER_KEY` in web und worker | kritische Alarme, die sich wiederholen, bis du quittierst – auch nachts |
| A4 | **Testalarm bestätigen:** unter «Meldungen» → «Testalarm senden», auf dem Handy quittieren | App | Voraussetzung für jede Live-Aktivierung (höchstens 7 Tage alt) |

Nach A2 muss der Telegram-Webhook einmal registriert werden (ein Aufruf, steht in `web/src/lib/notify/telegram.ts`);
das erledige ich, sobald die Variablen gesetzt sind.

## B. Für Aktien/ETFs im Paper-Betrieb (10 Minuten, kostenlos)

| # | Was | Wo | Schaltet frei |
|---|---|---|---|
| B1 | **Alpaca Paper-Only-Konto** (nur E-Mail, keine Einzahlung) → API-Schlüssel erzeugen | `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY` im worker | Tageskerzen für 12 ETFs und 8 Aktien, Signale nach US-Börsenschluss, eigenes Paper-Konto «US-Aktien» mit IBKR-Kostenmodell |

Ohne B1 bleibt der Aktien-Teil unsichtbar; nichts ist kaputt.

## C. Optional, verbessert den Betrieb

| # | Was | Wo | Nutzen |
|---|---|---|---|
| C1 | **healthchecks.io** (kostenlos): Prüfung mit Periode 1 min, Karenz 3 min | `HEALTHCHECK_URL` im worker | dritter, völlig unabhängiger Alarmweg, falls Vercel selbst ausfällt |
| C2 | **Resend** (kostenlos bis 3 000 Mails/Monat) + verifizierte Absenderadresse | `RESEND_API_KEY`, `ALERT_EMAIL_TO`, `ALERT_EMAIL_FROM` im worker | E-Mail als Fallback-Stufe und Tagesbericht |
| C3 | **Eigene Domain**, z. B. `trading.techengine.ch`: DNS-Eintrag (CNAME auf `cname.vercel-dns.com`) bei Hostpoint; danach `BETTER_AUTH_URL` umstellen | DNS + web | Zugang ohne vorgeschalteten Vercel-Login; **Passkeys sind an die Domain gebunden** – am besten vor dem Einrichten der Passkeys entscheiden |
| C4 | Eintrag im Control Center (`dashboard/src/config/projects.ts`) | anderes Repo | Kostenübersicht aller Projekte an einem Ort |

## D. Entscheidungen von dir (kein Zugang nötig)

| # | Frage | Warum sie zählt |
|---|---|---|
| D1 | **Pilotbudget** und Aufteilung Crypto / Aktien | bestimmt Positionsgrössen; unter ca. USD 8 000 für Aktien drücken Mindestgebühren spürbar (docs/09, Abschnitt 3) |
| D2 | **Genügt eine Reaktionszeit von bis zu einer Minute** für Orderabgleich und Schutzreparatur bei Kraken-Live? | sonst braucht es einen Dauerlauf-Server (docs/11, E-1) |
| D3 | **Wöchentliche manuelle IBKR-Anmeldung** akzeptabel? | Voraussetzung für Aktien-Echtgeld über Interactive Brokers; ohne sie bleibt Aktien nur Paper |
| D4 | **Notfallpolicy** Live: «halten mit Schutz» (Standard) oder «schliessen» | gilt bei Drawdown-Stufe 2 und im Notfallmodus |
| D5 | **Steuerliche Einordnung** mit Steuerberatung klären (privater Kapitalgewinn vs. gewerbsmässiger Wertschriftenhandel) | die App liefert nur die Daten (Exporte); häufiger automatischer Handel kann die Kriterien berühren |

## E. Für Echtgeld mit Kraken – erst wenn eine Strategie die Gates bestanden hat

Der Live-Orderweg ist gebaut und **mehrfach gesperrt**. Heute besteht keine Strategie die Gates (siehe unten),
deshalb gibt es nichts freizugeben. Wenn es so weit ist:

| # | Was | Wo |
|---|---|---|
| E1 | Kraken-Konto verifizieren, bestätigen, dass Spot-Handel für Wohnsitz Schweiz verfügbar ist, tatsächliche Gebührenstufe prüfen, CHF → USD klären | Kraken |
| E2 | API-Schlüssel **nur** mit: Query Funds, Query Open/Closed Orders, Create & Modify Orders, Cancel/Close Orders, Query Ledger. **Kein Withdraw, kein Deposit.** | `KRAKEN_API_KEY`, `KRAKEN_API_SECRET` **nur im live-Projekt** (nie im Browser, nie im Chat, nie im worker) |
| E3 | Im Live-Assistenten (`/live`): Konto registrieren (prüft, dass kein Auszahlungsrecht besteht), Mandat mit Budget anlegen, Strategieversion für Live freigeben – jeweils mit Step-up | App |
| E4 | `LIVE_TRADING_ENABLED=true` im live-Projekt und im web-Projekt setzen | Vercel |
| E5 | Vor dem ersten echten Auftrag: Vertragstests mit Krakens `validate`-Modus und zwei manuelle Kleinstorders nach Protokoll – nur mit deiner ausdrücklichen Zustimmung | gemeinsam |

Hinweise dazu: Eine IP-Allowlist für den Schlüssel ist mit Vercel nicht möglich (wechselnde Ausgangsadressen).
Die Live-Funktion läuft bereits in einem eigenen Vercel-Projekt mit eigener Datenbankrolle (docs/11, E-8); nur dort
gehören die Kraken-Schlüssel hin.

## F. Für Echtgeld mit Aktien (Interactive Brokers) – später

IBKR-Pro-Konto mit mindestens USD 500, zweiter Benutzername für die Engine, Paper-Konto, Status
«Non-Professional», Marktdaten-Abos (ab ca. USD 4.50/Monat), Bestätigung, dass US-ETFs für dein Konto
handelbar sind – und ein kleiner Server für das IB Gateway (ca. EUR 9/Monat). Der IBKR-Adapter ist **nicht**
gebaut: ohne Konto und Gateway lässt er sich nicht gegen die echte Schnittstelle prüfen.

## Was ohne dich schon läuft

- Signalbetrieb für fünf Crypto-Paare, Paper-Autopilot «Paper 1» (USD 10 000 virtuell), Wächter für Verlustgrenzen,
  tägliche USD/CHF-Kurse, Journal mit Exporten, Lernlabor mit eigenem Zeitplan, Meldungen in der App.
- Der ehrliche Zwischenstand des Lernlabors (Walk-forward 2015–2025, BTC und ETH, Kraken-Kosten): **keine Strategie
  besteht Gate G1.** S1 und S2 auf Tageskerzen: zu wenige unabhängige Fälle und nicht besser als Zufallseinstiege;
  S3: praktisch keine Trades; S1 auf 4h: nach Kosten negativ, abgelehnt. Das System zeigt das so an und schlägt
  nichts zur Freigabe vor.
