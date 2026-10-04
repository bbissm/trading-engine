# 01 · Produkt, Module und Zustandsabläufe

Stand: 4. Oktober 2026 · Arbeitstitel **TradingEngine** (keine geprüfte Marke)

## 1. Produktbeschreibung

TradingEngine ist eine persönliche Handelsplattform für **einen Nutzer mit eigenen Konten**. Sie beobachtet ein freigegebenes Universum aus Aktien, ETFs und Crypto-Spot-Paaren, berechnet nachvollziehbare Signale aus numerischen Marktdaten, handelt mit virtuellem Kapital (Paper) und – erst nach ausdrücklicher Aktivierung – mit einem begrenzten Echtgeldbudget (Live). Ein Lernlabor erzeugt und prüft neue Strategie-/Modellversionen, ohne die aktive Live-Version zu berühren.

Drei Dinge werden im ganzen Produkt getrennt geführt und getrennt abgenommen:

| Ebene | Frage | Nachweis |
|---|---|---|
| Technische Funktion | Läuft die Software korrekt? | Automatisierte Tests, Abnahmeszenarien (Dokument 07) |
| Strategiequalität | Gibt es einen belastbaren Vorteil nach Kosten? | Evidenz-Gates G1–G3 (Dokument 04) |
| Echtgeld-Freigabe | Darf diese Version mit diesem Budget handeln? | Meine Aktivierung eines Mandats (G4), Pilotüberwachung (G5) |

Kein Gate einer Ebene ersetzt ein Gate einer anderen.

### Abgrenzung

**Nicht Teil des Produkts:** Verwahrung von Geld, Handel für Dritte, Vermögensverwaltung, Anlageberatung, High-Frequency-Trading, Gewinnversprechen.

| | Erste Version (V1, Etappen 1–4) | Vollständiges Zielprodukt (Etappe 5+) |
|---|---|---|
| Märkte | ca. 20 liquide US-Aktien/ETFs, ca. 5 Crypto-Spot-Paare | weitere Börsen (SIX, XETRA), mehr Instrumente |
| Anbieter | 1 Aktienbroker, 1 Crypto-Exchange | weitere Adapter, modular |
| Richtung | Long-only, kein Hebel; SELL = Positionsabbau | Margin/Short/Derivate nur nach separater Bewertung |
| Zeitebenen | Signale auf abgeschlossenen Kerzen: Aktien/ETF 1d, Crypto 4h/1d; Überwachung im Minutentakt | zusätzliche Intraday-Ebenen, getestete Intrabar-Logik |
| Strategien | 3 regelbasierte Forschungskandidaten + ML-Setup-Bewertung | Regime-Gewichtung, optional RL (experimentell) |
| Autonomie | Stufe 1–3; Promotion neuer Versionen nur durch mich | optional automatische Promotion im Änderungsmandat |
| Benachrichtigung | In-App + 1 Messenger-Kanal + 1 Fallback-Kanal | weitere Kanäle (SMS, Anruf) |
| Ereignisdaten | Earnings-Kalender als Sperrfenster | strukturierte Nachrichten (LLM-gestützt, untrusted) |

## 2. Fachliche Module

| # | Modul | Aufgabe | Schreibt in den Live-Orderweg? |
|---|---|---|---|
| M1 | **Instrumente & Universum** | Stammdaten (Instrument-ID, Handelsplatz, Währung, Paar), Watchlists, Zulassungs-/Ausschlusslisten, Kalender, Corporate Actions | nein |
| M2 | **Marktdaten** | Feeds anbinden, Kerzen/Quotes speichern, Qualität prüfen (Lücken, Alter, Ausreisser), FX-Kurse | nein |
| M3 | **Analyse** | Features, Regime-Erkennung, Scanner innerhalb des freigegebenen Universums | nein |
| M4 | **Strategien & Signale** | versionierte Regeln/Modelle erzeugen Signale mit Begründung | nein |
| M5 | **Portfolio-Entscheid** | konsolidiert Signale mehrerer Strategien zu einer Zielposition je Konto; Eigentümerschaft von Trades | nein |
| M6 | **Risikoprüfung** (unabhängig) | prüft jede risikosteigernde Order gegen Risikopolicy und Mandat; reserviert Kapital; Verlustgrenzen | Veto-Instanz |
| M7 | **Ausführung Paper** | Simulator: Bid/Ask, Kosten, Teilfüllungen, Episoden | nein (technisch ausgeschlossen) |
| M8 | **Ausführung Live** | Anbieter-Adapter, Orderzustände, Schutzorders, Abgleich, Wiederherstellung | **ja – einziges Modul** |
| M9 | **Autopilot-Steuerung** | Zustandsautomaten Paper/Live/Signalbetrieb, Mandate, Start/Pause/Stopp/Notfall | über M8 |
| M10 | **Lernlabor** | Backtest, Walk-forward, Optimierung, ML-Training, Experimentprotokoll, Gates | nein |
| M11 | **Freigabe & Versionen** | Lebenszyklus, Statuskarten, Promotion, Rücknahme | setzt nur Zeiger «aktive Version» |
| M12 | **Benachrichtigung** | Stufen, Zustellung, Bestätigung, Eskalation, Kanalüberwachung | nein |
| M13 | **Journal & Performance** | Entscheidungs-/Tradejournal, Kennzahlen, Benchmarks, Exporte | nein |
| M14 | **Betrieb & Audit** | Verbindungen, Gesundheitsstatus, Kosten, Auditprotokoll, Secrets | nein |

Gemeinsam genutzt: M1–M4 (Marktinformationen und Strategieentscheidungen). Strikt je Modus und Konto getrennt: Konten, Kapital, Positionen, Orders, Fills, Berechtigungen, Statistiken. Jeder Datensatz in M5–M9 und M13 trägt `mode` (PAPER/LIVE/RESEARCH), `account_id` und `strategy_version_id`.

## 3. Zustandsabläufe

### 3.1 Autopilot (je einer pro Paper-Konto und pro Live-Konto, unabhängig)

```
EINGERICHTET → BEREIT → AKTIV ⇄ EINSTIEGE_PAUSIERT
                          │            │
                          ▼            ▼
                  POSITIONEN_ABWICKELN → GESTOPPT → (BEREIT)
jeder Zustand → WIEDERHERSTELLUNG → vorheriger Zustand | FEHLER
jeder Zustand → NOTFALL → (nur manuell) EINSTIEGE_PAUSIERT | GESTOPPT
```

| Zustand | Neue Einstiege | Positionsbetreuung/Schutz | Auslöser |
|---|---|---|---|
| EINGERICHTET | nein | – | Konfiguration unvollständig (fehlende Punkte werden konkret angezeigt) |
| BEREIT | nein | ja, falls Bestand existiert | alle Vorprüfungen grün; wartet auf Start |
| AKTIV | ja, im Mandat | ja | Start durch mich |
| EINSTIEGE_PAUSIERT | nein | **ja** | «Einstiege pausieren», Verlustlimit, Datenproblem, Benachrichtigungsausfall |
| POSITIONEN_ABWICKELN | nein | ja; Exits nach Strategie-Regeln | «Geordnet stoppen» |
| GESTOPPT | nein | Bestand = 0 bestätigt; sonst nicht erreichbar | Abwicklung abgeschlossen |
| WIEDERHERSTELLUNG | nein | erst Abgleich Bestand/Orders/Schutz, dann Rückkehr | Neustart, Reconnect |
| FEHLER | nein | Schutzorders beim Anbieter bleiben; Abgleich wird wiederholt | unaufgelöste Abweichung |
| NOTFALL | nein | Abgleich, sichere Schutz-/Exit-Aktionen nach Notfallpolicy, Alarm | Notfall-Schalter, kritische Regel |

Regeln:
- **GESTOPPT mit offenen Positionen gibt es nicht.** Solange Bestand existiert, bleibt die Betreuung aktiv; die UI zeigt dann «Keine neuen Einstiege – 2 Positionen werden weiter betreut».
- Paper- und Live-Autopilot besitzen getrennte Zustände, Prozesse und Schalter. Der Zustand des Lernlabors und des Paper-Autopiloten ist für Live keine Eingangsgrösse.
- Verbindungsverlust ändert keinen Positionsbestand; er führt zu WIEDERHERSTELLUNG und blockiert neue Risiken.

### 3.2 Bedienaktionen

| Schaltfläche | Neue Einstiege | Offene Einstiegsorders | Positionen | Schutzorders |
|---|---|---|---|---|
| **Einstiege pausieren** | gestoppt | ungefüllte werden storniert; bei teilgefüllten wird der Rest storniert, der gefüllte Teil betreut | bleiben, werden betreut | bleiben |
| **Geordnet stoppen** | gestoppt | wie oben | Abbau nach den Exit-Regeln der Strategie (Stop, Ziel, Zeitlimit); optional maximale Abwicklungsfrist | bleiben bis Exit |
| **Positionen jetzt schliessen** | gestoppt | storniert | marktnahe Exit-Orders; Bestätigungsdialog zeigt Bestand, geschätzte Kosten, geschlossene Märkte | werden erst nach bestätigtem Exit-Fill entfernt bzw. auf die Restmenge angepasst |
| **Notfall** | gestoppt | storniert (nur Einstiege) | nach vorab gewählter Notfallpolicy: *halten mit Schutz* (Standard) oder *schliessen* | bleiben; fehlende werden ersetzt |

«Positionen jetzt schliessen» ausserhalb der Handelszeit (Aktien) zeigt: Order wird zur nächsten Eröffnung platziert, Gap-Risiko bleibt. Verbleibende Positionen sind nach jeder Aktion sichtbar.

### 3.3 Signal

`ERZEUGT → RISIKOGEPRÜFT {ERLAUBT | BLOCKIERT | NUR_SIMULATION | MANUELLE_PRÜFUNG}` → `{IGNORIERT | ABGELAUFEN | FREIGEGEBEN}` → `ORDER_GESENDET → {TEILWEISE_AUSGEFÜHRT → AUSGEFÜHRT | ABGELEHNT}`

Signale sind unveränderlich (append-only). Spätere Erkenntnisse werden als Folgeereignisse angehängt, nie in das Signal zurückgeschrieben. Chart-Marker lesen ausschliesslich diese gespeicherten Signale, nicht neu berechnete Indikatoren.

Fachliche Aktionen und ihre Wirkung je nach Bestand:

| Aktion | Ohne Position | Mit Position |
|---|---|---|
| BUY | Einstiegsorder (falls Risiko erlaubt) | Aufstocken nur, wenn Strategie und Limits es vorsehen; sonst HOLD |
| HOLD | nichts | Position und Schutz unverändert |
| REDUCE | nichts (nie Short) | Teilverkauf, Schutzorder auf Restmenge anpassen |
| EXIT | nichts (nie Short) | vollständiger Abbau |
| NO TRADE | bewusste Nicht-Handlung mit Grund (Daten, Liquidität, Regime, fehlende Evidenz) | – |

Pflichtfelder eines Signals: Instrument, Handelsplatz, Zeit (Kerzenschluss und Erzeugungszeit), Datenquelle, Datenalter, Gültig-bis, Strategie-/Modellversion, Regime, Zeitebene, Auslöser, Gegenfaktoren, Einstieg/-bereich, Stop, Ziel, maximaler Horizont, Stückzahl, Nominal, geplantes Verlustrisiko (inkl. Kosten), geschätzte Gebühren/Spread/Slippage, Netto-Chance-Risiko, Risikoentscheid mit Grund, Ablaufstatus.

**Score statt Scheinwahrscheinlichkeit:** Standardanzeige ist «Setup-Score 0–100 (keine Gewinnwahrscheinlichkeit)». Eine Prozentwahrscheinlichkeit erscheint nur, wenn für genau diese Modellversion eine Kalibrierung auf unabhängigen Daten vorliegt (Kriterium in Dokument 04, Abschnitt 5), und dann mit Zieldefinition («Netto-Ergebnis > 0 nach 10 Handelstagen»), Horizont und Unsicherheitsband.

### 3.4 Order (Paper und Live identisches Modell)

`VORBEREITET → GEPRÜFT → ÜBERMITTELT → ANGENOMMEN → TEILWEISE_GEFÜLLT → GEFÜLLT`
Nebenpfade: `ABGELEHNT`, `ABGELAUFEN`, `STORNIERUNG_ANGEFRAGT → STORNIERT`, `UNBEKANNT`.

- `UNBEKANNT` entsteht bei Timeout/Verbindungsabbruch nach Übermittlung. Solange eine Order eines Kontos UNBEKANNT ist, sind neue risikosteigernde Orders für dieses Konto blockiert.
- `STORNIERUNG_ANGEFRAGT` akzeptiert weiterhin Fills. Die gefüllte Menge ist immer die Summe der Fill-Datensätze, nie ein überschriebenes Feld.
- Prognose, Entscheidung, Order und Fill sind vier getrennte Objekte mit eigenen Zeitstempeln.

### 3.5 Strategie-/Modellversion

`IDEE → HISTORISCH_GEPRÜFT (G1) → VALIDIERT (G2) → FORWARD_PAPER/SHADOW (G3) → FREIGABE_VORGESCHLAGEN → LIVE_FREIGEGEBEN (G4, durch mich) → LIVE_PILOT (G5) → LIVE_AKTIV`
Nebenpfade: `ABGELEHNT` (mit Grund), `ZU_WENIG_EVIDENZ`, `ZURÜCKGENOMMEN`, `STILLGELEGT`.

Eine Version ist unveränderlich (Code-Hash, Parameter, Modellartefakt-Hash, Datensatz-Hash). Jede Änderung erzeugt eine neue Version, die bei IDEE beginnt.

### 3.6 Lernlabor-Lauf

`GEPLANT → LÄUFT ⇄ PAUSIERT → ABGESCHLOSSEN {KANDIDAT | KEIN_BELASTBARER_FORTSCHRITT | ZU_WENIG_DATEN | ABGELEHNT} | ABGEBROCHEN (Budget erschöpft)`

Getrennte Schalter: **Training** (darf das Labor rechnen?) und **Promotion** (darf ein Ergebnis live aktiv werden?). Standard: Training an, Promotion nur durch mich.

### 3.7 Autonomiestufen (je Mandat)

1. **Informieren** – Signale und Meldungen, keine Orders.
2. **Vorbereiten** – Order wird vorbereitet und risikogeprüft, Freigabeanfrage mit Ablaufzeit (Standard: Schluss der nächsten Kerze der Signal-Zeitebene, höchstens die Signalgültigkeit). Keine Antwort = verfallen. Schutz-/Notfallausstiege warten nicht auf Freigabe.
3. **Mandat** – automatischer Handel innerhalb von Konto, Strategieversionen, Instrumenten, Budget und Risikogrenzen. Ausserhalb des Mandats: neue Risiken anhalten, eskalieren.

### 3.8 Mehrere Strategien, eine Position

- Je Konto und Instrument existiert **eine konsolidierte Zielposition**. Strategien liefern Wünsche, der Portfolio-Entscheid (M5) bildet daraus die Zielposition, die Ausführung handelt nur die Differenz.
- Jeder Trade hat genau **eine Eigentümer-Strategieversion**, deren Exit-/Schutzplan gilt, bis er geschlossen ist – auch nach Versionswechsel oder Rücknahme.
- Konflikte: EXIT/REDUCE des Eigentümers schlägt BUY anderer Strategien für die laufende Kerze. Ein zweites BUY einer anderen Strategie auf ein bereits gehaltenes Instrument wird in V1 verworfen (Grund «Instrument bereits durch Strategie X gehalten») statt aufgestockt.
- Bei mehr gültigen BUY-Signalen als freiem Budget entscheidet die dokumentierte Reihenfolge: Strategiepriorität (konfiguriert) → Netto-Chance-Risiko → geringere Korrelation zum Bestand. Der Rest wird als «blockiert: Budget» journalisiert.
