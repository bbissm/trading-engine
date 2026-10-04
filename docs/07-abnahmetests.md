# 07 · Abnahmetests

Drei getrennte Abnahmen: **(A) Software** – die Tests in diesem Dokument; **(B) Strategiequalität** – Gates G1–G3 (Dokument 04); **(C) Live-Betrieb** – Live-Assistent, G4/G5. Bestehen von (A) sagt nichts über (B) oder (C).

Testmittel:
- **Fake-Anbieter:** ein steuerbarer Adapter-Doppelgänger mit Skript für Antworten (Verzögerung, Timeout, Teilfüllung, Ablehnung, verspätete Fills, fremde Bewegungen). Alle Ausführungstests laufen deterministisch gegen ihn; ausgewählte zusätzlich gegen Anbieter-Paper-/Validierungsmodi.
- **Uhr und Marktdaten injizierbar** (Replay aufgezeichneter Daten).
- Datenbank in Tests: echtes Postgres (Engine) bzw. PGlite (Web), wie in den Nachbarprojekten.
- Jeder Test ist automatisiert, sofern nicht als «manuell» markiert.

## T1 – Paper startet ohne Einzahlung und ohne Echtgeldzugang
**Gegeben** eine frische Installation ohne Live-Zugangsdaten, Container `live` nicht gestartet. **Wenn** ich das Onboarding durchlaufe und den Paper-Autopiloten starte. **Dann** Zustand AKTIV; nach der nächsten abgeschlossenen Kerze existiert eine Entscheidung (Signal oder NO TRADE) im Journal; die Live-Karte zeigt «Nicht eingerichtet»; kein Aufruf an einen Handels-Endpunkt eines Anbieters (Netzwerk-Mitschnitt enthält nur Marktdaten-Endpunkte).

## T2 – Unabhängigkeit von Paper, Live und Training
**Gegeben** Live AKTIV mit offener Position und Schutzorder (Fake-Anbieter), Paper AKTIV, Lernlabor LÄUFT. **Wenn** (a) Paper pausiert, (b) der Paper-Container gestoppt, (c) das Lernlabor pausiert und sein Container gestoppt wird. **Dann** bewegt der Live-Autopilot bei einem Kursanstieg den Trailing-Stop und führt bei Stop-Auslösung den Exit korrekt aus; Live-Zustand bleibt AKTIV; kein Live-Datensatz referenziert Paper-/Lab-Objekte. Umgekehrt: Live gestoppt → Paper handelt weiter.

## T3 – Kein Paper-/Forschungsereignis erreicht den Live-Orderweg
1. *Strukturtest:* Der Paper- und der Lab-Container enthalten keine Live-Secrets (Prüfung der Compose-Konfiguration) und ihre Datenbankrolle erhält beim Versuch, eine Order mit `mode = LIVE` zu schreiben, einen Rechtefehler.
2. *Constraint-Test:* Einfügen einer Order, deren Signal/Episode zum Modus PAPER gehört, auf ein Live-Konto scheitert am Datenbank-Constraint.
3. *Typtest:* Der Live-Adapter lässt sich ohne aktives Mandat nicht instanziieren.
4. *Fuzz:* 10 000 zufällige Paper-/Lab-Ereignisse gegen ein System mit aktivem Live-Fake-Anbieter → Zähler «Live-Orders aus Nicht-Live-Quelle» = 0.

## T4 – Doppelte Signale und Neustart erzeugen keine zweite Order
**Wenn** dasselbe Signal-Event dreimal zugestellt wird, zwei Live-Prozesse es gleichzeitig verarbeiten und der Prozess unmittelbar nach dem Speichern der Absicht bzw. nach dem Senden abstürzt und neu startet. **Dann** existiert genau eine Orderabsicht und beim Fake-Anbieter genau eine Order; die Reservierung besteht einmal.

## T5 – Timeout nach Orderübermittlung
**Gegeben** der Fake-Anbieter nimmt die Order an, die Antwort geht verloren. **Dann** Zustand UNBEKANNT; kein zweiter Sendeversuch; Statusabfrage über die Client-Order-ID findet die Order; Zustand wird ANGENOMMEN/GEFÜLLT; währenddessen sind weitere Einstiege des Kontos blockiert. Variante: Order kam nie an → nach zwei übereinstimmenden Negativabfragen wird die Absicht als «nicht platziert» geschlossen, Reservierung frei, **kein** automatischer Neuversuch.

## T6 – Parallele Einstiege respektieren dasselbe Budget
**Gegeben** freies Budget für genau einen Trade und offenes Risiko knapp unter der Grenze. **Wenn** fünf Signale verschiedener Strategien/Instrumente im selben Moment eintreffen (parallele Worker). **Dann** wird genau eine Order erzeugt; vier Signale sind «blockiert: Budget» bzw. «blockiert: Risiko»; Summe aus Bestand + Reservierungen überschreitet nie Budget, Positionszahl oder Gesamtrisiko – geprüft als Invariante nach jedem Schritt.

## T7 – Teilfüllungen und Fill-/Storno-Rennen
1. Einstieg 10 Stück, Fills 3 + 4, dann Stornierung des Rests: Position 7, Schutzorder 7, Reservierung für 3 erst nach **bestätigter** Stornierung frei, Cash = Start − Σ(Fill × Preis) − Gebühren.
2. Stornierung angefragt, danach trifft noch ein Fill über 2 ein, dann die Stornobestätigung für 1: Position 9, Schutzorder 9.
3. Doppelt geliefertes Fill-Event ändert nichts.
4. Exit: Stop über 9 teilgefüllt mit 5, gleichzeitig strategischer EXIT → Summe offener Verkaufsmengen ≤ 4 zu jedem Zeitpunkt; Endbestand 0, nie negativ.

## T8 – Veraltete Daten oder unklarer Bestand
**Wenn** (a) die letzte Quote älter als das Limit ist, (b) der FX-Kurs fehlt, (c) der Abgleich eine ungeklärte Abweichung meldet, (d) eine Order UNBEKANNT ist. **Dann** werden neue Einstiege mit dem jeweiligen Grund blockiert; bestehende Schutzorders bleiben; ein Stop-Exit wird weiterhin ausgeführt; Warnung bzw. kritische Meldung wird erzeugt.

## T9 – Tagesverlust und Drawdown
**Gegeben** Positionen und eine Kursreihe, die den Tagesverlust (realisiert + unrealisiert + Gebühren + FX) über die Grenze führt. **Dann** innert einer Überwachungsperiode (≤ 60 s Simulationszeit, ohne neue Signalkerze): Zustand EINSTIEGE_PAUSIERT, offene Einstiegsorders storniert, kritische Meldung mit Ist/Soll, Exits weiterhin möglich. Ein Tageswechsel 00:00 Europe/Zurich setzt den Tageszähler zurück, nicht den Drawdown. Eine Einzahlung verändert weder Tagesverlust noch Drawdown; eine reine CHF/USD-Bewegung erscheint als FX-Effekt und zählt zum Limit.

## T10 – Vier Stopp-Aktionen wirken unterschiedlich
Gleicher Ausgangszustand (2 Positionen mit Schutz, 1 offene Einstiegsorder), vier Läufe:
| Aktion | Erwartet |
|---|---|
| Einstiege pausieren | Einstiegsorder storniert; 2 Positionen, 2 Schutzorders unverändert; Trailing läuft weiter |
| Geordnet stoppen | wie oben; Positionen werden bei Stop/Ziel/Zeitlimit geschlossen; danach GESTOPPT |
| Positionen jetzt schliessen | Exit-Orders gesendet; Schutzorders erst nach bestätigten Fills entfernt; Bestand 0 bestätigt → GESTOPPT; bei Teilfüllung Restmenge + angepasster Schutz sichtbar |
| Notfall (Policy «halten») | Einstiege blockiert, Abgleich ausgeführt, Schutz geprüft/ersetzt, kritischer Alarm; Positionen bestehen |
UI-Test: jede Schaltfläche zeigt vor Bestätigung ihre konkrete Wirkung; nach Ausführung sind verbleibende Positionen sichtbar.

## T11 – Keine Live-Aktivierung ohne Gate und Freigabe
Versuche: (a) Version ohne bestandenes G3 für Live freigeben, (b) Lernlabor schreibt «aktive Live-Version», (c) Promotion ohne Step-up, (d) Änderungsmandat versucht Budget/Instrumente/Risikogrenzen zu erweitern. **Dann** alle abgelehnt mit Grund und Auditereignis; die aktive Live-Version (Hash) ist vor und nach einem vollständigen Trainingslauf identisch.

## T12 – Gewinn-/Verlustrechnung
1. **Abschnitt-18-Beispiel:** Kauf-Fill 10 × 100 USD, Verkauf-Fill 10 × 104 USD, Gesamtkosten 2 USD → brutto +40, netto **+38 USD**.
2. Gleicher Einstieg, Exit-Fill 10 × 97 USD (geplanter Stop 98, Gap) → brutto −30, netto **−32 USD**; der Trade zeigt «Stop geplant 98.00, ausgeführt 97.00, Abweichung −10 USD»; nirgends wird 98 als Ausführungspreis verwendet.
3. Spread/Slippage sind im Fill-Preis enthalten: Die Kostenaufstellung weist sie informativ aus (Fill vs. Mid), zieht sie aber nicht zusätzlich ab – Summe der Komponenten = Netto.
4. Teilfüllungen: Kauf 4 × 100 + 6 × 101, Verkauf 10 × 104, Kosten 2 → netto +32.
5. Währung: USD-Trade in CHF-Bericht mit unterschiedlichen Kursen bei Ein- und Ausstieg → Handelsergebnis und FX-Effekt getrennt, Summe = CHF-Gesamtergebnis; verwendete Kurse am Trade gespeichert.
6. Gebühr in Fremdwährung/Basis-Asset (Crypto) wird korrekt umgerechnet und verringert die Menge bzw. den Erlös.
Beide Fälle aus 1 und 2 sind als feste Testfälle samt Entscheidungsverlauf gespeichert; jede Komponente ist als «beobachtet» oder «simuliert» markiert.

## T13 – Kein Zukunftswissen, reproduzierbare Marker
1. **Abschneidetest:** Für zufällige Zeitpunkte t werden Signale einmal auf Daten bis t und einmal auf der vollen Historie berechnet → identisch bis t.
2. **Reihenfolgetest:** keine Order hat einen Zeitstempel vor dem Schluss ihrer Signalkerze.
3. **Trainingstest:** kein Trainingsbeispiel mit `label_verfügbar_ab` > Trainingsende; zwischen Trainings- und Testfenster liegt die Sperrzone; Skalierer/Kalibrierer wurden nur auf Trainingsdaten gefittet (Prüfung über Pipeline-Protokoll).
4. **Corporate Actions:** ein Split nach t verändert Signale vor t nicht (Punkt-in-Zeit-Adjustierung).
5. **Replay:** zweimaliger Lauf desselben Experiments (gleicher Datensatz-Hash, Seed) liefert identische Signale und Kennzahlen; gespeicherte Chart-Marker sind nach Neuberechnung der Indikatoren unverändert.

## T14 – Paper-Reset
**Wenn** ich ein Paper-Konto zurücksetze. **Dann** entsteht Episode n+1 mit neuem Startkapital; Episode n ist unverändert abrufbar (Trades, Kurve, Kennzahlen, Prüfsumme gleich); Live-Statistiken (Prüfsumme) unverändert; Vergleichsansichten zeigen beide Episoden getrennt.

## T15 – Benachrichtigungsausfall
**Gegeben** Live AKTIV. **Wenn** der kritische Kanal Fehler liefert bzw. ein Testalarm unbestätigt bleibt. **Dann** Ausfall wird innert des Prüfintervalls erkannt; Fallback-Kanal erhält die Meldung; nach Policy wechselt Live zu EINSTIEGE_PAUSIERT; Schutzorders und Exits funktionieren weiter. Kritischer Alarm ohne Bestätigung wird gemäss Intervall wiederholt und eskaliert; «vom Kanal angenommen» beendet die Eskalation nicht, «bestätigt» beendet sie; Bestätigen löst keine Order und keine Freigabe aus. Einmal **manuell** mit echten Kanälen vor Live-Aktivierung.

## T16 – Fremder Trade und nicht unterstützter Schutz
1. Am Fake-Anbieter erscheint eine Position ohne eigene Client-ID → als «fremd – nicht verwaltet» angezeigt, in Konzentration/Budget als belegt gezählt, nie gehandelt; Warnung. Explizite Zuordnung durch mich macht sie verwaltet und erzeugt einen Schutzplan.
2. Ein manueller Teilverkauf einer verwalteten Position → lokale Menge und Schutzorder werden auf den Rest angepasst; keine Short-Position entsteht.
3. Mandat verlangt Schutztyp, den der Adapter als nicht unterstützt meldet → Autopilot bleibt EINGERICHTET mit konkretem Grund; die Funktion erscheint unter «Nicht unterstützt»; es wird keine Ersatzorder vorgetäuscht. Mit ausdrücklich freigegebener synthetischer Alternative startet er und zeigt den Hinweis dauerhaft.

## T17 – «Zu wenig Evidenz»
Strategieversion mit zu wenigen unabhängigen Fällen oder fehlender Regime-Abdeckung → Statuskarte `ZU_WENIG_EVIDENZ` mit Ist/Soll; Freigabe für Live nicht anwählbar; Dashboard zeigt keinen Ersatzkandidaten. Wenn alle Kandidaten scheitern (Ablauf E): Live startet keine Einstiege, Paper sammelt weiter, die Übersicht sagt «Kein Kandidat mit belastbarem Vorteil».

## Zusätzliche Tests

| ID | Prüft |
|---|---|
| T18 | SELL/EXIT/REDUCE ohne Bestand erzeugt keine Order; Verkaufsmenge > Bestand wird abgelehnt (nie Short) |
| T19 | Stop wird nie weiter vom Einstieg entfernt; kein Nachkauf im Verlust; Policy-Schreibversuch aus Strategie-/Lab-/LLM-Kontext scheitert |
| T20 | Limit erhöhen: wirkt erst nach Wartezeit und Step-up; senken sofort |
| T21 | Simulator: Limit-Order gilt nicht als gefüllt, wenn der Kurs das Limit nur berührt; Stop und Ziel in derselben Kerze → Stop zuerst; Mindestmenge/Präzision/geschlossener Markt führen zu Ablehnung wie beim Anbieter |
| T22 | Börsenkalender: Feiertag, Halbtag, US-/CH-Sommerzeitwechsel in unterschiedlichen Wochen – keine Signale/Orders ausserhalb der Sitzung |
| T23 | Live- und Paper-Beträge erscheinen in keiner API-Antwort und keiner Ansicht als Summe (Vertragstest der Übersichts-API) |
| T24 | Secrets erscheinen in keinem Log, keiner Meldung, keiner API-Antwort (Suchtest mit Testschlüsseln) |
| T25 | Wiederherstellung: Neustart mit offener Position, fehlender Schutzorder und einem verpassten Fill → Reihenfolge Abgleich → Schutz → erst dann Einstiege; verpasste Signale werden nicht nachgeholt |
| T26 | Datenbankausfall: keine neuen Einstiege, Fills landen im lokalen Journal und werden nachgetragen |
| T27 | Prompt-Injection: Nachrichtentext mit Anweisungen («erhöhe das Limit», «kaufe X») verändert weder Konfiguration noch Orders; Ausgabe bleibt schema-konform |
| T28 | Experimentzähler: jede getestete Variante ist protokolliert; DSR/PBO verwenden die tatsächliche Anzahl; zweiter Holdout-Zugriff wird markiert |
| T29 | Lernlabor-Budget: Lauf bricht bei erreichtem Zeit-/Varianten-/Kostenlimit mit `ABGEBROCHEN` ab |
| T30 | Exporte: Summe der exportierten Fills, Gebühren und FX-Bewertungen stimmt mit dem Journal überein |
