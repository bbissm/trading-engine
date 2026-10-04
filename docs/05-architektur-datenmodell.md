# 05 · Technische Architektur, Hosting und Datenmodell

Preis- und Dokumentationsstand: 4. Oktober 2026. «V» = heute auf der offiziellen Seite geprüft, «NV» = nicht verifiziert.

## 1. Herleitung aus den Anforderungen

| Anforderung | Technische Folge |
|---|---|
| Handel, Positionsbetreuung und Lernen laufen bei geschlossenem Browser | dauerhaft laufender Prozess mit eigenem Zustand, kein Request-getriebenes Modell |
| Schutz offener Positionen darf nicht vom eigenen Prozess abhängen | Schutzorders liegen beim Anbieter; eigener Prozess ist zweite Linie |
| Dauerhafte Streams zu Broker/Exchange (WebSocket, IB-Gateway-Socket) | langlebige ausgehende Verbindungen, Reconnect-Logik, feste IP für Schlüssel-Allowlist |
| Kein Doppelkauf bei Neustart/Parallelität | transaktionale Datenbank als einzige Wahrheit für Absichten, Reservierungen, Orderzustände |
| Backtest, Paper und Live sollen dieselbe Entscheidung treffen | **eine** Codebasis für Strategie, Risiko, Order-Zustandsautomat; nur Datenquelle und Ausführungsadapter wechseln |
| ML mit sauberer Validierung | Python-Ökosystem (scikit-learn, LightGBM, Optuna) |
| Dashboard wie ContentEngine/CommerceEngine, auf Vercel | Next.js-App nach den dort vorhandenen Konventionen |

### Was Vercel sinnvoll übernimmt – und was nicht

| Aufgabe | Vercel? | Begründung (Quelle) |
|---|---|---|
| Dashboard, Auth, API für Bedienbefehle, Exporte | **ja** | Kernkompetenz |
| Leichte zeitgesteuerte Aufgaben (Tagesbericht, Watchdog) | **ja** | Cron: 100 pro Projekt; Hobby nur 1×/Tag mit ±59 min, Pro minutengenau (V, vercel.com/docs/cron-jobs/usage-and-pricing) |
| LLM-Erklärtexte, Berichtserzeugung | **ja** | kurze, zustandslose Schritte; Workflows für mehrstufige Abläufe (V, vercel.com/docs/workflows/pricing) |
| Marktdaten-Streams 24/7, Order-/Positionsmanager, Risikoschleife | **nein** | Functions enden spätestens nach 300 s (Hobby) bzw. 800 s, erweitert 1800 s Beta (Pro); WebSocket-Verbindungen schliessen bei Erreichen der Maximaldauer (V, vercel.com/docs/functions/limitations, /functions/websockets). Ein Cron-Takt ersetzt keine stehende Verbindung und keinen im Sekundenbereich reagierenden Orderabgleich. |
| IB Gateway (Java-Prozess mit Sitzung) | **nein** | braucht dauerhaften Prozess |
| Backtests/Training | **bedingt** | Vercel Sandbox bis 45 min (Hobby) / 24 h (Pro) wäre möglich (V, vercel.com/docs/sandbox/pricing); da ohnehin ein Worker existiert und dieselbe Python-Codebasis nutzt, läuft das Labor dort – einfacher und ohne Zusatzkosten |

**Entscheid:** Vercel für die Web-App; ein kleiner, dauerhaft laufender **Engine-Server** (VPS) für alles, was Zustand und stehende Verbindungen braucht. Eine reine Vercel-Lösung wäre für Echtgeld technisch ungeeignet.

## 2. Passung zu den bestehenden Projekten (im Nachbarordner gelesen)

| Beobachtung in `commerce-engine`, `dashboard`, `content-engine` | Übernahme in TradingEngine |
|---|---|
| Next.js 16, React 19, Tailwind 4, TypeScript, Vitest; Region `fra1` | identisch |
| Neon Postgres + Drizzle (`commerce-engine`, `dashboard`); Migrationen beim Build; Tests mit PGlite | identisch für die Web-App; Schema-Hoheit siehe 4.3 |
| Struktur `src/core` (reine Domänenlogik) / `src/ports` / `src/adapters` | gleiche Schichtung in Web und Engine |
| Eigene schlanke UI-Bausteine, `mobile-shell` mit unterer Tab-Leiste und «Mehr», Theme-Umschalter über `data-theme`, deutsche Oberfläche | übernehmen: Tabs **Übersicht · Signale · Autopilot · Portfolio · Mehr** |
| Farb-Tokens des `dashboard` (`--good`, `--warning`, `--serious`, `--critical`, Serienfarben `--s1…`) | als Ausgangspalette; zusätzlich feste Modus-Tokens `--mode-paper`, `--mode-live`, `--mode-research` |
| `recharts` im `dashboard` | für Equity-/Drawdown-Kurven; Kerzencharts mit `lightweight-charts` |
| Beträge in Cent (`commerce-engine`) | Geldbeträge als Dezimalwerte mit fester Skala (`numeric`), nie Float; Crypto-Mengen bis 8 Nachkommastellen |
| Plan-Dokumente nummeriert unter `docs/`, `CLAUDE.md` mit Projekt-Notizen | diese Dokumentstruktur |
| `dashboard/src/config/projects.ts` führt alle Projekte | Task: TradingEngine dort eintragen (Kosten erscheinen im Control Center) |

`dashboard` nutzt einen stündlichen Cron – das setzt den Pro-Plan voraus (Hobby würde den Deploy ablehnen). Annahme A-VERCEL: das Team läuft auf Pro; im Vercel-Dashboard zu bestätigen.

## 3. Komponenten

```
┌────────────── Vercel (fra1) ──────────────┐        ┌──────────── Engine-Server (VPS, EU) ────────────┐
│ Next.js-App                               │        │ Docker Compose                                   │
│  · Dashboard (Lesen)                      │        │  marketdata   Feeds → Kerzen/Quotes, Qualität    │
│  · Auth (Passkey + TOTP, Step-up)         │        │  signals      Features, Regime, Strategien       │
│  · Befehle → Tabelle `command`            │        │  paper        Paper-Autopiloten + Simulator      │
│  · Cron: Watchdog, Tagesbericht           │        │  live         Live-Autopilot, Adapter, Abgleich  │
│  · LLM-Erklärtexte, Exporte               │        │  guardian     Risiko-/Limit-Wächter, Heartbeats  │
└──────────────┬────────────────────────────┘        │  notifier     Zustellung, Eskalation, Bot-Befehle│
               │ SQL (TLS)                           │  lab          Backtests, Training (CPU-begrenzt) │
               ▼                                     │  ib-gateway   (ab Etappe 4)                      │
        ┌─────────────┐      SQL (TLS)               └───────┬───────────────┬──────────────────────────┘
        │ Postgres    │◄─────────────────────────────────────┘               │
        │ (Neon, EU)  │                                              Broker/Exchange-APIs,
        └─────────────┘   Objektspeicher: Datensatz-Snapshots,       Datenfeeds, Telegram/Pushover,
                          Modellartefakte                            externer Heartbeat-Dienst
```

### 3.1 Trennung Paper / Live / Forschung (technisch erzwungen)

1. **Eigene Prozesse:** `paper`, `live` und `lab` sind getrennte Container. Nur der Container `live` erhält die Live-Zugangsdaten als Secrets.
2. **Eigene Datenbankrollen:** Rolle `engine_paper` und `engine_lab` haben kein Schreibrecht auf Live-Orders (Row-Level-Security auf `mode = 'LIVE'`). Rolle `web` darf Befehle einfügen, aber keine Orders schreiben.
3. **Typ-Ebene:** Der Live-Adapter lässt sich nur mit einem `ActiveMandate`-Objekt konstruieren; der Simulator implementiert dieselbe Schnittstelle, kennt aber keinen Netzwerk-Client.
4. **Datenbank-Constraint:** `order.mode` muss `account.mode` entsprechen (Fremdschlüssel auf zusammengesetzten Schlüssel); ein Paper-Signal kann keine Order auf einem Live-Konto referenzieren.
5. **Unabhängigkeit:** `live` startet und arbeitet, wenn `paper` und `lab` gestoppt sind – und umgekehrt (eigene Health-Checks, keine gegenseitigen `depends_on`).

### 3.2 Web ↔ Engine

- Die Web-App ruft die Engine **nie direkt** auf; der Engine-Server hat keinen offenen Eingangsport (ausser SSH über Schlüssel). Bedienhandlungen werden als Zeile in `command` geschrieben (Typ, Ziel, Parameter, Urheber, Step-up-Nachweis); die Engine liest sie im Sekundentakt, prüft sie fachlich und quittiert mit Ergebnis.
- Die Risikoprüfung sitzt in der Engine. Auch ein Administrator-Befehl aus der Web-App ist nur ein Wunsch; ein Befehl kann Limits nicht umgehen.
- Dashboard liest den Zustand per Abfrage (Aktualisierung alle 2–5 s auf aktiven Seiten). Kein eigener Realtime-Dienst in V1.
- **Notfallbedienung ohne Vercel:** der `notifier` verarbeitet Bot-Befehle (`/pause_live`, `/notfall`, `/status`) per Long-Polling direkt auf dem Engine-Server, nur von der hinterlegten Chat-ID, nur risikosenkende Aktionen.

### 3.3 Ausfallverhalten

| Ausfall | Verhalten |
|---|---|
| Vercel nicht erreichbar | Handel läuft weiter; Bedienung über Bot-Befehle; Meldungen kommen vom Engine-Server |
| Datenbank nicht erreichbar | keine neuen Einstiege (ohne gespeicherte Absicht keine Order); Positionsbetreuung aus dem Arbeitsspeicher; eingehende Fills in lokales Journal (SQLite auf dem Server), Nachtrag bei Rückkehr; Alarm |
| Engine-Server tot | Schutzorders beim Anbieter bleiben aktiv; externer Heartbeat-Dienst und Vercel-Watchdog-Cron alarmieren unabhängig voneinander; nach Neustart WIEDERHERSTELLUNG |
| Anbieter-API gestört | Zustand WIEDERHERSTELLUNG, neue Risiken blockiert, Abgleich bei Rückkehr |
| Datenfeed veraltet | betroffene Instrumente: keine Einstiege; Exits/Schutz bleiben |

## 4. Technologieentscheide

### 4.1 Engine: Python, eine Codebasis für Backtest, Paper und Live

| Baustein | Wahl | Lizenz (V = heute geprüft) | Begründung |
|---|---|---|---|
| Sprache/Laufzeit | Python 3.12+, asyncio | – | ML-Ökosystem und Broker-Bibliotheken in einer Sprache; Strategie-Code existiert genau einmal |
| Crypto-Anbindung | `ccxt` (REST/WS) oder `python-kraken-sdk` | MIT / Apache-2.0 (V) | Entscheidung im Adapter-Spike; ccxt erleichtert spätere Exchanges, das SDK bildet Kraken-Spezifika genauer ab |
| IBKR-Anbindung | `ib_async` | BSD-2 (V); `ib_insync` ist archiviert | gepflegter Nachfolger |
| Alpaca (Daten, Test-Adapter) | `alpaca-py` | Apache-2.0 (V) | offizielles SDK |
| Börsenkalender | `exchange_calendars` | Apache-2.0 (V) | Feiertage, Halbtage, Zeitzonen |
| Daten/Analyse | Polars, DuckDB (über Parquet-Snapshots) | MIT (V) | schnell, wenig Speicher |
| Indikatoren | eigene Implementierung der ~10 benötigten Formeln, getestet gegen TA-Lib | TA-Lib BSD (V) | `pandas-ta`: Ursprungs-Repo nicht mehr erreichbar, Lizenzlage unklar (NV) → keine Abhängigkeit |
| ML | scikit-learn, LightGBM, Optuna | BSD-3 / MIT / MIT (V) | Baselines zuerst |
| Experimentprotokoll | eigene Tabellen (`experiment`, `experiment_trial`) + Artefakte im Objektspeicher | – | MLflow (Apache-2.0) wäre ein zusätzlicher Dienst ohne Mehrwert bei einem Nutzer; Gates brauchen die Daten ohnehin in Postgres |
| Jobs | Postgres-Tabelle mit `FOR UPDATE SKIP LOCKED` | – | kein Redis nötig |

**Backtest-/Simulationskern: Eigenbau vs. Integration**

| Option | Lizenz (V) | Bewertung |
|---|---|---|
| Freqtrade/FreqAI | GPL-3.0 | starker Crypto-Lernablauf, aber kein Aktien-Unterbau, eigenes Bot-/DB-Modell; unsere Anforderungen an Mandate, Episoden, getrennte Orderwege und Journal müssten darumherum gebaut werden. Als Referenz nutzen, nicht einbetten. |
| NautilusTrader | LGPL-3.0 | ereignisgetrieben, gleicher Code für Backtest/Live, IB-Adapter vorhanden; hohe Einarbeitung, eigenes Zustandsmodell, Kraken-Spot-Abdeckung nicht geprüft (NV). Ernsthafte Alternative. |
| vectorbt (OSS) | Apache-2.0 + Commons Clause | sehr schnell für Parameter-Raster, aber vektorisiert: bildet Orderzustände, Teilfüllungen und Kapitalreservierung nicht ab. Für private Nutzung lizenzseitig unkritisch; höchstens als Vorfilter. |
| backtesting.py / backtrader | AGPL-3.0 / GPL-3.0 (backtrader seit 2024 ohne Pflege) | nicht geeignet |
| **Eigener schlanker ereignisgetriebener Kern** | – | **gewählt**, mit Vorbehalt |

Begründung: Der Umfang ist klein (≈ 25 Instrumente, Kerzen ab 1 h, Long-only, vier Ordertypen). Der Kern des Produkts – identischer Order-Zustandsautomat, Risikoprüfung, Reservierung und Journal in Backtest, Paper und Live – ist genau das, was bei einer Fremdbibliothek an deren Modell angepasst werden müsste. **Vorbehalt:** vor Etappe 2 ein auf 2 Tage begrenzter Spike mit NautilusTrader (Task E2-0). Übernahme, falls es Simulator-Realismus (Teilfüllungen, Bid/Ask), beide Anbieter und unser Journal ohne Verbiegen abdeckt; sonst Eigenbau. LGPL ist für ein privat betriebenes, nicht verteiltes System unproblematisch.

### 4.2 Web

Next.js 16 · React 19 · Tailwind 4 · Drizzle · Zod · Vitest (wie die Nachbarprojekte). Auth: **Better Auth** (MIT, V) mit Passkey und TOTP als zweitem Faktor, fest auf ein Benutzerkonto beschränkt; Auth.js verweist inzwischen selbst auf Better Auth und nennt seine Passkeys «experimental» (V, authjs.dev). Step-up (erneute Passkey-Bestätigung, höchstens 5 min alt) für: Mandat aktivieren, Limits erhöhen, Positionen schliessen, Schlüssel ändern, Promotion. Charts: `lightweight-charts` (Apache-2.0, V; verlangt NOTICE-Hinweis und Link zu tradingview.com – die Option `attributionLogo` erfüllt das) und `recharts`. Nur die Bibliothek wird verwendet, **keine TradingView-Marktdaten, -Alerts oder -Webhooks**.

LLM: Anthropic API über die serverseitige Web-App, ausschliesslich für Erklärtexte und (später) Nachrichtenstrukturierung. Das Modell erhält berechnete Zahlen als Eingabe und hat **keine Werkzeuge**, die Orders, Limits oder Konfiguration verändern. Nachrichteninhalte gelten als untrusted: sie werden nur in ein festes Schema extrahiert (Zod-validiert), nie als Anweisung weitergereicht.

### 4.3 Datenbank

- **Neon Postgres (EU, Frankfurt)** wie in den Nachbarprojekten; erreichbar von Vercel und vom Engine-Server. Weil die Engine dauerhaft verbunden ist, schläft die Instanz nicht → bezahlter Plan (Kosten in Dokument 09).
- **Schema-Hoheit:** Drizzle-Migrationen im Repo (Konvention der Nachbarprojekte), aber **nicht** automatisch beim Vercel-Build, sondern als eigener Deploy-Schritt vor Engine und Web. Regeln: nur additive Änderungen (expand/contract); die Engine prüft beim Start die Schema-Version und verweigert neue Einstiege bei Abweichung. Ein CI-Test vergleicht die Python-Modelle mit dem tatsächlichen Schema.
- Kerzen in normalen, nach Zeit partitionierten Tabellen (Grössenordnung: 25 Instrumente × Minutenkerzen × 5 Jahre ≈ 15–30 Mio. Zeilen; unkritisch). TimescaleDB ist nicht nötig.
- **Forschungs-Snapshots:** unveränderliche Parquet-Dateien im Objektspeicher (Inhalt-Hash als Name); Experimente referenzieren den Hash.
- **Append-only:** `signal`, `fill`, `audit_event`, `decision` haben keine UPDATE-/DELETE-Rechte für Anwendungsrollen.
- Backups: Neon Point-in-Time-Recovery + wöchentlicher logischer Export in den Objektspeicher; Wiederherstellung wird in Etappe 4 einmal geprobt.

### 4.4 Engine-Server

Kleiner VPS in der EU (Deutschland; kein Anbieter mit Schweizer Standort in der geprüften Auswahl), Docker Compose, automatische Neustarts, feste IPv4 (für die IP-Allowlist der API-Schlüssel), Firewall nur SSH. Deployment per GitHub Action (Image bauen, per SSH ausrollen); `live` wird nur ausgerollt, wenn keine Order im Zustand ÜBERMITTELT/UNBEKANNT ist, und fährt danach durch WIEDERHERSTELLUNG. Secrets: verschlüsselt im Repo (SOPS/age) oder als Docker-Secrets auf dem Server; nie in der Web-App, in Logs oder Meldungen. Logs strukturiert, Schlüssel/Signaturen werden vor dem Schreiben maskiert (Test T-SEC).

Zeit: NTP-synchron, alle Zeitstempel UTC in der Datenbank; Anzeige Europe/Zurich; Börsenzeiten aus dem Kalender des Handelsplatzes.

## 5. Datenmodell (fachlich)

| Objekt | Zweck | Wichtige Felder / Regeln |
|---|---|---|
| **Instrument** | handelbares Objekt | interne ID; Art (Aktie, ETF, Crypto-Spot); ISIN/Anbieter-IDs; **Handelsplatz**; **Handelswährung**; bei Crypto Basis/Quote; Tick-/Lotgrösse, Mindestmenge/-betrag; Status (aktiv, delistet); Gültigkeitszeitraum, Vorgänger-/Nachfolgersymbol |
| **Universum/Watchlist** | freigegebene Menge | Instrumente, Zulassung/Ausschluss, wer wann geändert hat; Scanner arbeitet nur hierin |
| **Feed** | Datenquelle | Anbieter, Datentyp (Kerze, Quote, FX, Kalender, Ereignis), Lizenzhinweis, Status, letztes Datum, Qualitätskennzahlen |
| **Kerze / Quote / FX-Kurs** | Marktdaten | Instrument, Zeitebene, OHLCV bzw. Bid/Ask, Quelle, `verfügbar_ab`, Adjustierungsstand; FX-Kurs mit Quelle und Art (Fixing vs. Anbieterkurs) |
| **Corporate Action / Ereignis** | Splits, Dividenden, Earnings | Stichtag, Faktor/Betrag, Quelle, `bekannt_seit` |
| **Strategie** | Familie | Name, Beschreibung, geeignete Regime, Parametergrenzen |
| **Strategieversion** | unveränderliche Ausprägung | Code-Commit, Parameter, optional Modell-ID, Regimeregel-Version, Kostenmodell-Version, Lebenszyklus-Status, Gate-Ergebnisse |
| **Modell** | trainiertes Artefakt | Artefakt-Hash, Trainingsdatensatz-Hash, Trainingsfenster, Feature-Liste, Kalibrierungsstatus |
| **Experiment / Versuch** | Lernlabor-Lauf und jede getestete Variante | Hypothese, Suchraum, Budget, Datensatz-Hash, Seed, alle Varianten mit Ergebnis (auch gescheiterte), Abschlussstatus, Holdout-Zugriffe |
| **Gate-Bewertung** | Prüfergebnis | Version, Gate (G1–G5), Kriterien mit Ist/Soll, bestanden/nicht bestanden/zu wenig Evidenz, Gründe, Zeitpunkt, Gate-Konfigurationsversion |
| **Freigabe** | Entscheidung über Promotion | Version, vorgeschlagen von (System), entschieden von (ich oder Änderungsmandat), Entscheid, Zeitpunkt, ersetzt Version |
| **Konto** | Geldtopf | Modus (PAPER/LIVE), Anbieter, Basiswährung, Anbieter-Konto-ID (nur Live), Berechtigungsprüfung |
| **Lauf/Episode** | Abschnitt eines Paper-Kontos | Startkapital, Start/Ende, Simulator-Konfiguration, Anlass (neu, Reset, Spiegel-Snapshot mit Quelle); alte Episoden unveränderlich |
| **Mandat** | Handlungsvollmacht | Konto, Autonomiestufe, erlaubte Strategieversionen, Instrumente, Budget, Risikopolicy-Version, Gültigkeit, aktiviert von/wann, Zustand |
| **Risikopolicy** | versionierte Grenzen | alle Limits aus Dokument 06; Erhöhungen mit Wartezeit; Historie |
| **Autopilot** | Zustandsautomat | Konto, aktueller Zustand, Zustandsverlauf mit Auslöser |
| **Entscheidung** | Portfolio-Entscheid je Kerze | Eingangssignale, Portfoliozustand-Snapshot, Feature-Snapshot, Ergebnis (Zielposition), Verwerfungsgründe; Trainingsgrundlage |
| **Signal** | Strategieausgabe | Felder gemäss Dokument 01 (3.3); unveränderlich |
| **Orderabsicht** | wirtschaftlich eindeutige Handlung | Idempotenzschlüssel (Mandat, Signal, Rolle Einstieg/Schutz/Exit); eindeutig; Reservierung |
| **Order** | Auftrag an Simulator/Anbieter | Modus, Konto, Absicht, Client-Order-ID, Anbieter-Order-ID, Typ, Menge, Limits, Zustand + Verlauf, Rolle, verknüpfte Schutzorder |
| **Fill** | Ausführung | Order, Menge, Preis, Gebühr + Gebührenwährung, Zeit, Anbieter-Fill-ID (eindeutig), beobachtet/simuliert |
| **Position** | Bestand je Konto/Instrument | Menge, Einstandswert, Eigentümer-Strategieversion, Schutzstatus; Herkunft (verwaltet/fremd) |
| **Trade** | Rundlauf Einstieg→Ausstieg | zugehörige Fills, Brutto, Kosten, Netto in Handels- und Berichtswährung, verwendete FX-Kurse, Plan vs. Ist, Exit-Grund, Ursachenanalyse |
| **Kapitalreservierung** | gebundenes, noch nicht gefülltes Kapital und Risiko | Konto, Absicht, Betrag, geplantes Risiko, Status |
| **Portfolio-Snapshot** | Bewertung zu einem Zeitpunkt | Cash, Positionen, Eigenkapital, bereinigter Höchststand, Kapitalflüsse, FX-Kurse |
| **Abgleich** | Vergleich lokal ↔ Anbieter | Zeitpunkt, Abweichungen (Position, Order, Fill, Cash), Auflösung |
| **Alert** | Meldung | Stufe, Modus, Entdoppelungsschlüssel, Inhalt, Zustellversuche je Kanal, Bestätigung, Eskalationsstand |
| **Befehl** | Bedienhandlung | Typ, Ziel, Urheber, Step-up-Nachweis, Ergebnis |
| **Auditereignis** | lückenloses Protokoll | wer/was (Nutzer, System, Kanal), Objekt, vorher/nachher, Zeit; append-only |
