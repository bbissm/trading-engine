# TradingEngine – Engine

Dauerlauf-Prozess für Marktdaten, Analyse und Signalbetrieb. **Stand: erster vertikaler Schnitt.**
Es gibt keinen Orderweg, keinen Simulator und keine Handels-Zugangsdaten. Signale sind ungeprüfte
Forschungsausgaben einer Strategie ohne Qualitätsnachweis.

## Was der Prozess tut

Alle 60 s:
1. Abgeschlossene 4h- und Tageskerzen für das Universum (`universe.py`: BTC/USD, ETH/USD) von Krakens
   öffentlicher REST-Schnittstelle holen; nur neue Kerzen speichern.
2. Datenqualität je Instrument und Zeitebene bewerten: `OK`, `STALE`, `GAP`, `ERROR`.
3. Regime (`regime@1`) und Features je Kerze speichern.
4. Für die zuletzt abgeschlossene, noch gültige Kerze die Entscheidung der Strategie
   `s1-trend-pullback@1` ins Signaljournal schreiben – auch `NO_TRADE` mit Grund. Bei nicht einwandfreiem
   Feed oder fehlender fälliger Tageskerze wird gewartet; abgelaufene Kerzen erhalten kein Signal.

Alle 2 s: Befehle aus der Tabelle `command` verarbeiten (derzeit nur `PING`). Alle 30 s: Heartbeat in
die Datenbank und optional an einen externen Heartbeat-Dienst.

## Betrieb

```
uv sync
DATABASE_URL=postgresql://… uv run tradingengine once   # ein Durchlauf
DATABASE_URL=postgresql://… uv run tradingengine run    # Dauerlauf
```

| Variable | Zweck |
|---|---|
| `DATABASE_URL` | Postgres mit angewandten Migrationen aus `web/drizzle` (Pflicht) |
| `HEALTHCHECK_URL` | Ping-URL eines externen Heartbeat-Dienstes (optional, empfohlen) |

Die Engine startet nicht, wenn `schema_meta.version` nicht zu `schema_version.py` passt.

Tiefere Historie als die 720 Kerzen der REST-Schnittstelle: Kraken-OHLCVT-Archiv herunterladen und
`uv run tradingengine import-archive "KRAKEN:BTC/USD" 4h XBTUSD_240.csv`.

## Tests

```
uv run ruff check . && uv run mypy && uv run pytest -q
```

Mit `TE_TEST_DATABASE_URL` (leere Wegwerf-Datenbank – das Schema `public` wird gelöscht!) laufen
zusätzlich die Vertragstests gegen Postgres und die Drizzle-Migrationen.

Abgedeckt aus `docs/07-abnahmetests.md`: T13.1 (Abschneidetest), T13.2 (kein Signal vor Kerzenschluss),
Vorstufen von T4 (keine doppelte Entscheidung), T8 (Feed-Sperre) und T13.5 (Replay).

## Bekannte Grenzen dieses Schnitts

- Daten per REST-Abfrage im Minutentakt; WebSocket-Streams und Quotes (Bid/Ask) folgen mit dem Simulator.
- Kein Kostenmodell: BUY-Signale tragen den Gegenfaktor «Kosten … noch nicht geprüft».
- Ein Prozess statt getrennter Dienste; die Trennung Paper/Live/Lab entsteht mit den jeweiligen Etappen.
- Kraken liefert für Intervalle ohne Trades keine Kerze; bei BTC/USD und ETH/USD praktisch nie der Fall,
  bei illiquiden Paaren würde der Feed als `GAP` gesperrt.
