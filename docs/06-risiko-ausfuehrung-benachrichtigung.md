# 06 · Risiko, Ausführung, Benachrichtigung und Wiederherstellung

## 1. Drei Arten von Grenzen (im Produkt getrennt benannt)

| Begriff in der UI | Bedeutung | Beispiel |
|---|---|---|
| **Preislimit** | Eigenschaft einer Order | Kauf-Limit 101.20 |
| **Kapitalgrenze** | wie viel Geld gebunden werden darf | max. CHF 600 pro Instrument |
| **Risikogrenze** | wie viel geplanter oder eingetretener Verlust zulässig ist | max. 0.5 % Risiko pro Trade, Tagesverlust 1.5 % |

**Kapitalbindung** (Nominal) und **geplantes Verlustrisiko** (Menge × (Einstieg − Stop) + geschätzte Kosten beider Seiten) werden überall als zwei Zahlen gezeigt.

Hinweistext an jeder Verlustgrenze und jedem Stop: «Auslöse- und Kontrollregel, kein garantierter Maximalverlust. Kurslücken, Handelsunterbrüche und fehlende Liquidität können die Ausführung verschlechtern oder verhindern.»

## 2. Risikopolicy

Versioniertes Objekt je Konto. Startwerte sind **Annahmen (A-RISK)** für ein kleines Pilotbudget und frei änderbar; Erhöhungen im Live-Modus mit Step-up und Wartezeit.

| Gruppe | Parameter | Startwert |
|---|---|---|
| Kapital | Gesamtbudget Live; Teilbudgets Aktien / Crypto | von mir festzulegen (offener Punkt O-1) |
| Kapital | max. Investition pro Instrument / Strategie / Einzelorder | 20 % / 60 % / 20 % des Teilbudgets |
| Kapital | Cash-Reserve | 10 % |
| Risiko | geplantes Risiko pro Trade | 0.5 % des zugeordneten Eigenkapitals |
| Risiko | gesamtes offenes Stop-Risiko | 2.5 % |
| Risiko | gleichzeitige Positionen | 6 |
| Verlust | Tagesverlust | 1.5 % → Einstiege pausiert bis zum nächsten Tageswechsel |
| Verlust | Wochenverlust | 3 % → Einstiege pausiert bis Montag **und** meine Bestätigung |
| Verlust | Drawdown vom bereinigten Höchststand | 8 % → Einstiege pausiert bis zu meiner Wiederfreigabe; 12 % → Notfallpolicy |
| Konzentration | je Instrument / korrelierte Gruppe (ρ > 0.7, 60 Tage) / Handelsplatz | 20 % / 40 % / 100 % (ein Platz je Klasse in V1) |
| Orders | erlaubte Typen | Einstieg: Limit (marktnah, mit Preisobergrenze); Schutz: Stop; Exit: Limit, im Notfall Market |
| Orders | max. Orderalter Einstieg | bis Schluss der nächsten Signalkerze |
| Markt | max. Spread | Aktien/ETF 10 bp, Crypto 15 bp |
| Markt | max. Datenalter | Quote ≤ 10 s bei Ordererzeugung; Signalkerze ≤ 2 min nach Schluss vollständig |
| Markt | max. Slippage (Preisobergrenze der Einstiegsorder) | 0.3 × ATR der Signalebene, höchstens 30 bp |
| Markt | Liquiditätsanteil | Order ≤ 1 % des medianen Kerzenvolumens |
| Frequenz | neue Einstiege pro Tag | 4 |
| Frequenz | Wartezeit nach Verlustserie | nach 3 Verlusttrades in Folge 24 h keine Einstiege dieser Strategie |
| Ereignis | Earnings-Sperrfenster (Aktien) | kein Einstieg ab 2 Handelstagen davor bis 1 danach |
| Zeit | Handelsfenster | Aktien: reguläre Sitzung ohne erste/letzte 15 min; Crypto: 24/7 ohne Wartungsfenster |
| Gesperrt | Margin, Short, Derivate, Hebel, Martingale, Nachkaufen im Verlust, Stop-Ausweitung | fest, nicht konfigurierbar in V1 |

### 2.1 Unabhängige Risikoprüfung (M6)

Jede risikosteigernde Orderabsicht durchläuft die Prüfung in **einer Datenbanktransaktion mit Sperre auf dem Konto**:

1. Mandat aktiv, Instrument und Strategieversion im Mandat?
2. Autopilot-Zustand erlaubt Einstiege?
3. Daten frisch, Spread/Liquidität innerhalb der Grenzen, FX-Kurs gültig?
4. Kein Order-Zustand UNBEKANNT, letzter Abgleich fehlerfrei und nicht älter als zulässig?
5. Schutzpolicy für dieses Instrument beim Anbieter verfügbar?
6. Kapital: frei = Cash − Reserve − **bestehende Reservierungen**; Budget- und Konzentrationsgrenzen inklusive der neuen Order?
7. Risiko: offenes Stop-Risiko inklusive Reservierungen + neues Risiko ≤ Grenze?
8. Verlustgrenzen und Frequenzregeln nicht verletzt?

Bestanden → Reservierung von Kapital und Risiko in derselben Transaktion. Jeder nicht bestimmbare Wert (unbekannter Preis, fehlende Position vom Anbieter, fehlender FX-Kurs) zählt als **Verletzung**, nicht als null. Ergebnis und alle Ist/Soll-Werte werden am Signal gespeichert.

Risikosenkende Orders (Stop, REDUCE, EXIT) durchlaufen nur Plausibilitätsprüfungen (Menge ≤ Bestand, keine Short-Position, Mengenkonflikt mit anderen Exit-Orders) und werden von Verlust-/Frequenzgrenzen nie blockiert.

Die Risikopolicy ist für Strategie-, Lern- und LLM-Code nicht schreibbar (Datenbankrolle ohne Schreibrecht). Nur ein Befehl mit Step-up kann sie ändern.

### 2.2 Verlustrechnung

- **Eigenkapital** = Cash + Marktwert der Positionen (Bid für Long-Bestände; bei geschlossenem Markt letzter gültiger Schluss), in Kontowährung und in CHF.
- **Tagesverlust** = Eigenkapital jetzt − Eigenkapital zum Tageswechsel − Netto-Kapitalflüsse seit Tageswechsel. Enthält realisierte und unrealisierte Ergebnisse, Gebühren und FX-Effekte. **Tageswechsel 00:00 Europe/Zurich**, Wochenwechsel Montag 00:00. (Börsenzeiten bleiben die des Handelsplatzes.)
- **Drawdown** = 1 − Eigenkapital ÷ bereinigter Höchststand. Der Höchststand wird um Ein-/Auszahlungen verschoben; eine Einzahlung ist kein Gewinn, eine Auszahlung kein Drawdown.
- **FX:** Limits werden in CHF ausgewertet. Die Anzeige zerlegt jede Veränderung in Handelsergebnis (Handelswährung) und FX-Effekt; verwendeter Kurs mit Quelle und Zeit ist sichtbar. Für realisierte Ergebnisse gilt der tatsächliche Umrechnungskurs des Anbieters, falls umgerechnet wurde; sonst der Bewertungskurs.
- Auswertung **laufend** (bei jedem Preis-/Fill-Ereignis, mindestens minütlich) durch den `guardian`, nicht erst bei der nächsten Strategieberechnung. Vorwarnung bei 70 % einer Grenze.

### 2.3 Reaktion bei Limit-Erreichen
Einstiege stoppen → offene, ungefüllte Einstiegsorders stornieren → Reservierungen freigeben, sobald Stornierung **bestätigt** → bestehende Positionen gemäss Policy (Standard: mit Schutz weiterführen; Drawdown-Stufe 2: Notfallpolicy) → kritische Meldung mit dem auslösenden Ist/Soll-Wert. Exits bleiben jederzeit zulässig.

## 3. Ausführung

### 3.1 Genau eine wirtschaftliche Order je Absicht
1. **Absicht speichern:** Zeile `orderabsicht` mit Idempotenzschlüssel `(mandat, signal, rolle)` und Unique-Constraint. Ein zweiter Prozess oder ein Neustart scheitert am Constraint und liest die bestehende Absicht.
2. **Client-Order-ID** wird deterministisch aus der Absicht abgeleitet und vor dem Senden gespeichert (Kraken: `cl_ord_id`, IBKR: `orderRef`).
3. **Senden**, Zustand ÜBERMITTELT.
4. **Antwort bleibt aus (Timeout/Abbruch):** Zustand UNBEKANNT. Es wird **nicht** erneut gesendet. Der Adapter fragt den Anbieter nach der Client-Order-ID (offene Orders, geschlossene Orders, Fills). Gefunden → Zustand übernehmen. Sicher nicht vorhanden (zwei übereinstimmende Abfragen nach Ablauf der Order-Deadline) → Absicht als «nicht platziert» schliessen; ein neuer Versuch ist eine neue, erneut risikogeprüfte Entscheidung der Strategie.
5. Eine Orderübermittlung trägt, wo der Anbieter es unterstützt, eine **Deadline** (Kraken: `deadline`, 2–60 s), damit eine verspätet ankommende Order nicht mehr akzeptiert wird.

Kraken dokumentiert keine Ablehnung doppelter `cl_ord_id`; die Eindeutigkeit gilt nur unter offenen Orders. Der Schutz vor Doppelkauf liegt deshalb vollständig auf unserer Seite (Schritte 1–4) und wird nicht an den Anbieter delegiert.

### 3.2 Fills, Stornos, Restmengen
- Fills sind eigene, über die Anbieter-Fill-ID eindeutige Datensätze; doppelt gelieferte Fill-Events sind wirkungslos.
- Nach **jedem** Fill einer Einstiegsorder: Position aktualisieren → Schutzorder auf die tatsächlich gefüllte Menge setzen/anpassen → Reservierung um den gefüllten Teil in Bestand umbuchen.
- Stornierung: Zustand STORNIERUNG_ANGEFRAGT; erst die Bestätigung des Anbieters gibt Reservierung frei. Ein Fill, der währenddessen eintrifft, wird normal verarbeitet (Fill-/Storno-Rennen).
- Exit-Konflikte: Summe aller offenen Verkaufsorders eines Instruments darf den Bestand nie übersteigen. Vor einem strategischen Exit wird die Schutzorder angepasst oder ersetzt (erst Bestätigung abwarten, dann Exit senden); bei Anbietern mit verknüpften Orders (OCA) übernimmt das der Anbieter.

### 3.3 Schutzorders
Rangfolge: **(1) Schutzorder beim Anbieter**, **(2) überwachter synthetischer Schutz** nur mit ausdrücklicher Freigabe im Mandat und sichtbarem Hinweis.

Anbieterspezifische Schutzpolicy für V1 (Belege in Dokument 03):

| | Kraken Spot | IBKR |
|---|---|---|
| Stop-Loss beim Anbieter | ja (stop-loss, stop-loss-limit, trailing-stop) | ja; je Börse nativ oder von IBKR simuliert (simulierte hängen von Marktdaten ab) |
| Stop **und** Ziel gleichzeitig beim Anbieter | **nein** – kein OCO/Bracket über die API; ein Conditional Close erlaubt nur *eine* Folgeorder | ja – Bracket (`parentId`) und OCA-Gruppen |
| V1-Policy | Stop-Loss liegt immer bei Kraken, als eigene Order auf die gefüllte Menge. Ziele und Trailing führt die Engine: Trailing durch Nachziehen (Ändern) des Stops; Gewinnziel als **synthetischer** Exit (Engine storniert Stop → wartet Bestätigung → sendet Exit). | Einstieg mit Stop (und ggf. Ziel) als OCA-Gruppe beim Broker, GTC. Nur Instrumente, deren Stop an der Börse nativ unterstützt wird, sind ohne Zusatzfreigabe zugelassen. Verhalten bei Teilfüllung wird vor Freigabe im IBKR-Paper-Konto geprüft (O-10). |
| Sichtbarer Hinweis | «Gewinnziel wird von TradingEngine überwacht, nicht von Kraken. Bei Ausfall bleibt der Stop-Loss bei Kraken aktiv, das Ziel nicht.» | «Stop wirkt nur in der regulären Handelszeit. Über Nacht und am Wochenende besteht Kurslückenrisiko.» · «IBKR-Sitzung gültig bis …; ohne Sitzung betreut nur der Broker-Stop die Position.» |

Kraken-Conditional-Close wird **nicht** verwendet: jede Teilfüllung erzeugt dort eine eigene Folgeorder, und Folgeorders werden nicht automatisch storniert, wenn die Position anders geschlossen wird – beides erschwert den Abgleich.

**Dead-Man-Switch (Kraken `CancelAllOrdersAfter`): in V1 nicht verwendet.** Er storniert laut Dokumentation *alle* Orders des Kontos – also auch die Schutz-Stops – und löst auch aus, wenn Krakens Handelssystem aus einer Wartung zurückkehrt. Für ein Long-only-Spot-Konto mit Stops beim Anbieter würde er genau im Störfall den Schutz entfernen. Stattdessen: kurze Orderlaufzeiten für Einstiege (GTD/Deadline), sodass bei einem Engine-Ausfall keine Einstiegsorders lange offen bleiben.

Fehlt eine Schutzorder (abgelehnt, storniert, Menge passt nicht): sofort ersetzen; gelingt das innert 60 s nicht → kritischer Alarm, Notfallpolicy für diese Position (Standard: marktnaher Exit), Einstiege pausiert.

Unterstützt ein Anbieter die im Mandat gewählte Schutzpolicy für ein Instrument nicht, bleibt der Autopilot in EINGERICHTET mit dem konkreten Grund.

### 3.4 Abgleich mit dem Anbieter
- **Wann:** beim Start, nach jeder Wiederverbindung, alle 60 s, nach jedem Order-Timeout, vor jedem Zustandswechsel nach AKTIV.
- **Was:** Positionen, Cash, offene Orders, Fills seit letztem bestätigtem Stand.
- **Abweichungen:**
  - unbekannter Fill einer eigenen Order → nachtragen;
  - Order beim Anbieter, die lokal fehlt, mit eigener Client-ID → übernehmen; ohne → **fremd**;
  - Position/Menge, die sich nicht aus eigenen Fills erklärt → **fremde Position**: wird angezeigt, bewertet und in Konzentrations-/Budgetgrenzen als belegt gezählt, aber nie automatisch gehandelt oder liquidiert. Ich kann sie ausdrücklich einer Strategie zuordnen oder dauerhaft als «nicht verwaltet» markieren;
  - verwaltete Position kleiner als lokal (manueller Verkauf) → lokale Position anpassen, Schutzorder auf Restmenge reduzieren, Warnung;
  - nicht auflösbar → Zustand FEHLER, Einstiege blockiert, kritischer Alarm.
- «Geschlossen» wird nur gemeldet, wenn Fills die Menge decken **oder** der Anbieterbestand null bestätigt.

### 3.5 Wiederherstellung (Reihenfolge fest)
1. Verbindung und Authentifizierung. 2. Abgleich Bestand/Orders/Fills. 3. Schutz jeder verwalteten Position prüfen und reparieren. 4. Offene Einstiegsorders gegen Gültigkeit prüfen, abgelaufene stornieren. 5. Verlustgrenzen neu bewerten. 6. Erst dann Rückkehr in den vorherigen Zustand; verpasste Signale werden **nicht** nachgeholt (abgelaufen).

### 3.6 Messgrössen und Ziele (Swing/Intraday auf Kerzenbasis)

| Messgrösse | Ziel | Bei Verletzung |
|---|---|---|
| Kerzenschluss → Signal gespeichert | ≤ 30 s (p95) | Warnung ab 2 min; Signal verfällt nach Policy |
| Signal → Order übermittelt (Stufe 3) | ≤ 5 s (p95) | Warnung |
| Übermittelt → Anbieterbestätigung | ≤ 3 s (p95) | UNBEKANNT-Pfad ab 10 s |
| Fill → Schutzorder bestätigt | ≤ 10 s (p99) | kritisch ab 60 s |
| Quote-Alter bei Ordererzeugung | ≤ 10 s | Order blockiert |
| Abgleichsintervall | ≤ 60 s | Einstiege blockiert ab 5 min |
| Ausführungsabweichung (Fill vs. Modellpreis) | Median ≤ Modellannahme | Warnung ab 2 × über 20 Fills |
| Engine-Heartbeat | alle 60 s | externer Alarm nach 3 min |

## 4. Benachrichtigung

### 4.1 Kanäle (V1)

| Kanal | Rolle | Eigenschaften (V = am 4.10.2026 geprüft) |
|---|---|---|
| In-App | vollständiges Meldungsarchiv, Bestätigung | immer |
| **Telegram-Bot** | Hauptkanal: Info, Signale, Warnungen, Freigabe-Schaltflächen, Notfallbefehle | kostenlos; Inline-Schaltflächen mit Callback; ca. 1 Nachricht/s pro Chat (V, core.telegram.org/bots) |
| **Pushover** | kritische Alarme | Priorität 2 wiederholt bis zur Bestätigung (`retry` ≥ 30 s, `expire` ≤ 10 800 s), übergeht Ruhezeiten des Geräts; Quittungs-API liefert `acknowledged` (V, pushover.net/api) |
| E-Mail (Resend) | Tagesbericht, Fallback-Stufe | 3 000/Monat kostenlos (V) |
| SMS (Twilio) | optional, letzte Eskalationsstufe | USD 0.0769 je Segment in die Schweiz (V) |

Web-Push als installierte Web-App auf iOS ist technisch möglich (seit iOS 16.4, V), die Zuverlässigkeit ist nicht belegt → nicht als kritischer Kanal eingeplant.

### 4.2 Verhalten je Stufe

| Stufe | Kanäle | Bestätigung | Wiederholung/Eskalation | Ruhezeit (22–07 Uhr, einstellbar) |
|---|---|---|---|---|
| Information | In-App; Telegram gebündelt (Tagesbericht 07:30) | nein | keine | zurückgehalten |
| Handelssignal | In-App, Telegram | nur Stufe 2 (Freigabe mit Ablaufzeit) | keine Wiederholung; Aktualisierung derselben Nachricht statt neuer | Stufe 1: zurückgehalten; Stufe 2: zugestellt, stumm |
| Warnung | In-App, Telegram | optional | einmalige Erinnerung nach 30 min, falls Zustand anhält | zugestellt, stumm |
| Kritisch | In-App, Pushover Prio 2 **und** Telegram gleichzeitig | **erforderlich** | Pushover alle 60 s bis 3 h; nach 15 min ohne Bestätigung E-Mail (+ SMS falls aktiviert); nach 3 h neuer Zyklus, solange ungelöst | wird übergangen (Standard; abschaltbar) |

Regeln:
- Jede Meldung beginnt mit dem Modus in Grossbuchstaben: `[LIVE]`, `[PAPER]`, `[FORSCHUNG]`.
- Wortlaut-Vorlagen unterscheiden strikt: «Order vorbereitet – wartet auf deine Freigabe bis 15:00» / «Order gesendet» / «Ausgeführt: 10 Stk. zu 100.02».
- **Entdoppelung:** Schlüssel aus (Art, Konto, Objekt, Zustand). Gleicher Schlüssel aktualisiert die bestehende Meldung; ein anhaltender Zustand erzeugt keine Flut.
- **Zustellstatus** hat vier getrennte Stufen: erzeugt → vom Kanal angenommen → auf Gerät zugestellt (wo der Kanal es liefert) → von mir bestätigt. Nur die letzte stoppt die Eskalation.
- **Bestätigen genehmigt nichts.** Freigaben sind eigene Schaltflächen mit eigener Ablaufzeit; Freigaben über Telegram sind in V1 nur für Paper möglich, Live-Freigaben (Stufe 2) verlangen die Web-App mit Anmeldung (Annahme A-TG).
- Im Autopilot (Stufe 3) meldet das System die **bereits erfolgte** Handlung und ihr Ergebnis; Schutz- und Notfallausstiege warten nie auf eine Reaktion.
- Meldungen enthalten keine Zugangsdaten, keine vollständigen Kontonummern.

### 4.3 Überwachung der Kanäle
- Technische Prüfung alle 15 min (Bot-/API-Erreichbarkeit).
- **Testalarm** mit Bestätigungspflicht: beim Onboarding, vor jeder Mandatsaktivierung (höchstens 7 Tage alt) und monatlich.
- Kanalausfall-Policy (Standard): ist kein kritischer Kanal zustellfähig oder bleibt ein Testalarm 24 h unbestätigt → Live: **Einstiege pausieren**; Schutz und Exits bleiben aktiv. Paper läuft weiter.
- Der Benachrichtigungsdienst selbst wird durch den externen Heartbeat-Dienst und den Vercel-Watchdog überwacht (zwei unabhängige Wege).

## 5. Sicherheit
- API-Schlüssel: nur Handels- und Leserechte, **keine Auszahlung**; IP-Allowlist auf die Server-IP; getrennte Schlüssel für Lesen (Datendienst) und Handeln (nur Container `live`). Die App prüft beim Verbinden, soweit der Anbieter es abfragbar macht, dass Auszahlungsrechte fehlen, und verweigert sonst die Verbindung bzw. verlangt meine ausdrückliche Bestätigung.
- Zugangsdaten werden nie im Browser eingegeben und an die Web-App gesendet, sondern direkt auf dem Engine-Server hinterlegt (dokumentierter CLI-Schritt). Die Web-App zeigt nur Status und Rechte.
- Auditprotokoll für jede Bedienhandlung, Policy-Änderung, Freigabe, jeden Zustandswechsel und jede manuelle Zuordnung.
- LLM: keine Werkzeuge mit Schreibwirkung auf Handel oder Konfiguration; externe Texte nur in validierte Schemata.
