# 04 · Strategie-, Lern- und Validierungsplan

Alle Strategien hier sind **Forschungskandidaten**. Dieses Dokument behauptet für keine davon einen Vorteil. Das wahrscheinlichste ehrliche Ergebnis der ersten Durchläufe ist, dass mindestens ein Teil der Kandidaten nach Kosten scheitert (Ablauf E).

## 1. Analysegrundlage

### 1.1 Zeitebenen
- **Regime:** Tageskerzen. **Signal:** Crypto 4h und 1d; Aktien/ETF in V1 **1d** (Signal nach Börsenschluss, Order zur nächsten Sitzung – dafür genügen die kostenlosen vollständigen Tagesdaten, Dokument 03). Stündliche Aktiensignale erst mit bezahlten Echtzeitdaten und eigener G1-Prüfung. **Überwachung** von Stops, Datenalter und Limits: Minutentakt, unabhängig vom Signalrhythmus.
- Entscheidungen fallen nur auf **abgeschlossenen** Kerzen; die Order entsteht frühestens zum nächsten handelbaren Zeitpunkt danach. Intrabar-Logik existiert in V1 nicht.

### 1.2 Feature-Menge (bewusst klein)

| Gruppe | Features | Begründung |
|---|---|---|
| Trend | Abstand Schluss zu SMA200 (1d), Steigung SMA50, ADX(14) | Richtung, Stärke – ohne drei redundante MA-Varianten |
| Momentum | Rendite 20/60 Perioden, RSI(14) | ein Oszillator genügt; MACD wäre zu RSI/Rendite hoch korreliert und entfällt |
| Volatilität | ATR(14)/Preis, realisierte Vola-Perzentile (1 Jahr) | Positionsgrösse, Stressdetektion |
| Struktur | Donchian-Hoch/Tief(20), Abstand zum 52-Wochen-Hoch | Ausbruch, Support/Resistance ohne subjektive Zonen |
| Volumen/Liquidität | Volumen relativ zum 20er-Median, Spread in bp, Dollar-Volumen | Bestätigung und Handelbarkeit |
| Marktkontext | Regime des Leitinstruments (SPY bzw. BTC), 20-Tage-Korrelation zum Bestand | Portfolio-Kontext |
| Ereignis | Tage bis/seit Earnings (Aktien) | Sperrfenster |

Vor Aufnahme eines weiteren Features: Korrelation zu bestehenden prüfen (|ρ| > 0.8 → nicht aufnehmen) und im Experimentprotokoll begründen.

### 1.3 Marktregime (regelbasiert, je Instrument und für den Leitmarkt)

| Regime | Regel (Startdefinition, als Version geführt) |
|---|---|
| Stress | realisierte Vola > 90. Perzentil **oder** Drawdown vom 60-Tage-Hoch > 2.5 × ATR-Tagesäquivalent **oder** Datenlücke/Handelsunterbruch |
| Aufwärtstrend | Schluss > SMA200, SMA50 steigend, ADX ≥ 20 |
| Abwärtstrend | Schluss < SMA200, SMA50 fallend, ADX ≥ 20 |
| Seitwärts | sonst (ADX < 20) |

Stress hat Vorrang. Die Regimeregel ist selbst ein versioniertes Objekt und wird nicht pro Strategie optimiert (sonst vervielfacht sich der Suchraum).

## 2. Startstrategien

| | S1 Trendfolge-Pullback | S2 Volumenbestätigter Ausbruch | S3 Mean Reversion |
|---|---|---|---|
| Regime | nur Aufwärtstrend (Instrument **und** Leitmarkt nicht im Stress) | Aufwärtstrend oder Seitwärts→Ausbruch; nicht Stress | nur Seitwärts; Leitmarkt nicht Abwärts/Stress |
| Einstieg | Rücksetzer an EMA20 der Signalebene, danach Schluss über Vorkerzenhoch | Schluss über Donchian-Hoch(20) mit Volumen ≥ 1.5 × Median(20) | Schluss unter unterem Bollinger-Band(20, 2) und RSI(14) < 30, dann erste Kerze mit höherem Schluss |
| Stop | Einstieg − 2 × ATR | Ausbruchsniveau − 1 × ATR | Einstieg − 1.5 × ATR |
| Ziel/Exit | Trailing 3 × ATR vom höchsten Schluss; Exit bei Regimewechsel | Trailing 2.5 × ATR; Exit bei Schluss zurück unter Ausbruchsniveau innert 3 Kerzen | Mittelband (SMA20); Zeitlimit 10 Kerzen |
| Max. Haltedauer | 40 Kerzen der Signalebene | 30 Kerzen | 10 Kerzen |
| Grösse | Risiko pro Trade fix (Risikopolicy) ÷ (Stopabstand + Kosten) | gleich | gleich, halbes Risiko |
| Parametergrenzen | ATR-Faktor 1.5–3, EMA 15–30 | Donchian 15–40, Volumenfaktor 1.2–2 | BB-Breite 1.8–2.5, RSI 20–35 |
| Kein Trade bei | Earnings ≤ 2 Tage, Spread > Limit, Daten veraltet, Netto-CRV < 1.5 | gleich | gleich, Netto-CRV < 1.0 |

Stops werden nach dem Einstieg **nie weiter entfernt**; nur enger oder gleich. Kein Nachkaufen im Verlust.

**Baselines je Strategie:** (a) Cash, (b) Buy-and-Hold des gehandelten Instruments mit gleichen Kosten, (c) Buy-and-Hold skaliert auf die gleiche durchschnittliche Marktexposure wie die Strategie, (d) Zufallseinstiege mit gleicher Haltedauer- und Grössenverteilung (zeigt, ob der Einstieg überhaupt Information trägt).

## 3. Testarten und ihr Zweck

| Testart | Daten | Aussage | Keine Aussage über |
|---|---|---|---|
| Backtest | historisch, chronologisch | Regeln sind konsistent; grobe Ökonomie nach Kosten | echte Ausführung |
| Walk-forward | Training auf älterem Fenster, Test auf jeweils späterem | Robustheit ausserhalb der Stichprobe | Zukunft |
| Paper | jetzt eintreffende Daten | Betrieb, Timing, Übereinstimmung mit Backtest-Erwartung | echte Fills, Marktwirkung |
| Shadow | wie Paper, Kandidat neben fester Live-Version | relativer Vergleich bei gleichem Markt | absolute Qualität |

## 4. Schutz vor falschen Erfolgsergebnissen

### 4.1 Datenaufteilung
- **Endprüfungs-Holdout:** die jüngsten 12 Monate je Anlageklasse werden bei Projektstart eingefroren und pro Strategiefamilie **genau einmal** für G2 verwendet. Jeder Zugriff wird protokolliert; ein zweiter Zugriff derselben Familie markiert das Ergebnis als «Holdout verbraucht – nur noch Forward-Evidenz zählt».
- **Entwicklungsdaten:** alles davor, im Walk-forward: Trainingsfenster 24 Monate, Testfenster 6 Monate, rollierend. Zwischen Training und Test liegt eine **Sperrzone** von der Länge des maximalen Label-Horizonts (Purging) plus 5 Handelstage (Embargo).
- Skalierung, Feature-Auswahl, Hyperparameter und Kalibrierung werden ausschliesslich im jeweiligen Trainingsfenster bestimmt (als eine Pipeline, die pro Fold neu gefittet wird).

### 4.2 Biases
| Risiko | Massnahme |
|---|---|
| Look-ahead | Ereignisgetriebener Simulator: Strategie sieht nur Daten mit `verfügbar_ab ≤ Entscheidungszeit`. Automatischer Test: Signale auf abgeschnittener Historie müssen identisch zu Signalen auf voller Historie sein (Dokument 07, T13). |
| Survivorship | V1-Universum besteht aus heute liquiden Titeln → **dokumentierter Bias**. Für ETFs/BTC/ETH klein, für Einzelaktien erheblich. Backtests von Einzelaktien tragen den Hinweis «Universum nach heutiger Auswahl»; G1 für Einzelaktien verlangt zusätzlich Bestehen auf dem ETF-Universum oder punkt-in-Zeit-Daten inkl. delisteter Titel (Datenanbieter-Frage, Dokument 03). |
| Auswahlbias | Jede getestete Variante wird im Experimentprotokoll gezählt (auch abgebrochene). Bewertung mit Deflated Sharpe Ratio (berücksichtigt Anzahl und Varianz der Versuche) und Probability of Backtest Overfitting (CSCV nach Bailey et al.). |
| Kostenoptimismus | Sensitivität: Kosten × 1.5 und × 2, Slippage +50 %, Einstieg eine Kerze verzögert. |
| Intrabar-Reihenfolge | Berühren Stop und Ziel dieselbe Kerze, gilt der Stop als zuerst erreicht (konservativ); bei verfügbaren Minutendaten wird damit aufgelöst. Sensitivitätslauf mit umgekehrter Annahme wird ausgewiesen. |
| Parameter-Überanpassung | Plateau-Test: alle Nachbarn ±20 % je Parameter müssen ≥ 50 % der Netto-Kennzahl erreichen. Maximal 3 freie Parameter je Strategie, maximal 50 Varianten pro Lauf. |
| Abhängige Beobachtungen | Trades werden zu **Clustern** zusammengefasst (zeitlich überlappend und Instrumente mit ρ > 0.7 im selben Fenster = ein Fall). Kennzahlen-Unsicherheit per Block-Bootstrap über Cluster. Wiederholte Simulatorläufe auf demselben Marktpfad zählen nicht als zusätzliche Evidenz. |
| LLM-Zukunftswissen | LLM-Ausgaben sind in historischen Tests **kein** Feature und kein Entscheidungsinput. LLM-gestützte Nachrichtenfeatures dürfen nur mit Forward-Daten ab ihrem Einführungszeitpunkt bewertet werden. |
| Reproduzierbarkeit | Jeder Lauf speichert: Datensatz-Snapshot (Hash), Code-Commit, Parameter, Zufalls-Seed, Kostenmodell-Version, Ergebnis. Wiederholung muss bitgleiche Signale liefern. |

### 4.3 Gates

Die Schwellen sind **begründete Startwerte**, als Konfiguration versioniert; Änderungen werden auditiert und gelten nicht rückwirkend für bereits bewertete Versionen.

**G1 – Historische Prüfung (Walk-forward auf Entwicklungsdaten)**
| Kriterium | Schwelle | Begründung |
|---|---|---|
| Netto-Erwartungswert je Trade | > 0 bei Basiskosten **und** bei Kosten × 1.5 | Vorteil darf nicht an der Kostenannahme hängen |
| Unabhängige Fälle | ≥ 100 Cluster über alle Testfenster | darunter ist das Konfidenzintervall des Erwartungswerts für diese Trade-Varianzen typischerweise breiter als der Effekt |
| Regime-Abdeckung | Testfenster enthalten ≥ 1 Stressphase und ≥ 1 Abwärtsphase des Leitmarkts (z. B. 2022) | sonst nur Schönwetter-Evidenz |
| Deflated Sharpe Ratio | Wahrscheinlichkeit ≥ 0.90 | Korrektur für Mehrfachtests |
| PBO | ≤ 0.25 | Auswahl aus Varianten nicht überwiegend Zufall |
| Plateau-Test | bestanden | keine Nadelspitzen-Parameter |
| Max. Drawdown im Test | ≤ 1.5 × konfiguriertes Drawdown-Limit | Strategie passt zur Risikopolicy |
| Gegen Baselines | besser als Cash; gegenüber exposure-gleichem Buy-and-Hold besseres Verhältnis Rendite/Max-Drawdown; besser als Zufallseinstiege (95 %-Perzentil) | ein Long-only-System im Bullenmarkt sieht sonst automatisch gut aus |

**G2 – Unabhängige Validierung (Holdout, einmalig)**
Netto-Erwartungswert > 0; Ergebnis liegt innerhalb des 80 %-Bootstrap-Intervalls der Walk-forward-Verteilung (kein Einbruch); ≥ 20 Cluster, sonst Status `ZU_WENIG_EVIDENZ` (kein Durchfallen, aber kein Bestehen).

**G3 – Forward-Paper/Shadow**
| Kriterium | Schwelle |
|---|---|
| Dauer | ≥ 8 Wochen **und** ≥ 25 Cluster (das Spätere gilt); bei Tagesstrategien realistisch 3–6 Monate |
| Konsistenz | kumuliertes Netto-Paper-Ergebnis nicht unter dem 10. Perzentil der aus dem Walk-forward erwarteten Verteilung für dieselbe Anzahl Trades |
| Signaltreue | Paper-Signale stimmen zu 100 % mit einem nachträglichen Replay derselben Version überein |
| Betrieb | keine ungelöste Abgleichs- oder Datenstörung; Simulator-Realismus-Karte ohne rote Punkte für die genutzten Ordertypen |

G3 ist ausdrücklich **kein statistischer Vorteilsbeweis** – 25 Fälle können das nicht leisten. Es prüft, ob die Forward-Realität der historischen Erwartung nicht widerspricht und ob der Betrieb stimmt. Die Statuskarte formuliert das so.

**G4 – Echtgeld-Freigabe:** meine Aktivierung eines Mandats nach bestandenem Live-Assistenten (Dokument 02, 3.2). Kein automatischer Übergang.

**G5 – Live-Pilot:** kleines Budget, ≥ 3 Monate und ≥ 25 Cluster. Überwacht: Ausführungsabweichung Live vs. Modell (Median-Slippage ≤ 2 × Modellannahme), Live-Ergebnis nicht unter dem 5. Perzentil der Erwartung, keine kritischen Betriebsvorfälle. Erst danach Budgeterhöhung – durch mich.

**Ablehnungsgründe (maschinenlesbar, auf der Statuskarte):** `NETTO_NEGATIV`, `KOSTENSENSITIV`, `ZU_WENIG_FAELLE`, `REGIME_NICHT_ABGEDECKT`, `UEBERANPASSUNG_DSR`, `UEBERANPASSUNG_PBO`, `KEIN_PLATEAU`, `DRAWDOWN_ZU_HOCH`, `SCHLECHTER_ALS_BASELINE`, `HOLDOUT_EINBRUCH`, `FORWARD_INKONSISTENT`, `BETRIEB_INSTABIL`.

## 5. Lernen

### 5.1 Lernwege in Reihenfolge
1. **Begrenzte Parameteroptimierung** (Etappe 3): Rastersuche/Optuna innerhalb der Parametergrenzen, ≤ 50 Varianten je Lauf, Bewertung nur über Walk-forward.
2. **Überwachtes Setup-Modell – Meta-Labeling** (Etappe 3): Die Regelstrategie erzeugt Kandidaten; ein Modell schätzt, ob ein Kandidat netto lohnt. Ziel: Netto-Ergebnis nach Kosten bei Exit gemäss Plan (Stop/Ziel/Zeitlimit) > 0. Start mit regularisierter logistischer Regression als Baseline, danach Gradient Boosting (LightGBM) mit flachen Bäumen. Das Modell darf nur **filtern oder verkleinern**, nie Positionen über die Regelgrösse hinaus vergrössern.
3. **Regime-Gewichtung** (Etappe 3, Should): Risikobudget je freigegebener Strategie nach Regime, aus wenigen diskreten Stufen (0 / 0.5 / 1).
4. **Reinforcement Learning** (Etappe 5, experimentell): erst wenn der Simulator gegen Live-Fills validiert ist und ≥ 12 Monate eigene Forward-Daten vorliegen. Bleibt im Lernlabor; Promotion nur über dieselben Gates.

### 5.2 Trainingsdaten
- Jede Entscheidung (auch NO TRADE und blockierte Signale) wird mit dem damaligen Feature-Snapshot gespeichert.
- Ein Beispiel wird erst trainierbar, wenn sein Zielhorizont abgeschlossen ist (`label_verfügbar_ab`).
- Drei getrennte Ergebnisarten: **beobachtet-live** (echte Fills), **beobachtet-paper** (simulierte Fills), **hypothetisch** (nicht ausgeführte Signale; Ergebnis aus Kursverlauf mit Kostenmodell). Hypothetische Labels sind gekennzeichnet; Auswertungen weisen sie getrennt aus.

### 5.3 Optimierungsziel
Netto-Erwartungswert je Cluster, bestraft durch Drawdown und Turnover:
`Ziel = mittlerer Netto-R je Cluster − λ₁ · MaxDrawdown(R) − λ₂ · Turnover-Kosten`, zusätzlich Nebenbedingung «≥ Mindestzahl Cluster». Trefferquote und Bruttogewinn sind keine Zielgrössen. «Nicht handeln» ist eine reguläre Ausgabe mit Ergebnis 0.

### 5.4 Kalibrierung und Wahrscheinlichkeitsanzeige
Eine Prozentwahrscheinlichkeit wird nur angezeigt, wenn auf Out-of-fold-Daten gilt: ≥ 300 gelabelte Fälle, Expected Calibration Error ≤ 0.05, Brier-Score besser als die Basisrate. Sonst Anzeige als Setup-Score mit Hinweis «nicht kalibriert».

### 5.5 Budgets und Abschluss
Je Lernlabor: max. Läufe pro Woche (Start: 2), max. Varianten pro Lauf (50), max. Rechenzeit pro Lauf (2 h), monatliches Kostenlimit. Jeder Lauf endet mit genau einem Ergebnis: `KANDIDAT`, `KEIN_BELASTBARER_FORTSCHRITT`, `ZU_WENIG_DATEN`, `ABGELEHNT` oder `ABGEBROCHEN`. Ein Champion bleibt, wenn der Herausforderer nicht in einem gepaarten Vergleich auf denselben Testfenstern besser ist (Differenz der Netto-R, Bootstrap-Intervall schliesst 0 aus).

### 5.6 Überwachung nach Freigabe
| Signal | Reaktion (Standardpolicy) |
|---|---|
| Feature-Drift (PSI > 0.25 auf ≥ 2 Kernfeatures) | Warnung; Modellfilter fällt auf Regelstrategie ohne Modell zurück, falls diese selbst freigegeben ist |
| Kalibrierung verschlechtert | Prozentanzeige aus, Score bleibt; Warnung |
| Live-Ergebnis < 5. Perzentil der Erwartung | Einstiege pausieren, Meldung «kritisch», meine Entscheidung |
| Median-Slippage > 2 × Modell über 20 Fills | Warnung, Positionsgrösse halbiert bis Bestätigung |
| Rücknahme auf Vorversion | nur neue Entscheidungen; offene Trades behalten Eigentümer und Exit-Plan |

## 6. Unsicherheiten, die bleiben
- Vergangene Robustheit garantiert keine künftige. Regime können sich so ändern, dass alle drei Kandidaten gleichzeitig versagen.
- Bei 25 Instrumenten und Swing-Frequenz entstehen unabhängige Fälle langsam; realistische Zeit bis G3 pro Version: Monate.
- Das Kostenmodell für Aktien stützt sich bis zum Live-Pilot auf Annahmen; erst G5 liefert echte Ausführungsdaten.
- Kleine Live-Budgets verzerren durch Mindestgebühren und ganze Stücke (Dokument 09, Abschnitt 3).
- Die belegten Kraken-Gebühren der untersten Stufe (0.8–1.6 % je Rundlauf) sind hoch genug, dass Crypto-Kandidaten allein daran scheitern können. Das ist ein gültiges Ergebnis, kein Anlass, das Kostenmodell zu schönen.
