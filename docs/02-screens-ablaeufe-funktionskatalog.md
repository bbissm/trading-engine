# 02 · Screens, Nutzerabläufe und Funktionskatalog

## 1. Gestaltungsgrundsätze

- **Modus ist immer sichtbar.** Jede Karte, Tabelle, Meldung und jeder Export trägt ein Modus-Etikett: `PAPER` (neutral/blau), `LIVE` (kräftige Signalfarbe, zusätzlich Text «Echtgeld»), `FORSCHUNG` (grau). Farbe ist nie der einzige Träger; das Wort steht immer dabei.
- **Live- und Paper-Beträge werden nie addiert.** Es gibt keine Gesamtsumme über Modi; Übersichten zeigen getrennte Spalten.
- **Vier Zahlenarten sind optisch getrennt:** Setup-Score · historisches (Backtest-)Ergebnis · Paper-Ergebnis · echtes Live-Ergebnis. Backtest- und Paper-Zahlen tragen den Hinweis «simuliert».
- **Begriffe für Ausführung sind eindeutig:** «Order vorbereitet», «Order gesendet», «vom Anbieter angenommen», «teilweise ausgeführt (3 von 10)», «ausgeführt». «Gekauft/verkauft» wird nur bei bestätigtem Fill verwendet.
- **Keine künstliche Aktivität:** leere Zustände sagen offen «Kein gültiges Setup» oder «Zu wenig Evidenz»; keine Konfetti-, Streak- oder Gewinnanimationen.
- **Mobil zuerst für die Übersicht, das Signal-Center und Notfallaktionen;** dichte Tabellen (Orders, Experimente) sind auf dem Desktop vollständig, mobil als Kartenliste.
- **Passung zu ContentEngine/CommerceEngine/Control Center:** Die drei Repos im Nachbarordner wurden gelesen. Übernommen werden Next.js 16 + Tailwind 4, die Mobile-Shell mit unterer Tab-Leiste und «Mehr», der Theme-Umschalter, die schlanken eigenen UI-Bausteine und die Status-Farbtokens des `dashboard` (Details in Dokument 05, Abschnitt 2). Untere Tabs: **Übersicht · Signale · Autopilot · Portfolio · Mehr**; hinter «Mehr»: Scanner, Paper-Lab, Strategien/Lernlabor, Journal, Verbindungen/Betrieb.

## 2. Screens

| # | Screen | Beantwortet | Kerninhalte |
|---|---|---|---|
| 1 | **Übersicht** | Was macht der Bot? Was ist offen? Wie viel echtes Geld ist exponiert? Was braucht mich? | Zeile «Braucht Aufmerksamkeit» (kritische Meldungen, offene Freigaben, Abweichungen); getrennte Statuskarten Paper / Live / Lernlabor / Signalbetrieb mit Zustand und Schaltern; je Modus: Eigenkapital, Cash, investiert, reserviert, offenes Stop-Risiko, Tages-/Wochenergebnis, Drawdown mit Abstand zum Limit; Dienstgesundheit (Daten, Anbieter, Worker, Benachrichtigung) |
| 2 | **Marktscanner** | Wo gibt es Kandidaten im freigegebenen Universum? | Watchlists, Regime je Instrument, Liquidität/Spread, Datenqualität, Setup-Kandidaten, Filter; Hinweis, wenn Instrumente ausserhalb des Universums wären – ohne sie hinzuzufügen |
| 3 | **Instrumentanalyse** | Warum dieses Signal? | Kerzenchart mit Zeitebenen, wenige Indikatoren, gespeicherte Signalmarker, echte Fills (Paper/Live unterscheidbar), Stops/Ziele, Strategieversion, Earnings/Corporate Actions, Begründungstext mit Auslösern und Gegenfaktoren |
| 4 | **Signal-Center** | Welche Signale sind aktiv, was wurde aus ihnen? | Reiter Aktiv / Freigabe nötig / Blockiert / Abgelaufen / Umgesetzt; Freigabe-Schaltflächen mit Ablaufzähler (Stufe 2); Blockiergrund aus der Risikoprüfung |
| 5 | **Autopilot** | Wer darf was, und wie halte ich ihn an? | je Autopilot: Zustand, Mandat, Limits mit Auslastung, Start / Einstiege pausieren / Geordnet stoppen / Positionen jetzt schliessen / Notfall; Live-Aktionen mit erneuter Anmeldung (Step-up) |
| 6 | **Paper-Lab** | Wie schlagen sich Strategien virtuell? | virtuelle Konten, Episoden (Reset = neue Episode), Vergleich von Strategien/Versionen, Simulator-Einstellungen, **Realismus-Karte** (welche Komponenten simuliert, beobachtet, fehlend) |
| 7 | **Strategien & Lernlabor** | Was wurde gelernt, vorgeschlagen, freigegeben? | Versionenliste mit Statuskarte (Gates G1–G5, bestanden/nicht bestanden mit Grund), Experimentprotokoll inkl. gescheiterter Varianten, Budgets (Rechenzeit, Anzahl Experimente), drei getrennte Spalten: «automatisch gelernt» · «zur Freigabe vorgeschlagen» · «live freigegeben» |
| 8 | **Portfolio & Orders** | Was halte ich wirklich? | Anbieterbestand vs. lokaler Bestand, offene Orders, Teilfüllungen, Schutzorder je Position (vorhanden/fehlt/Menge passt), fremde Positionen (nicht verwaltet), letzter Abgleich |
| 9 | **Journal & Performance** | Warum hat er das gemacht, und was kam heraus? | Trade-Detail (Entscheidungskette, Plan vs. Ausführung, Kosten, Ursachenanalyse), Kennzahlen mit Unsicherheit, Benchmarks, Exporte |
| 10 | **Verbindungen & Betrieb** | Funktioniert alles? Was kostet es? | Konto-/Feedstatus, Berechtigungen der API-Schlüssel (insb. «keine Auszahlung» bestätigt), unterstützte/nicht unterstützte Funktionen je Anbieter, Kostenübersicht, Kanäle + Testalarm, letzte erfolgreiche Prüfungen, Auditprotokoll |

## 3. Nutzerabläufe

### 3.1 Onboarding ohne Einzahlung (Ablauf A)
1. Anmelden (Passkey/2FA). 2. Berichtswährung CHF, Zeitzone Europe/Zurich bestätigen. 3. Watchlist aus Vorschlag (liquide ETFs/Aktien, Crypto-Paare) anpassen. 4. Virtuelles Konto anlegen (Startkapital, Währung). 5. Strategie(n) und Risikoprofil wählen (Standardwerte vorbelegt). 6. Benachrichtigungskanal verbinden und **Testalarm bestätigen**. 7. Paper-Autopilot starten. → Live-Karte zeigt «Nicht eingerichtet – nicht erforderlich».

### 3.2 Live-Assistent (erst ab Etappe 4)
Checkliste mit konkretem Status je Punkt: Anbieterkonto verbunden · Schlüsselrechte geprüft (Handel ja, Auszahlung nein) · Schutzpolicy vom Anbieter unterstützt · Abgleich fehlerfrei · Benachrichtigung + Fallback getestet (≤ 7 Tage alt) · Risikopolicy gesetzt · Strategieversion hat G1–G3 bestanden · Budget festgelegt · Notfallpolicy gewählt. Erst wenn alles grün ist, erscheint «Mandat aktivieren» (Step-up-Anmeldung, Zusammenfassung, Eingabe des Budgets zur Bestätigung).

### 3.3 Freigabe einer neuen Version (Ablauf C)
Lernlabor schliesst Lauf ab → Statuskarte zeigt Gates → bei Bestehen Meldung «Freigabevorschlag» mit Vergleich gegen Champion → ich entscheide: ablehnen · weiter beobachten · als Shadow laufen lassen · für Live freigeben (wirkt nur auf neue Entscheidungen; offene Trades behalten ihren Plan).

### 3.4 Limit ändern
Senken wirkt sofort. **Erhöhen** eines Live-Limits oder Budgets verlangt Step-up-Anmeldung und wird nach einer Wartezeit wirksam (Annahme A-LIMIT: 12 h, einstellbar ≥ 0), mit Meldung bei Anlage und bei Wirksamkeit. Schutz gegen impulsives Erhöhen nach Verlusten und gegen kompromittierte Sitzungen.

### 3.5 Kritischer Alarm
Push mit Modus, Sachverhalt, bereits erfolgter Systemreaktion und Handlungsoptionen («Bestätigen», «Einstiege pausieren», «Notfall») → Wiederholung bis Bestätigung → Fallback-Kanal. Bestätigen quittiert nur den Alarm.

## 4. Funktionskatalog

Priorität: **M** = Must für V1 · **S** = Should (V1, falls Aufwand passt) · **L** = Later. Abhängigkeiten verweisen auf Epics in Dokument 08.

| Bereich | Funktion | Prio | Abhängig von |
|---|---|---|---|
| Universum | Instrumentstamm mit ID, Handelsplatz, Währung; Watchlist, Zulassungs-/Ausschlussliste | M | E0 |
| Universum | Liquiditäts-/Spread-/Volumenfilter | M | E1 |
| Universum | Börsenkalender, Feiertage, Sommerzeit | M | E1 |
| Universum | Splits/Dividenden, Symboländerungen, Delistings | M (Splits/Dividenden) · S (Rest) | E1 |
| Daten | Historische + laufende Kerzen Aktien/ETF und Crypto, Datenqualitätsprüfung | M | E1 |
| Daten | Bid/Ask-Quotes für Simulation und Spread-Filter | M | E1 |
| Daten | FX-Kurse für CHF-Berichte, sichtbar je Bewertung | M | E1 |
| Daten | Earnings-Kalender als Sperrfenster | S | E1 |
| Daten | Nachrichten strukturiert (LLM, untrusted) | L | E5 |
| Analyse | Features, Regime-Erkennung, Multi-Zeitebene | M | E1 |
| Analyse | Scanner im freigegebenen Universum | M | E1 |
| Signale | 3 Startstrategien, versioniert; Signal mit vollständiger Begründung | M | E1 |
| Signale | Append-only-Signaljournal, stabile Chart-Marker | M | E1 |
| Signale | Kalibrierte Wahrscheinlichkeit (nur bei Nachweis) | L | E3 |
| Portfolio | Konsolidierte Zielposition, Eigentümerschaft, Prioritäten | M | E2 |
| Risiko | Unabhängige Risikoprüfung mit Kapitalreservierung | M | E2 |
| Risiko | Tages-/Wochenverlust, Drawdown, Konzentration, Frequenz, Cooldown | M | E2 |
| Risiko | Korrelationsgruppen, Sektorgrenzen | S | E2 |
| Simulation | Backtest (ereignisgetrieben, gleiche Strategie-/Risiko-Logik wie Live) | M | E2 |
| Simulation | Paper-Autopilot, virtuelle Konten, Episoden, Realismus-Karte | M | E2 |
| Simulation | Shadow-Vergleich Kandidat vs. Champion | M | E3 |
| Steuerung | Zustandsautomaten, vier Stopp-Aktionen, Notfall | M | E2 |
| Benachrichtigung | In-App + Messenger, Stufen, Entdoppelung, Bestätigung, Eskalation, Fallback, Ruhezeiten, Testalarm, Kanalüberwachung | M | E2 |
| Lernen | Experimentprotokoll, begrenzte Parameteroptimierung, Walk-forward | M | E3 |
| Lernen | Überwachtes Setup-Modell (Meta-Labeling) | M | E3 |
| Lernen | Regime-Gewichtung freigegebener Strategien | S | E3 |
| Lernen | Reinforcement Learning | L | E5 |
| Freigabe | Statuskarten, Gates, Promotion durch mich, Rücknahme | M | E3 |
| Freigabe | Automatische Promotion im Änderungsmandat | L | E5 |
| Live | Adapter Crypto-Exchange und Aktienbroker, Orderzustände, Schutzorders, Abgleich, Wiederherstellung | M | E4 |
| Live | Erkennung fremder Trades, Unsupported-Features sichtbar | M | E4 |
| Live | Mandate, Autonomiestufen 1–3 | M | E4 |
| Journal | Trade-Detail, Kennzahlen, Benchmarks, Ursachenanalyse | M | E2 |
| Journal | LLM-Erklärtexte aus berechneten Zahlen | S | E3 |
| Journal | Exporte (CSV) Orders/Fills/Gebühren/FX, Jahresübersicht | M | E2 (Paper) · E4 (Live) |
| Betrieb | Auditprotokoll, Gesundheitsstatus, externe Heartbeat-Überwachung, Kostenübersicht | M | E0/E2 |
| Betrieb | Notfallbedienung ohne Web-App (Messenger-Befehle, nur risikosenkend) | S | E4 |
| Erweiterung | Weitere Anbieter/Börsen, Margin/Short/Derivate | L | E5 |
