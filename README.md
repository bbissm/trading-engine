# TradingEngine

Persönliche Handelsplattform für Aktien, ETFs und Crypto-Spot: Marktbeobachtung, nachvollziehbare Signale, Paper-Autopilot mit virtuellem Kapital, Lernlabor und – erst nach ausdrücklicher Aktivierung – begrenzter Live-Autopilot über eigene Konten. «TradingEngine» ist ein Arbeitstitel.

**Status (4. Oktober 2026):** Plan vollständig; erster vertikaler Schnitt umgesetzt – Signalbetrieb mit öffentlichen Kraken-Marktdaten, Strategie S1 als ungeprüfter Signalgeber, Dashboard zum Lesen. Es gibt keinen Orderweg, kein verbundenes Broker- oder Exchange-Konto und keinen Echtgeldhandel. Einrichtung: [docs/10-betrieb.md](docs/10-betrieb.md); Code: [`engine/`](engine/README.md), [`web/`](web/README.md).

Technische Funktion, belegte Strategiequalität und Echtgeld-Freigabe sind drei getrennte Dinge und werden getrennt abgenommen. Nichts in diesem Repository ist ein Gewinnversprechen.

## Projektplan

| Dokument | Inhalt | Auftragspunkt |
|---|---|---|
| [01 Produkt, Module, Zustände](docs/01-produkt-module-zustaende.md) | Produktbeschreibung, Abgrenzung V1/Zielprodukt, Module, Zustandsabläufe, Stopp-Aktionen | 1, 2 |
| [02 Screens, Abläufe, Funktionskatalog](docs/02-screens-ablaeufe-funktionskatalog.md) | Screens, Nutzerabläufe, Bedienbegriffe, Must/Should/Later | 3, 4 |
| [03 Anbieter und Daten](docs/03-anbieter-und-daten.md) | quellenbasierter Vergleich IBKR / Kraken / Alpaca, Datenquellen, Datenrechte, Startumfang | 5 |
| [04 Strategie, Lernen, Validierung](docs/04-strategie-lernen-validierung.md) | Features, Regime, Startstrategien, Bias-Schutz, Gates G1–G5, Lernwege | 8 |
| [05 Architektur und Datenmodell](docs/05-architektur-datenmodell.md) | Vercel vs. Engine-Server, Technologieentscheide, Lizenzen, fachliches Datenmodell | 6, 7 |
| [06 Risiko, Ausführung, Benachrichtigung](docs/06-risiko-ausfuehrung-benachrichtigung.md) | Risikopolicy, Orderweg, Schutzorders, Abgleich, Wiederherstellung, Alarme | 9 |
| [07 Abnahmetests](docs/07-abnahmetests.md) | 17 Pflichtszenarien + 13 zusätzliche Tests | Abschnitt 20 |
| [08 Etappen und Backlog](docs/08-etappen-backlog.md) | Etappen mit Aufwandsspannen, Epics und Tasks | 10, 11 |
| [09 Kosten, Annahmen, nächster Schritt](docs/09-kosten-annahmen-naechster-schritt.md) | laufende Kosten in Szenarien, Annahmen, offene Punkte, nächster Schritt | 12, 13, 14 |
| [10 Betrieb](docs/10-betrieb.md) | Einrichtung von Datenbank, Web-App und Engine | – |
| [11 Entscheide](docs/11-entscheide.md) | Abweichungen vom Plan während der Umsetzung, mit Begründung | – |

## Kernentscheide

- **Web-App auf Vercel** (Next.js 16, Tailwind 4, Drizzle, Neon Postgres – wie ContentEngine, CommerceEngine und das Control Center). **Engine (Python) ebenfalls auf Vercel** als eigenes Projekt mit Minuten-Cron, solange nur Signalbetrieb und Paper-Handel laufen; ein Dauerlauf-Server wird erst für den Live-Handel über Interactive Brokers nötig (docs/11, E-1).
- **Paper, Live und Lernlabor** sind getrennte Prozesse mit getrennten Zugangsdaten und Datenbankrollen.
- **Live-Kandidaten:** Interactive Brokers (Aktien/ETF), Kraken (Crypto-Spot). Paper-Phase mit kostenlosen Daten (Alpaca Paper-Only, Kraken öffentlich).
- **Eine Codebasis** für Strategie, Risikoprüfung und Order-Zustandsautomat in Backtest, Paper und Live.
- **Schutzorders beim Anbieter** als erste Linie; kein Dead-Man-Switch, der Schutzorders löscht.
