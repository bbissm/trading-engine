# 03 · Anbieter- und Datenvergleich

Recherchestand: **4. Oktober 2026**, offizielle Seiten der Anbieter. **V** = an diesem Tag auf der offiziellen Seite gelesen · **NV** = nicht verifiziert (Seite nicht ladbar, nur Sekundärquelle oder Erinnerung). Vor jeder Integration und vor jedem Abo erneut prüfen; Preise ändern sich.

Abkürzung `IB-DOCS` = `https://www.interactivebrokers.com/docs`.

## 1. Empfehlung in Kurzform

| Rolle | Wahl für V1 | Warum | Wichtigste Einschränkung |
|---|---|---|---|
| Aktien/ETF Live | **Interactive Brokers (IBKR Pro)** | einziger geprüfter Kandidat mit belegter Verfügbarkeit für Wohnsitz Schweiz; Bracket/OCA beim Broker; tiefe Gebühren | **wöchentliche manuelle 2FA-Anmeldung**, keine Bruchstücke per API, Paper nur mit kapitalisiertem Live-Konto |
| Crypto-Spot Live | **Kraken** | CHF-Einzahlung per SIC, Stop-Orders beim Anbieter, Schlüssel ohne Auszahlungsrecht, gute API | **kein OCO/Bracket per API**, hohe Gebühren in der untersten Stufe, kein Spot-Sandbox |
| Aktien-Marktdaten in der Paper-Phase | **Alpaca Paper-Only-Konto, Basic-Daten** | weltweit ohne Einzahlung eröffnbar, kostenlos | Echtzeit nur IEX (≈ 2.5 % des Volumens); vollständige SIP-Daten erst 15 min verzögert |
| Aktien-Adapter-Testintegration | Alpaca Paper-API, später IBKR Paper | echte API ohne Geld | Alpaca-Live für die Schweiz nicht belegt → nur Test und Daten |
| Crypto-Marktdaten | Kraken (öffentliche WebSocket-/REST-Daten + herunterladbare Historie) | gleiche Quelle wie späterer Handelsplatz | REST liefert nur die letzten 720 Kerzen |
| FX für CHF-Berichte | Frankfurter-API (EZB-Referenzkurse) für Tagesbewertung; Anbieterkurs für realisierte Umrechnungen | kostenlos | Tagesfixing, kein handelbarer Kurs |
| Eigener Paper-Simulator | **Eigenbau** (Dokument 05), nicht der Paper-Modus eines Anbieters | einheitlich für Aktien und Crypto, Episoden, dokumentierter Realismus | Modell bleibt Modell |

**Folge für den Start-Umfang:** Aktien/ETF-Strategien entscheiden in V1 auf **Tageskerzen** (Signal nach US-Börsenschluss, Order zur nächsten Sitzung). Dafür genügen die kostenlosen, vollständigen SIP-Tagesdaten; die 15-Minuten-Verzögerung ist irrelevant. Stündliche Aktiensignale folgen erst mit bezahlten Echtzeitdaten. Crypto entscheidet auf 4h- und Tageskerzen.

## 2. Vergleichsmatrix

### 2.1 Instrumente, Wohnsitz, Konto

| | Interactive Brokers | Kraken | Alpaca |
|---|---|---|---|
| Wohnsitz Schweiz | **V:** Schweiz in der Länderliste (interactivebrokers.com/en/accounts/open-account-country-list.php) | **NV explizit.** Global Terms (Stand 1.10.2026) nennen Payward Trading Ltd (BVI) als Vertragspartner «anywhere else in the world where we make our services available»; die Schweiz ist nicht namentlich genannt. CHF-Einzahlung per SIC «Switzerland and Liechtenstein only» ist dokumentiert (V) | Paper-Only: **V** «Anyone globally» (docs.alpaca.markets/docs/paper-trading). Live: «195+ countries», aber «not every jurisdiction»; Schweiz **NV** |
| Zuständige Gesellschaft für CH | NV (vermutlich IBKR UK als Vermittler zu IB LLC) | siehe oben | – |
| Instrumente | Aktien/ETFs weltweit inkl. SIX, XETRA (V: Gebührentabellen) | Crypto-Spot; XBT/CHF, ETH/CHF, USDT/CHF, USDC/CHF online (V, AssetPairs) | US-Aktien/ETFs, Crypto in «select jurisdictions» |
| US-ETFs für CH-Privatkunden | **NV.** PRIIPs-Einschränkung gilt laut IBKR-Hinweis für «EEA and UK retail clients»; für die Schweiz bei IBKR zu bestätigen | – | – |
| Kontovoraussetzung API | IBKR **Pro** (Lite nur US-Residents); API-Marktdaten erfordern USD 500 Eigenkapital (V) | verifiziertes Konto | E-Mail (Paper) |
| Bruchstücke | **per API nicht** («APIs do not support fractional or cash quantity trading») (V) | bis 8 Nachkommastellen | ja, aber nicht mit Bracket/OCO |

### 2.2 API-Betrieb und Authentifizierung

| | Interactive Brokers | Kraken | Alpaca |
|---|---|---|---|
| Zugang | TWS-API über **IB Gateway** (Socket); Client-Portal-Web-API | REST + WebSocket v2 | REST + WebSocket |
| Authentifizierung | Benutzername + **2FA zwingend**; OAuth für Einzelkonten nicht verfügbar (V) | API-Schlüssel mit HMAC-Signatur und Nonce | API-Schlüssel |
| Betrieb ohne Bedienung | **eingeschränkt.** «headless session … is not supported»; Auto-Restart täglich möglich, aber **einmal pro Woche manuelle Anmeldung** (Token verfällt Sonntag 01:00 ET; IBKR kann auch ausserplanmässig widerrufen) (V, IB-DOCS/tws-api/doc/tws-settings/daily-weekly-reauthentication; ibkrguides.com/traderworkstation/auto-restart-considerations.htm) | ja; Schlüssel optional mit IP-Allowlist und Ablaufdatum (V) | ja |
| Web-API-Alternative | schlechter: tägliche Neuanmeldung im Browser, Timeout nach ≈ 6 min ohne `/tickle` (V) | – | – |
| Sitzungskonflikte | eine Handelssitzung pro Benutzername → **zweiten Benutzernamen** für die Engine anlegen (V) | – | – |
| Rechte | «Read-Only API» muss für Handel deaktiviert sein; keine Auszahlung über die API vorgesehen | granular: Handeln/Abfragen getrennt von **Withdraw** (V, docs.kraken.com/exchange/guides/rest/api-keys) | Handel |
| Hilfswerkzeuge | IBC / Docker-Gateway-Images automatisieren die Anmeldemaske – Drittsoftware, von IBKR nicht unterstützt (NV) | – | – |

**Konsequenz IBKR:** Die wöchentliche Anmeldung ist eine reale, bleibende Aufgabe. Das Produkt behandelt sie offen: Erinnerung am Samstag, Statusanzeige «Sitzung gültig bis …», bei abgelaufener Sitzung Zustand WIEDERHERSTELLUNG (keine Einstiege); der Schutz hängt in dieser Zeit ausschliesslich an den beim Broker liegenden Orders.

### 2.3 Ordertypen, Schutz, Status

| | Interactive Brokers | Kraken Spot | Alpaca |
|---|---|---|---|
| Basis | Market, Limit, Stop, Stop-Limit, Trailing (V) | market, limit, stop-loss, take-profit, je auch -limit, trailing-stop(-limit), iceberg (V, docs.kraken.com/api/docs/rest-api/add-order) | Market, Limit, Stop, Stop-Limit, Trailing |
| Bracket / OCO | **ja:** Bracket über `parentId`, OCA-Gruppen; bei Teilfüllung «re-balances» die Gruppe (V) | **nein per API.** Conditional Close erlaubt genau eine Folgeorder; «not possible to place … One-Cancels-the-Other type orders» (V, support.kraken.com/articles/360038640052). OCO existiert nur in der Kraken-Pro-Oberfläche | Bracket, OCO, OTO – nur ganze Stücke, nur reguläre Handelszeit |
| Teilfüllung + Schutz | Verhalten der Bracket-Kinder bei Teilfüllung des Einstiegs NV → in Paper testen | jede Teilfüllung erzeugt eine **eigene** Folgeorder (V) → wir setzen Stops selbst | – |
| Stop liegt beim Anbieter? | je Börse nativ oder **von IBKR simuliert**; simulierte hängen von Marktdaten ab und werden während des täglichen Resets verzögert (V) | ja; Auslöser `last` oder `index` | ja |
| Ausserhalb regulärer Handelszeit | einfache Stop-Orders wirken **nicht** ausserhalb RTH; Stop-Limit kann (V, IBKR Campus) | 24/7 | Trailing nur RTH |
| Trockenlauf | Paper-Konto | `validate=true` prüft ohne Platzierung (V) | Paper-Konto |
| Order-Kennung | `permId` kontoweit eindeutig; `orderRef` bleibt über die Lebensdauer erhalten (laut Referenz «intended for institutional customers» → in Paper bestätigen) (V) | `cl_ord_id` (UUID oder ≤ 18 Zeichen); eindeutig nur unter offenen Orders; Ablehnung von Duplikaten **nicht** dokumentiert (V) | `client_order_id` |
| Statusklärung | `reqAllOpenOrders`, `reqCompletedOrders`, `reqPositions`; `reqExecutions` am Gateway **nur aktueller Tag** → Fills selbst speichern; `orderStatus` kommt oft doppelt (V) | QueryOrders/OpenOrders/ClosedOrders nach `cl_ord_id`; WS-Kanal `executions` mit Snapshot (V) | REST + Stream |
| Dead-Man-Switch | – | `CancelAllOrdersAfter` storniert **alle** Orders des Kontos und löst auch bei Rückkehr aus Wartung aus (V) → in V1 nicht verwendet | – |

**Nicht unterstützt und in der App so angezeigt:** Kraken – Stop und Ziel gleichzeitig beim Anbieter; IBKR – Bruchstücke per API, Stop-Schutz ausserhalb der regulären Handelszeit. Diese Funktionen werden nicht als echte Orders vorgetäuscht.

### 2.4 Mindestgrössen, Gebühren, FX (Preise wie am 4.10.2026 angezeigt)

| | Interactive Brokers | Kraken | Alpaca |
|---|---|---|---|
| Handelsgebühr | US-Aktien/ETFs **Fixed** USD 0.005/Aktie, min. USD 1.00, max. 1 %; **Tiered** USD 0.0035/Aktie, min. USD 0.35 + Börsen-/Regulierungsgebühren. SIX: 0.05 %, min. CHF 1.50 (Tiered) / CHF 5 (Fixed). XETRA: 0.05 %, min. EUR 1.25 / EUR 3 (V, …/en/pricing/commissions-stocks.php) | Kraken Pro Spot, unterste Stufe: **0.40 % Maker / 0.80 % Taker**; ab USD 2 500 30-Tage-Volumen 0.30/0.60; ab USD 10 000 0.22/0.38. Stablecoin-/FX-Paare 0.20 % (V, kraken.com/features/fee-schedule) – eigene Stufe über den Endpunkt TradeVolume prüfen | Aktien kommissionsfrei; Crypto 15–25 bp |
| Mindestgrösse | 1 Aktie per API | XBT 0.00005, ETH 0.001; Mindestwert 0.5 (V, AssetPairs) | USD 1 (Bruchstücke) |
| FX | manuell 0.20 bp, min. USD 2; automatische Umrechnung ≈ 0.03 % Aufschlag (V) | CHF→Stablecoin über USDT/CHF bzw. USDC/CHF zu 0.20 % | nur USD-Finanzierung |
| Kontogebühr | keine Mindesteinlage, keine Inaktivitätsgebühr (V) | – | – |
| Einzahlung | – | CHF per SIC kostenlos, min. CHF 1 (V) | – |

**Kraken-Liquidität nach Quote-Währung** (V, Ticker 4.10.2026, 24 h): BTC/USD 783 BTC · BTC/EUR 227 BTC · **BTC/CHF 8.4 BTC**; ETH/USD 11 741 ETH · ETH/CHF 17 ETH. → CHF-Paare sind für automatischen Handel zu dünn. V1 handelt **USD-Paare**; das Quote-Währungsrisiko (USD gegenüber CHF) wird in der CHF-Bewertung als FX-Effekt ausgewiesen. Der Weg CHF → USD auf Kraken ist noch zu klären (offener Punkt O-5).

### 2.5 Marktdaten

| | Interactive Brokers | Kraken | Alpaca |
|---|---|---|---|
| Echtzeit per API | kostenpflichtig («off-platform»): Network A/B/C je USD 1.50/Monat, Cboe One USD 1.00, Snapshot-Bundle USD 10 (erlassen ab USD 30 Kommission), Streaming-Zusatz USD 4.50 (V, …/pricing/market-data-pricing.php). Status muss selbst auf **Non-Professional** gestellt werden (V) | öffentliche WebSocket-Kanäle book, ticker, trade, ohlc (V) | **Basic kostenlos:** IEX in Echtzeit, SIP nur älter als 15 min, 30 WebSocket-Symbole, 200 Abrufe/min. **Algo Trader Plus USD 99/Monat:** volle SIP-Echtzeit (V, alpaca.markets/data) |
| Verzögert kostenlos | 15–20 min per `reqMarketDataType(3)` (V) | – | siehe Basic |
| Historie | Kerzen ≤ 30 s nur 6 Monate; **keine Daten delisteter Titel**; Pacing-Regeln (V) | REST: nur letzte 720 Kerzen (V). **Download-Archive** OHLCVT (1 min–1 Tag) ab Marktstart bis 30.6.2026, quartalsweise ergänzt; Kerzen ohne Trades fehlen (V) | seit 2016 (V) |
| Abo-Verfall | Marktdaten-Abos verfallen ohne TWS-Anmeldung innert 60 Tagen; ob Gateway-Anmeldung zählt, ist nicht dokumentiert (V/NV) | – | – |
| Paper-Simulation des Anbieters | Fills vom Top-of-Book; «Stops and other complex order types are always simulated»; Startkapital USD 1 Mio.; braucht kapitalisiertes Live-Konto (V) | **kein Spot-Sandbox** für normale Kunden (V) | Fills gegen NBBO ohne Liquiditätsprüfung, 10 % zufällige Teilfüllungen; nicht simuliert: Marktwirkung, Latenz, Warteschlange, Dividenden (V) |

### 2.6 Limits, Wartung, Ausfälle

| | Interactive Brokers | Kraken |
|---|---|---|
| API-Limits | 50 Anfragen/s; Historie max. 60 Anfragen/10 min (V) | REST-Zähler je Schlüssel (Starter: max. 15, Abbau 0.33/s); Handelslimit je Paar; Storno junger Orders kostet mehr Punkte; öffentliche Endpunkte ≈ 1/s (V) |
| Wartung | täglicher Reset: Nordamerika 00:15–01:45 ET, Europa 06:25–07:45 MEZ; native Orders arbeiten weiter, Ausführungsberichte und simulierte Orders verzögert (V) | Modi `maintenance` (weder neue Orders noch **Stornos**), `cancel_only`, `post_only`, `limit_only`; in keinem davon wird gematcht → Stops können währenddessen nicht auslösen (V). Geplante Wartungen auf status.kraken.com |
| Wiederverbindung | Auto-Restart, danach Abgleich nötig | WS: nach Wartung höchstens alle 5 s neu verbinden; ≈ 150 Verbindungsversuche/10 min (V) |

### 2.7 Datenrechte (maschinelle Auswertung, Speicherung, Training)

| Anbieter | Befund |
|---|---|
| IBKR | **NV.** Abonnentenverträge nur im Kundenportal einsehbar. Belegt ist nur: Kategorie «Non-Display (API trading applications)» im Abo-Dialog und dass Non-Professional rein private Nutzung voraussetzt. |
| Kraken | **NV.** Global Terms untersagen Scraping; keine ausdrückliche Klausel zu Speicherung/ML gefunden. Private Nutzung der öffentlichen API-Daten für den eigenen Handel erscheint üblich, ist aber nicht schriftlich belegt. |
| Alpaca | **NV.** AGB-PDF nicht lesbar; ein Support-Hinweis nennt persönliche, nicht-kommerzielle Nutzung ohne Weiterverbreitung. |
| TradingView | Standardlizenz beschränkt Non-Display-Nutzung → **keine** TradingView-Daten, -Alerts oder -Webhooks als Handelsinput. Verwendet wird nur die Open-Source-Chartbibliothek `lightweight-charts` (Apache-2.0, V) mit eigenen Daten. |

Für alle drei gilt: **Klauseln zu Speicherung, Ableitungen und Kündigung vor dem jeweiligen Abo selbst lesen** (offener Punkt O-4). Die Architektur speichert Daten nur privat, verbreitet nichts weiter und kann einen Feed je Anbieter löschen.

## 3. Zusätzliche Datenquellen für US-Aktien (nur bei Bedarf)

| Anbieter | Privattarife (V, 4.10.2026) | Minutenhistorie | Delistete Titel | Adjustierung | Bemerkung |
|---|---|---|---|---|---|
| Massive (früher Polygon.io) | USD 0 / 29 / 79 / 199 | 2 / 5 / 10 / 20+ Jahre | ja | Split-adjustiert, **nicht** dividendenadjustiert | Echtzeit nur im 199er-Tarif |
| EODHD | USD 19.99–99.99 | 1 min seit 2004 (ab 29.99) | ja (Intraday nur für Delistings ab 2021) | Intraday unadjustiert | günstigste Quelle für Survivorship-Prüfung |
| Tiingo | USD 0 / 30 | IEX-basiert | NV | Tagesdaten adjustiert, 30+ Jahre | «internal use» |
| Databento | USD 199/Monat inkl. Live; USD 125 Startguthaben | EQUS.MINI ab März 2023 | NV | NV | ausdrücklich Non-Display erlaubt (laut Anbieter-Blog) |
| Twelve Data | USD 0 / 79 / 229 | NV | NV | NV | – |
| FMP | Preise NV (Seite nicht ladbar) | – | – | – | – |

Earnings-Kalender: Finnhub (Gratisstufe, Bedingungen NV), EODHD/FMP in Bezahltarifen (NV). → Task E1-6 beginnt mit einer Bedingungsprüfung; bis dahin gilt das Earnings-Sperrfenster als «nicht verfügbar» und Einzelaktien-Signale tragen den Hinweis.

**Survivorship:** Weder Alpaca-Historie (ab 2016) noch IBKR liefern delistete Titel verlässlich. Für das ETF-/Large-Cap-Startuniversum wird der Bias dokumentiert (Dokument 04, 4.2). Sobald Einzelaktien-Strategien G1 anstreben, ist EODHD (≈ USD 30) oder Massive Developer (USD 79) für einen Monat der pragmatische Weg, punkt-in-Zeit-Universen zu prüfen.

## 4. FX-Kurse
- Frankfurter-API: kostenlos, ohne Schlüssel, Tageskurse auf Basis der EZB-Referenzkurse, CHF enthalten (V, frankfurter.dev). Die EZB veröffentlicht ca. 16:00 MEZ «for information purposes only» (V). → Tagesbewertung und Berichte.
- SNB-Datenportal: NV (nicht ladbar).
- Realisierte Umrechnungen: tatsächlicher Kurs des Anbieters. Für die Steuererklärung massgebliche Jahresendkurse (ESTV-Kursliste): NV, selbst zu prüfen.

## 5. Referenzprodukte aus dem Auftrag (Inspiration, keine Abhängigkeit)

| Referenz | Übernommen | Bewusst nicht übernommen |
|---|---|---|
| TrendSpider | Statuskarten/Gates, Forward-Test vor Freigabe | ML-Signale als Qualitätsversprechen |
| Capitalise.ai | einfache, lesbare Regelbeschreibung je Strategie | Regeldefinition per Freitext als Orderweg |
| QuantConnect | klare Stufen Forschung → Paper → Live, dokumentierte Fill-Annahmen (Realismus-Karte) | Plattformbindung |
| Freqtrade/FreqAI | Dry-/Live-Trennung, periodisches Nachtraining mit Grenzen, Look-ahead-Prüfung als automatischer Test | GPL-Code-Einbettung, Demo-Strategien |
| FreqAI-RL | nur als spätere Laboroption | RL in V1 |
| 3Commas | sichtbare Demo/Live-Trennung | Perpetual-Demo als Spot-Massstab |
