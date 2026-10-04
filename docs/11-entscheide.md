# 11 · Entscheide während der Umsetzung

Abweichungen vom ursprünglichen Plan (docs/01–09) mit Begründung. Wo sich Plan und dieses Dokument
widersprechen, gilt dieses Dokument.

## E-1 · Engine läuft auf Vercel (Minuten-Cron), nicht auf einem eigenen Server (4.10.2026)

**Plan:** kleiner Dauerlauf-Server für die Engine ab Etappe 0.
**Entscheid:** Die Engine ist ein eigenes Vercel-Projekt `trading-engine-worker` (Root `engine/`). Ein Cron
ruft jede Minute `api/tick.py` auf; jeder Aufruf ist zustandslos (Befehle, fällige Kerzen, Entscheidungen,
Heartbeat). Was offen ist, wird aus der Datenbank abgeleitet.

**Warum das genügt:** Signalbetrieb und Paper-Handel entscheiden auf abgeschlossenen 4h-/Tageskerzen;
eine Reaktionszeit von bis zu einer Minute ist dafür unerheblich. Es entfallen Serverkosten, Wartung und
die Abhängigkeit von einem lokalen Rechner. So arbeiten auch ContentEngine und CommerceEngine.

**Grenzen (bleiben aus docs/05 gültig):**
- **Live-Handel über Interactive Brokers** braucht das IB Gateway – ein dauerhaft laufendes Programm mit
  Anmeldesitzung. Das kann Vercel nicht hosten; dafür wird in Etappe 4 ein Server nötig.
- **Live-Handel über Kraken** ist per Minuten-Cron denkbar, weil der Stop-Loss beim Handelsplatz liegt und
  REST-Aufrufe reichen. Ob eine Reaktionszeit von bis zu 60 s für Orderabgleich und Schutzreparatur
  akzeptabel ist, wird vor Etappe 4 bewusst entschieden – nicht stillschweigend.
- Bedienbefehle aus der Web-App werden innert höchstens einer Minute quittiert (nicht Sekunden).
- Krakens öffentliche API begrenzt Abrufe je IP; Vercel-Funktionen teilen sich Ausgangs-IPs. Die Engine
  ruft deshalb nur ab, wenn eine neue Kerze fällig ist. Abruffehler erscheinen als Feed-Status `ERROR`.

`docker-compose.yml` und `tradingengine run` bleiben für lokalen Betrieb und einen späteren Server erhalten.

## E-2 · Migrationen laufen im Vercel-Build (4.10.2026)

**Plan:** Migrationen als eigener Schritt ausserhalb des Builds.
**Entscheid:** `pnpm vercel-build` = Migration → Engine-Datenbankrolle → `next build`.
**Grund:** Die Neon-Zugangsdaten sind auf Vercel als «sensitive» hinterlegt und ausserhalb von Vercel nicht
lesbar. Schutz bleibt: nur additive Schemaänderungen, und die Engine verweigert den Start bei falscher
`schema_meta.version`. Die Engine nutzt die Rolle `te_engine` mit minimalen Rechten (Signale, Kerzen, Audit
nur einfügbar).

## E-3 · Simulationskern als Eigenbau, ohne praktischen NautilusTrader-Spike (4.10.2026)

**Plan (E2-0):** zweitägiger Spike mit NautilusTrader vor dem Bau.
**Entscheid:** Eigenbau; der Spike wurde **nicht** durchgeführt. Die Entscheidung stützt sich auf
Architekturgründe, nicht auf einen Praxisvergleich:
- Der Betrieb ist jetzt zustandslos pro Cron-Aufruf (E-1). NautilusTrader ist als dauerhaft laufender,
  zustandsbehafteter Knoten mit eigenem Ereignisbus gebaut – das passt nicht zu diesem Modell.
- Kontobuch mit Reservierung, Episoden, Mandaten und append-only Journal müssten um das fremde
  Zustandsmodell herumgebaut werden.
- Der eigene Kern ist klein (Orders/Fills, PnL, Kosten, Grösse, Risiko, Simulator, Exit-Plan: einige hundert
  Zeilen reine Funktionen) und vollständig durch die Abnahmetests aus docs/07 abgedeckt.

Offen bleibt die Neubewertung, falls später ein Dauerlauf-Server mit Intraday-Strategien entsteht.

## E-4 · Stress-Regel des Regimes (4.10.2026)
Siehe docs/04, Abschnitt 1.3: «Rückgang über 5 Tage > 4 × ATR» statt «Abstand zum 60-Tage-Hoch > 2.5 × ATR»,
geändert vor jeder Auswertung echter Daten.

## E-5 · Wartezeit nach Verlustserie ist zeitlich begrenzt (4.10.2026)
Der erste Backtest-Lauf zeigte, dass eine Verlustserie Einstiege dauerhaft blockierte (die Serie konnte nur
durch einen Gewinn enden, der ohne Einstieg nie kommt). Umgesetzt wie in docs/06 beschrieben: 24 h Wartezeit
ab dem letzten Verlust, danach beginnt die Serie neu (`RiskPolicy.loss_streak_cooldown_hours`).

## Erste Backtest-Läufe (nur Plausibilitätsprüfung der Software)

Mit 720 echten Kraken-Tageskerzen je Instrument lieferten die drei Strategien 0–6 Trades je Instrument,
überwiegend mit negativem Nettoergebnis nach den belegten Kraken-Gebühren; S3 erzeugte keinen einzigen
Trade. Das ist **kein Urteil über die Strategien** – für jedes Gate aus docs/04 sind es viel zu wenige
Fälle – aber es bestätigt die Erwartung aus docs/09: Die Gebühren sind die grösste Hürde, und «zu wenig
Evidenz» ist der realistische Zwischenstand.

## E-6 · Paper-Handel wird je abgeschlossener 4h-Kerze fortgeschrieben (4.10.2026)

Der Paper-Autopilot läuft im Minuten-Tick, die Simulation selbst schreitet aber in 4h-Kerzen voran
(auch für Trades aus Tagessignalen – feinere Daten als die Signal-Zeitebene):

- Ein Stop «löst» im Modell erst bei Kerzenschluss aus; der Ausführungspreis folgt trotzdem der Stop-Regel
  (schlechterer aus Stop und Eröffnung, abzüglich Slippage). Die Realismus-Karte im Paper-Lab weist das aus.
- Eine Order nimmt an einer Kerze teil, wenn sie höchstens 5 Minuten nach deren Beginn erteilt wurde
  (Signal kurz nach Kerzenschluss → Order in der Folgekerze). Später erteilte Orders – etwa «Positionen jetzt
  schliessen» mitten in einer Kerze – werden erst in der nächsten Kerze ausgeführt.
- Jeder Kerzenschritt wird genau einmal verbucht (Zeiger `episode.sim_through`, eine Transaktion je Tick).
  Nach einem Ausfall holt die Engine die verpassten Kerzen in Reihenfolge nach: Stops und Ausstiege werden
  nachvollzogen, verpasste Signale aber nicht nachträglich gehandelt.
- Bei Erreichen einer Verlustgrenze blockiert die Risikoprüfung neue Einstiege mit Grund; ein automatischer
  Zustandswechsel des Autopiloten auf «Einstiege pausiert» samt kritischer Meldung folgt mit den
  Benachrichtigungen (E2-9).

Noch offen in Etappe 2: Benachrichtigungen (E2-9), Journal mit CHF-Bewertung und Exporten (E2-10/E2-12),
laufender Verlustgrenzen-Wächter mit Zustandswechsel, Backtest-Oberfläche mit Baselines und Sensitivitäten.
