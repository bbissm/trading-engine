# 08 · Umsetzungsetappen und Backlog

## 1. Etappen

Aufwand in **Arbeitstagen fokussierter Entwicklung** (eine Person mit KI-Unterstützung), als Spanne. Kalenderzeit ist zusätzlich durch Beobachtungsdauern bestimmt, die sich nicht beschleunigen lassen.

| Etappe | Inhalt | Aufwand | Abnahme Software | Abnahme Strategie/Betrieb |
|---|---|---|---|---|
| **E0 Fundament** | Repo, Web-Gerüst, Engine-Gerüst, DB, Auth, Deploy, Heartbeat | 4–7 | Login, Deploy-Pipeline, Heartbeat sichtbar | – |
| **E1 Daten & Analyse** | Marktdaten Aktien/ETF + Crypto, Qualität, Watchlists, Features, Regime, 3 Strategien als Signalgeber, Charts, Signaljournal | 10–16 | T13 (1, 2, 4), T22 | keine – Signale sind ausdrücklich ungeprüft |
| **E2 Simulation & Benachrichtigung** | Kostenmodell, Backtest, Paper-Autopilot, Episoden, Risikoprüfung, Zustandssteuerung, Alarme, Journal/Performance, Exporte | 15–25 | T1, T4–T10 (gegen Simulator), T12, T14, T15, T18–T21, T23 | Paper läuft; noch **kein** Qualitätsnachweis |
| **E3 Lernlabor** | Experimentprotokoll, Walk-forward, Optimierung, Meta-Labeling, Gates, Statuskarten, Shadow, Champion/Challenger | 12–20 | T11, T13 (3, 5), T17, T28, T29 | erste G1/G2-Ergebnisse – inklusive möglicher Ablehnung aller Kandidaten |
| **E4 Echte Ausführung** | Adapter-Vertrag + Fake-Anbieter, Kraken-Adapter, Aktien-Testintegration, IBKR-Adapter, Schutz, Abgleich, Wiederherstellung, Live-Assistent, Mandate | 20–35 | T2, T3, T5, T7, T8, T16, T24–T26 gegen Fake **und** Anbieter-Test-/Validierungsmodus | G3 je Version; G4 = meine Aktivierung; danach Live-Pilot G5 |
| **E5 Erweiterte Autonomie** | weitere Anbieter/Börsen, Korrelations-/Sektorregeln, Nachrichten, Änderungsmandat, RL-Experiment | offen, je Paket 5–15 | je Paket | je Paket |

Summe E0–E4: **ca. 60–100 Arbeitstage**. Früheste realistische Kalenderzeit bis zu einem Live-Pilot: **5–8 Monate**, weil G3 mindestens 8 Wochen Forward-Paper verlangt und erst nach E3 sinnvoll beginnt. E4 kann parallel zur G3-Beobachtung gebaut werden.

Abhängigkeiten: E0 → E1 → E2 → E3; E4 braucht E2 (Order-/Risikomodell) und kann ab dort parallel zu E3 laufen; Live-Pilot braucht E3 (Gates) **und** E4.

Jede Etappe liefert vertikal nutzbare Funktion: nach E1 ein Analyse- und Signal-Dashboard, nach E2 ein vollständiger Paper-Bot mit Alarmen, nach E3 belegte oder widerlegte Kandidaten, nach E4 Echtgeldfähigkeit.

## 2. Backlog

Priorität P1 (ohne geht die Etappe nicht) · P2 (soll) · P3 (kann warten). «Abh.» = Abhängigkeit. Abnahme verweist, wo möglich, auf Tests in Dokument 07. Es werden keine Tickets in externen Systemen angelegt.

### Epic E0 – Fundament

| ID | Task | Ergebnis | Prio | Abh. | Abnahme |
|---|---|---|---|---|---|
| E0-1 | Monorepo anlegen: `web/` (Next.js 16, Tailwind 4, Drizzle, Vitest), `engine/` (Python, uv, pytest, ruff, mypy), `docs/`, `CLAUDE.md`, CI | lauffähige Gerüste, CI grün | P1 | – | `typecheck`, `lint`, `test`, `build` laufen in CI für beide Teile |
| E0-2 | Datenbank: Neon-Projekt (EU), Rollen `web`, `engine_paper`, `engine_live`, `engine_lab`, Migrationsschritt als eigener Deploy-Job, Schema-Versionstabelle | Basisschema: Instrument, Konto, Befehl, Audit, Heartbeat | P1 | E0-1 | Rollenrechte per Test geprüft; Engine verweigert Start bei falscher Schema-Version |
| E0-3 | UI-Grundgerüst nach Vorbild `commerce-engine`/`dashboard`: Mobile-Shell mit Tabs, Theme-Umschalter, Tokens inkl. Modus-Farben, Modus-Etikett-Komponente | navigierbare leere Screens | P1 | E0-1 | Screens 1–10 erreichbar; Modus-Etikett in Storybook-artiger Testseite; mobil ohne horizontales Scrollen |
| E0-4 | Auth: Better Auth, ein Benutzer, Passkey + TOTP, Step-up-Mechanismus | geschützte App | P1 | E0-1 | unangemeldet kein Zugriff auf Seiten/API; Step-up läuft nach 5 min ab |
| E0-5 | Engine-Server: VPS, Docker Compose, Deploy-Action, Secrets-Verfahren, Firewall, NTP | Container `guardian` läuft und schreibt Heartbeat | P1 | E0-2 | Heartbeat in Übersicht sichtbar; Neustart des Servers stellt Dienste selbst wieder her |
| E0-6 | Externe Überwachung: Heartbeat-Dienst + Vercel-Watchdog-Cron | Alarm bei totem Server | P1 | E0-5 | Container stoppen → Alarm innert 5 min auf zwei unabhängigen Wegen |
| E0-7 | Befehlskanal Web → Engine mit Quittung und Audit | Tabelle `command`, Verarbeitungsschleife | P1 | E0-2 | Befehl «Ping» wird quittiert; ungültiger Befehl abgelehnt und auditiert |
| E0-8 | TradingEngine im Control Center (`dashboard/src/config/projects.ts`) eintragen | Kosten/Status dort sichtbar | P3 | E0-1 | Projekt erscheint im Dashboard |

### Epic E1 – Daten und Analyse

| ID | Task | Ergebnis | Prio | Abh. | Abnahme |
|---|---|---|---|---|---|
| E1-1 | Instrumentstamm + Start-Universum (ca. 20 US-Aktien/ETFs, ca. 5 Crypto-Paare), Watchlist/Zulassung/Ausschluss mit Audit | pflegbares Universum | P1 | E0 | Scanner liefert nie ein Instrument ausserhalb der Zulassung (Test) |
| E1-2 | Datenadapter Aktien/ETF: Historie (Minuten-/Tageskerzen), laufende Kerzen, Quotes | Kerzen in DB, Quelle + `verfügbar_ab` | P1 | E1-1 | 5+ Jahre Historie geladen; Stichprobenvergleich gegen zweite Quelle |
| E1-3 | Datenadapter Crypto: Historie aus Anbieter-Dateien + laufende Kerzen/Quotes per WebSocket; Lücken (Kerzen ohne Trades) korrekt gefüllt | Kerzen in DB | P1 | E1-1 | lückenlose Reihe; Reconnect-Test |
| E1-4 | Datenqualität: Lücken, Alter, Ausreisser, Zeitstempel, Vergleich Stream vs. Nachlade-Historie; Status je Instrument | Qualitätsstatus in Scanner und Betrieb | P1 | E1-2/3 | künstliche Lücke/veralteter Feed wird erkannt und sperrt Signale |
| E1-5 | Börsenkalender, Sitzungen, Sommerzeit; Crypto-Wartungsstatus | Handelbarkeitsauskunft | P1 | E1-1 | T22 |
| E1-6 | Corporate Actions (Splits, Dividenden) punkt-in-Zeit; Earnings-Kalender | adjustierte Reihen ohne Zukunftswissen | P1 (Splits/Div.) · P2 (Earnings) | E1-2 | T13.4 |
| E1-7 | FX-Kurse und CHF-Bewertung mit sichtbarer Quelle | Bewertungsdienst | P1 | E0 | Bewertung zeigt Kurs, Quelle, Zeit |
| E1-8 | Feature- und Regime-Berechnung, versioniert, auf abgeschlossenen Kerzen | Feature-Snapshots | P1 | E1-2/3 | Indikatoren gegen Referenzwerte; T13.1 |
| E1-9 | Strategien S1–S3 als versionierte Signalgeber inkl. Begründung, Gegenfaktoren, NO-TRADE-Gründe | Signale im Journal | P1 | E1-8 | jedes Signal enthält alle Pflichtfelder; T13.2 |
| E1-10 | Scanner-Screen und Instrumentanalyse mit Kerzenchart, gespeicherten Markern, Regime, Datenqualität | Screens 2 und 3 | P1 | E1-9, E0-3 | Marker bleiben nach Neuberechnung unverändert (T13.5) |
| E1-11 | Signal-Center (nur Anzeige) + Signalbetrieb (Autonomiestufe 1) | Screen 4 | P1 | E1-9 | aktive/abgelaufene/blockierte Signale korrekt |

### Epic E2 – Simulation, Risiko, Steuerung, Benachrichtigung

| ID | Task | Ergebnis | Prio | Abh. | Abnahme |
|---|---|---|---|---|---|
| E2-0 | Spike (max. 2 Tage): NautilusTrader gegen Eigenbau-Kriterien | dokumentierter Entscheid | P1 | E1 | Entscheidnotiz in `docs/` |
| E2-1 | Domänenkern: Orderabsicht, Order-Zustandsautomat, Fill, Position, Trade, Reservierung – modusneutral | getestete Kernbibliothek | P1 | E2-0 | T4, T7 (gegen Simulator), T18 |
| E2-2 | Kostenmodell je Anbieter (Gebühren, Spread, Slippage), versioniert | Kostenschätzung an jedem Signal | P1 | E2-1 | T12 |
| E2-3 | Simulator: Bid/Ask, Latenz, Teilfüllung, Ablehnung, Ablauf, Mindestmengen/Präzision, Handelszeiten, Intrabar-Regel, Realismus-Karte | Paper-/Backtest-Ausführung | P1 | E2-1/2 | T21 |
| E2-4 | Backtest-Runner (ereignisgetrieben, gleiche Strategie-/Risiko-Logik), Baselines, Sensitivitäten | reproduzierbare Backtests | P1 | E2-3 | T13.5; Baselines im Bericht |
| E2-5 | Risikoprüfung + Risikopolicy + Guardian (laufende Verlustgrenzen) | unabhängige Prüfinstanz | P1 | E2-1 | T6, T8, T9, T19, T20 |
| E2-6 | Portfolio-Entscheid: konsolidierte Zielposition, Eigentümerschaft, Prioritäten | eine Position je Instrument | P1 | E2-5 | Konflikttests (zwei Strategien, ein Instrument) |
| E2-7 | Autopilot-Zustandsautomat + vier Stopp-Aktionen + Notfall; Screen 5 | Steuerung | P1 | E2-5 | T10 |
| E2-8 | Virtuelle Konten, Episoden, Reset, Paper-Lab-Screen | Screen 6 | P1 | E2-3 | T1, T14 |
| E2-9 | Benachrichtigung: Stufen, Vorlagen, Entdoppelung, Telegram, Pushover, E-Mail, Bestätigung, Eskalation, Ruhezeiten, Testalarm, Kanalüberwachung | Meldungswesen | P1 | E0-5 | T15 |
| E2-10 | Journal & Performance: Trade-Detail, Kennzahlen mit Unsicherheit, Benchmarks, CHF-Bewertung | Screen 9 | P1 | E2-1 | T12, T23 |
| E2-11 | Übersicht (Screen 1) mit getrennten Modus-Karten und «Braucht Aufmerksamkeit» | Startseite | P1 | E2-7/9/10 | T23; mobile Abnahme |
| E2-12 | Exporte CSV (Orders, Fills, Gebühren, FX, Jahresübersicht) | Exportfunktion | P2 | E2-10 | T30 |
| E2-13 | Onboarding-Ablauf | geführter Start | P2 | E2-8/9 | T1 als Durchklicktest |

### Epic E3 – Lernlabor und Freigabe

| ID | Task | Ergebnis | Prio | Abh. | Abnahme |
|---|---|---|---|---|---|
| E3-1 | Datensatz-Snapshots (Parquet, Hash), Holdout einfrieren, Zugriffsprotokoll | reproduzierbare Datenstände | P1 | E1 | T28 (Holdout) |
| E3-2 | Experimentprotokoll, Budgets, Abschlussstatus; Job-Ausführung im Container `lab` mit CPU-Grenze | Lernlabor-Lauf | P1 | E2-4 | T29 |
| E3-3 | Walk-forward mit Purging/Embargo; Cluster-Bildung; Block-Bootstrap | Validierungsbibliothek | P1 | E3-1 | T13.3; Tests mit synthetischen Daten (bekannter Effekt / kein Effekt) |
| E3-4 | Parameteroptimierung in Grenzen; Plateau-Test; DSR und PBO | bewertete Varianten | P1 | E3-3 | T28; auf reinem Zufallssignal besteht kein Kandidat G1 |
| E3-5 | Gate-Engine G1–G3 + Statuskarten + Ablehnungsgründe; Screen 7 | sichtbarer Lebenszyklus | P1 | E3-4 | T11, T17 |
| E3-6 | Meta-Labeling-Modell (logistische Regression, dann LightGBM), Kalibrierungsprüfung, Modellregister | Modellkandidaten | P1 | E3-3 | T13.3; Prozentanzeige nur bei erfülltem Kriterium |
| E3-7 | Shadow-Lauf und Champion/Challenger-Vergleich (gepaart) | Vergleichsansicht | P1 | E3-5, E2-8 | Ablauf C als Test |
| E3-8 | Freigabeablauf mit Step-up, Rücknahme, Eigentümerschaft offener Trades | Promotion | P1 | E3-5 | T11; Rücknahme-Test mit offenem Trade |
| E3-9 | Drift-/Kalibrierungs-/Ausführungsüberwachung mit Reaktionspolicy | Überwachung | P2 | E3-6 | simulierte Drift löst Warnung/Reduktion aus |
| E3-10 | LLM-Erklärtexte für Trades/Berichte aus berechneten Zahlen | lesbare Analysen | P2 | E2-10 | T27; Zahlen im Text stimmen mit Journal überein (automatischer Abgleich) |
| E3-11 | Regime-Gewichtung | Zuteilungslogik | P3 | E3-5 | eigene G1-Bewertung |

### Epic E4 – Echte Ausführung

| ID | Task | Ergebnis | Prio | Abh. | Abnahme |
|---|---|---|---|---|---|
| E4-1 | Adapter-Vertrag (Fähigkeiten, Orders, Status, Fills, Abgleich) + skriptbarer Fake-Anbieter | Testbasis | P1 | E2-1 | Vertragstests laufen gegen Fake |
| E4-2 | Live-Prozess: Idempotenz, UNBEKANNT-Pfad, Abgleich, Wiederherstellung, lokales Journal | Ausführungskern | P1 | E4-1 | T2–T5, T7, T8, T25, T26 |
| E4-3 | Schutzorder-Verwaltung inkl. synthetischem Ziel, Trailing durch Stop-Änderung, Reparatur | Schutzlogik | P1 | E4-2 | T7.4, T16.3, Schutz-Reparatur-Test |
| E4-4 | Kraken-Adapter (REST + WebSocket), Fähigkeitsmatrix, Wartungsmodi | Crypto-Live-Fähigkeit | P1 | E4-2/3 | Vertragstests mit `validate`-Aufrufen; danach manuelle Kleinstorders (Mindestgrösse) nach Testprotokoll |
| E4-5 | Aktien-Testintegration gegen Anbieter-Paper-Konto | Adapter gegen echte API geprüft | P1 | E4-2/3 | Vertragstests gegen Paper-API |
| E4-6 | IBKR-Adapter mit Gateway-Betrieb, Sitzungsüberwachung, Wiederanmeldung | Aktien-Live-Fähigkeit | P1 | E4-5 | Vertragstests gegen IBKR-Paper; Verhalten bei Gateway-Neustart und abgelaufener Sitzung |
| E4-7 | Fremde Trades, manuelle Eingriffe, Zuordnung | Abgleich-UI (Screen 8) | P1 | E4-2 | T16 |
| E4-8 | Mandate, Autonomiestufen 2/3, Freigaben mit Ablauf | Mandatsverwaltung | P1 | E2-7 | Ablauftest; Schweigen = verfallen |
| E4-9 | Live-Assistent + Verbindungen/Betrieb (Screen 10), Schlüsselrechte-Prüfung | geführte Aktivierung | P1 | E4-4/6 | fehlende Voraussetzung blockiert «Mandat aktivieren» |
| E4-10 | Notfallbefehle per Bot (nur risikosenkend) | Bedienung ohne Web-App | P2 | E2-9 | Befehl von fremder Chat-ID wird ignoriert und auditiert |
| E4-11 | Sicherheitsprüfung: Secrets, Rollen, Logs, Backup-Wiederherstellung geprobt | Betriebsfreigabe | P1 | E4-2 | T24; Restore-Probe dokumentiert |
| E4-12 | **Live-Pilot** – erst nach meiner Aktivierung | G5-Beobachtung | – | G1–G3, E4-9 | Kriterien G5 |

### Epic E5 – Erweiterungen (Later)
Weitere Börsen (SIX, XETRA) · zweite Exchange · Korrelationsgruppen/Sektoren · strukturierte Nachrichten · Änderungsmandat für automatische Promotion (kann nie Budget, Instrumente oder harte Risikogrenzen erweitern) · RL-Experiment im Lernlabor · SMS/Anruf-Eskalation.
