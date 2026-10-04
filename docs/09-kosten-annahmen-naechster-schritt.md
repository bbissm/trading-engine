# 09 · Laufende Kosten, Annahmen, offene Punkte und nächster Schritt

Preisstand: **4. Oktober 2026**. «Belegt» = am Preisstand auf der offiziellen Seite gelesen (Quellen in Dokument 03 und 05). «Schätzung» = eigene Rechnung auf Basis belegter Einheitspreise oder Annahme. Alle Preise ohne MWST. Währungen werden nicht umgerechnet, wo es nicht nötig ist; CHF-Summen sind grob gerundete Schätzungen.

## 1. Einheitspreise

### Infrastruktur
| Posten | Preis | Art |
|---|---|---|
| Vercel Pro | USD 20/Monat je Sitz inkl. USD 20 Nutzungsguthaben | belegt. Vermutlich bereits vorhanden (Annahme A-VERCEL) → Mehrkosten ≈ 0, solange die Nutzung im Guthaben bleibt |
| Vercel Hobby | kostenlos, aber «non-commercial personal use only», Cron nur 1×/Tag | belegt; ob ein privates Trading-Dashboard als kommerziell gilt, ist nicht geklärt (NV) |
| Neon Postgres, Plan Launch | USD 0.106 je CU-Stunde + USD 0.35/GB-Monat | belegt |
| → Dauerbetrieb 0.25 CU, 5 GB | ≈ USD 19 + 2 = **USD 21/Monat** | Schätzung (0.25 CU × 730 h; kleinste Grösse angenommen) |
| Engine-Server Hetzner CX23 (2 vCPU/4 GB) | EUR 5.49 + EUR 0.50 IPv4 = **EUR 5.99/Monat** | belegt; Verfügbarkeit der Tarife am Stichtag unklar (NV) |
| Engine-Server Hetzner CX33 (4 vCPU/8 GB) | EUR 8.49 + EUR 0.50 = **EUR 8.99/Monat** | belegt; empfohlen ab IB Gateway + Lernlabor |
| Alternative Fly.io shared-cpu-2x 2 GB | USD 13.39 + USD 2 IPv4 | belegt |
| Objektspeicher Cloudflare R2 | 10 GB kostenlos, danach USD 0.015/GB | belegt |
| Heartbeat (healthchecks.io) | 20 Prüfungen kostenlos | belegt |

### Benachrichtigung
| Posten | Preis | Art |
|---|---|---|
| Telegram-Bot | kostenlos | belegt |
| Pushover | USD 4.99 einmalig je Plattform; 10 000 Meldungen/Monat | belegt |
| E-Mail (Resend) | 3 000/Monat kostenlos | belegt |
| SMS Schweiz (Twilio, optional) | USD 0.0769 je Segment | belegt |

### Marktdaten
| Posten | Preis | Art |
|---|---|---|
| Alpaca Basic (IEX-Echtzeit, SIP verzögert, Historie ab 2016) | kostenlos | belegt |
| Alpaca Algo Trader Plus (SIP-Echtzeit) | USD 99/Monat | belegt |
| IBKR API-Echtzeit US: Network A + B + C | 3 × USD 1.50 = USD 4.50/Monat | belegt (Non-Professional) |
| IBKR Snapshot-Bundle + Streaming-Zusatz | USD 10 (erlassen ab USD 30 Kommission) + USD 4.50 | belegt |
| Kraken öffentliche Daten, Historien-Archive | kostenlos | belegt |
| FX (Frankfurter/EZB) | kostenlos | belegt |
| Historie mit delisteten Titeln: EODHD / Massive Developer | USD 29.99 / USD 79 je Monat, monatsweise buchbar | belegt |

### Handel
| Posten | Preis | Art |
|---|---|---|
| IBKR US-Aktien/ETF | Fixed min. USD 1.00 je Order; Tiered min. USD 0.35 + Börsengebühren | belegt |
| IBKR FX | automatische Umrechnung ≈ 0.03 %; manuell 0.20 bp, min. USD 2 | belegt |
| Kraken Spot (unterste Stufe) | 0.40 % Maker / 0.80 % Taker | belegt |

### LLM (Anthropic API, USD je Mio. Tokens Eingabe/Ausgabe)
Haiku 4.5: 1 / 5 · Sonnet 5.5: 2 / 10 · Opus 5.5: 4 / 20 – belegt. Batch −50 %, Cache-Lesen 0.1× Eingabe.
Schätzung Nutzung: Tagesbericht + Trade-Erklärungen ≈ 300 Aufrufe/Monat × (3 000 Ein- + 800 Ausgabe-Tokens) mit Sonnet 5.5 ≈ USD 1.80 + 2.40 = **≈ USD 4/Monat**. Nachrichtenstrukturierung (Etappe 5, Haiku, Batch) Schätzung USD 5–15/Monat.

### Training
Läuft auf dem Engine-Server innerhalb der Lernlabor-Budgets (Dokument 04, 5.5): keine Zusatzkosten. Die Datenmengen (Tageskerzen/4h, ≈ 25 Instrumente, flache Modelle) brauchen keine GPU.

## 2. Szenarien (monatlich, ohne Handelsgebühren)

| | A · Paper-Phase (E0–E3) | B · Live-Pilot klein | C · Ausgebaut |
|---|---|---|---|
| Vercel | 0 zusätzlich (Pro vorhanden) | 0 zusätzlich | 0–20 USD Nutzung |
| Datenbank | USD 21 | USD 21 | USD 30–45 |
| Engine-Server | EUR 6 | EUR 9 | EUR 9–20 |
| Marktdaten | 0 | USD 4.50–14.50 (IBKR-API) | USD 99 (Alpaca SIP) oder IBKR; + USD 30–79 Historie zeitweise |
| Benachrichtigung | 0 (+ USD 5 einmalig) | 0 | USD 1–5 (SMS) |
| LLM | USD 0–4 | USD 4 | USD 10–20 |
| **Summe (Schätzung)** | **≈ CHF 25–30** | **≈ CHF 35–45** | **≈ CHF 150–250** |

Sparvariante für A: Postgres im Docker auf dem Engine-Server statt Neon → ≈ CHF 6/Monat gesamt. Preis dafür: Datenbank-Port muss für Vercel erreichbar sein, Backups in Eigenverantwortung. Nicht empfohlen für die Live-Phase.

Ohne bestehenden Vercel-Pro-Plan kommen USD 20/Monat hinzu.

## 3. Handelsgebühren – entscheidend für die Strategiefrage

Reine Rechenbeispiele, keine Empfehlung.

**Aktien/ETF (IBKR Fixed, USD 1 Mindestgebühr je Order):**
| Positionsgrösse | Kosten Kauf + Verkauf | Anteil |
|---|---|---|
| USD 300 | USD 2 | 0.67 % |
| USD 1 000 | USD 2 | 0.20 % |
| USD 3 000 | USD 2 | 0.07 % |
Dazu Spread und, falls USD erst gekauft werden muss, die FX-Umrechnung. Weil per API nur **ganze Stücke** handelbar sind, lassen sich Positionsgrössen bei teuren Titeln (ein ETF-Anteil zu mehreren hundert USD) nur grob steuern. Mit 0.5 % Risiko pro Trade und einem Stop von ≈ 4 % Abstand ergibt sich eine Position von ≈ 12.5 % des Eigenkapitals; damit die Position ≥ USD 1 000 ist, braucht das Aktien-Teilbudget ≈ **USD 8 000**. Kleinere Budgets funktionieren technisch, tragen aber spürbare Gebührenlast und grobe Stückelung.

**Crypto (Kraken, unterste Stufe):**
| Ausführung | Kosten Rundlauf |
|---|---|
| Einstieg Limit (Maker) + Ausstieg Limit (Maker) | 0.80 % |
| Einstieg Limit (Maker) + Ausstieg per Stop (Taker) | 1.20 % |
| beides Taker | 1.60 % |
Bei 6 Rundläufen pro Monat à USD 800 und 1.2 % sind das ≈ USD 58/Monat, also ≈ USD 690 pro Jahr allein an Gebühren – bei einem Crypto-Teilbudget von USD 4 000 rund 17 % pro Jahr, die die Strategie erst verdienen muss. **Das ist die grösste absehbare Hürde für G1 bei Crypto.** Folgen im Plan: Crypto-Strategien werden auf 4h/1d mit weiten Zielen geprüft, Einstiege als Limit; das Kostenmodell rechnet mit den belegten Sätzen; es ist gut möglich, dass Crypto-Kandidaten deshalb `KOSTENSENSITIV` oder `NETTO_NEGATIV` enden. Die Evaluation einer zweiten Exchange mit tieferen Gebühren ist als offener Punkt O-6 geführt.

## 4. Annahmen (für den Plan getroffen, jederzeit änderbar)

| ID | Annahme |
|---|---|
| A-VERCEL | Das Vercel-Team läuft auf Pro (abgeleitet aus dem stündlichen Cron im Projekt `dashboard`). |
| A-RISK | Startwerte der Risikopolicy gemäss Dokument 06, Abschnitt 2. |
| A-LIMIT | Erhöhungen von Live-Limits wirken nach 12 h Wartezeit. |
| A-TG | Live-Freigaben (Autonomiestufe 2) nur in der Web-App; Telegram-Freigaben nur für Paper. |
| A-UNI | Start-Universum (Vorschlag, in E1-1 gegen die Anbieterlisten zu prüfen): ETFs SPY, QQQ, IWM, DIA, EFA, EEM, GLD, TLT, XLK, XLF, XLE, XLV; Aktien AAPL, MSFT, NVDA, AMZN, GOOGL, META, JPM, UNH; Crypto BTC, ETH, SOL, XRP, LINK jeweils gegen USD auf Kraken. Auswahl nach heutiger Liquidität → Survivorship-Hinweis. |
| A-TF | Aktien/ETF-Signale in V1 auf Tageskerzen; Crypto auf 4h und 1d. |
| A-CCY | Berichtswährung CHF, Tageswechsel 00:00 Europe/Zurich; Crypto in USD-Paaren. |
| A-NOTIF | Telegram als Hauptkanal, Pushover für kritische Alarme, E-Mail als Fallback. |
| A-PAPER | Paper-Startkapital je virtuellem Konto frei; Vorschlag CHF-Gegenwert des geplanten Pilotbudgets, damit Stückelung und Mindestgebühren realistisch wirken. |
| A-LANG | Oberfläche und Meldungen auf Deutsch, wie die Nachbarprojekte. |
| A-EIGENBAU | Eigener Simulationskern, vorbehältlich des NautilusTrader-Spikes (E2-0). |

## 5. Offene Punkte

Keiner dieser Punkte blockiert die Etappen 0–3 (alles ohne Echtgeld). **Blockierend für Etappe 4 bzw. den Live-Pilot** sind O-1 bis O-5.

| ID | Punkt | Blockiert | Wer |
|---|---|---|---|
| O-1 | **Pilotbudget** und Aufteilung Aktien/Crypto; bestimmt Positionsgrössen und Gebührenlast (Abschnitt 3) | Live-Pilot | ich |
| O-2 | **IBKR-Konto:** IBKR Pro, kapitalisiert (≥ USD 500 für API-Daten), zweiter Benutzername für die Engine, Paper-Konto, Non-Professional-Status. Akzeptanz der **wöchentlichen manuellen Anmeldung** | E4-5/E4-6 | ich |
| O-3 | Bei IBKR bestätigen: zuständige Gesellschaft für Schweizer Wohnsitz und ob US-ETFs für mein Konto handelbar sind. Sonst Ausweichen auf UCITS-ETFs an SIX/XETRA (anderer Kalender, andere Daten, andere Gebühren) | Universum Live | ich / IBKR-Support |
| O-4 | Datenbedingungen (Speicherung, maschinelle Auswertung, Training) von Alpaca, Kraken und IBKR lesen – in dieser Recherche bei keinem Anbieter verifizierbar | Daten-Abos; streng genommen vor produktiver Nutzung | ich |
| O-5 | Kraken: Konto für Schweizer Wohnsitz bestätigen, tatsächliche Gebührenstufe prüfen, Weg CHF → USD klären | E4-4 | ich |
| O-6 | Zweite Crypto-Exchange mit tieferen Gebühren evaluieren (nicht recherchiert) | nein | Entscheid nach ersten G1-Ergebnissen |
| O-7 | Steuerliche Einordnung: Ob Gewinne als private Kapitalgewinne oder als Einkommen aus gewerbsmässigem Wertschriftenhandel gelten, hängt von Kriterien wie Haltedauer, Umschlagshäufigkeit und Fremdfinanzierung ab (aus Fachwissen, **nicht** in dieser Recherche geprüft). Häufiger automatischer Handel kann diese Kriterien berühren. Mit Steuerberatung bzw. Steueramt klären; die App liefert nur die Datengrundlage (Exporte) | nein | ich |
| O-8 | Verfügbarkeit der Hetzner-Tarife am Bestelltag; sonst Fly.io | E0-5 | bei Umsetzung |
| O-9 | Earnings-Kalender-Quelle mit geklärten Bedingungen | Earnings-Sperrfenster | E1-6 |
| O-10 | IBKR in Paper testen: Verhalten der Bracket-Kinder bei Teilfüllung, native vs. simulierte Stops je Börse, `orderRef` für Einzelkonten, Abo-Verfall ohne TWS-Anmeldung | E4-6 | bei Umsetzung |

## 6. Nächster Implementierungsschritt (ohne Echtgeld, ohne Konto)

**Vertikaler Schnitt «Erste Kerze bis erstes Signal» – E0-1 bis E0-7 plus E1-3, E1-8, E1-9 (nur S1), E1-10 für Crypto.**

1. Monorepo `web/` + `engine/` mit CI anlegen; `CLAUDE.md` mit den Projektregeln (Modustrennung, kein Echtgeld ohne Mandat, Dokumente unter `docs/`).
2. Neon-Datenbank mit Rollen und Basisschema; Migrationsschritt.
3. Engine-Server aufsetzen; Container `guardian` schreibt Heartbeat; externer Heartbeat-Alarm.
4. Web-App mit Anmeldung und Übersicht, die den Engine-Heartbeat und die (leeren) Modus-Karten zeigt.
5. Container `marketdata`: Kraken-Historie für BTC/USD und ETH/USD aus dem öffentlichen Archiv laden, laufende 4h-/1d-Kerzen per öffentlichem WebSocket; Qualitätsstatus.
6. Features, Regime und Strategie S1 auf abgeschlossenen Kerzen; Signale append-only ins Journal.
7. Instrumentanalyse mit Kerzenchart, Regime und gespeicherten Signalmarkern.

Dafür nötig: nichts ausser den vorhandenen Vercel-/GitHub-Zugängen, einem Neon-Projekt und einem VPS. Kraken-Marktdaten sind öffentlich; es wird kein Handelskonto verbunden.

**Abnahme dieses Schritts:** Abschneidetest T13.1 und Reihenfolgetest T13.2 grün; Marker bleiben nach Neuberechnung unverändert; Heartbeat-Alarm ausgelöst und empfangen; jede Ansicht trägt den Hinweis «Signale ungeprüft – kein Qualitätsnachweis».
