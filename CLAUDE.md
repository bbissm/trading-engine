# Projekt-Notizen

- Plan: `docs/01`–`09` (Produkt, Screens, Anbieter, Strategie/Validierung, Architektur, Risiko/Ausführung, Abnahmetests, Backlog, Kosten/offene Punkte). Vor grösseren Änderungen das passende Dokument lesen und bei Abweichungen anpassen.
- Aufbau: `web/` (Next.js 16, Tailwind 4, Drizzle, Vitest; Vercel, Region fra1) · `engine/` (Python, uv; Dauerlauf-Server per Docker Compose).
- **Harte Regeln**
  - Kein Echtgeldhandel ohne ausdrückliche Aktivierung durch den Nutzer. Es gibt derzeit keinen Orderweg; Handels-Zugangsdaten gehören nie ins Repo, in die Web-App, in Logs oder Meldungen.
  - Paper, Live und Forschung bleiben getrennt (Prozesse, Datenbankrollen, `mode` an jedem Datensatz). Live- und Paper-Beträge nie addieren.
  - Entscheidungen nur auf abgeschlossenen Kerzen. `signal` ist append-only; alte Kerzen erhalten nie nachträglich ein Signal.
  - Keine Erfolgs- oder Gewinnbehauptungen in UI, Texten oder Tests. Setup-Score ist keine Wahrscheinlichkeit.
  - Die Web-App ruft die Engine nie direkt auf: Bedienhandlungen gehen über die Tabelle `command`.
- **Schema:** Hoheit bei `web/src/db/schema.ts` (Drizzle). Nur additive Änderungen. Bei jeder Migration `SCHEMA_VERSION` in `web/src/db/schema.ts` **und** `engine/src/tradingengine/schema_version.py` erhöhen und `schema_meta` in der Migration aktualisieren. Migrationen laufen **nicht** im Build: `pnpm db:generate`, dann `pnpm db:migrate` als eigener Schritt vor dem Ausrollen.
- **Engine-Struktur:** `core/` reine Domänenlogik ohne I/O (Indikatoren, Regime, Strategien – dieselbe Logik für Signalbetrieb, Backtest, Paper, Live) · `ports.py` Schnittstellen · `adapters/` Kraken, Postgres, In-Memory · `services/` Abläufe · `cli.py`.
  - Strategie- und Regimeregeln sind versioniert (`s1-trend-pullback@1`, `regime@1`). Jede Änderung an Regeln oder Parametern ist eine neue Version, nie eine Bearbeitung der bestehenden.
  - Indikatoren müssen kausal bleiben (Wert an Index i nutzt nur Daten ≤ i); der Abschneidetest `test_t13_1_truncation_no_lookahead` darf nicht aufgeweicht werden.
- **Checks vor jedem Push**
  - Engine: `cd engine && uv run ruff check . && uv run mypy && uv run pytest -q` (mit `TE_TEST_DATABASE_URL` auf eine leere Wegwerf-Datenbank laufen auch die Postgres-Vertragstests).
  - Web: `cd web && pnpm typecheck && pnpm lint && pnpm test && pnpm build`.
- Web-Konventionen folgen den Nachbarprojekten `../dashboard` und `../commerce-engine` (Mobile-Shell, Tokens, deutsche Oberfläche mit «ss»). Hinweis in `web/AGENTS.md` zu Next.js 16 beachten, falls vorhanden.
- Beträge und Preise: `numeric`/`Decimal`, nie Float. Zeit: UTC in der Datenbank, Anzeige Europe/Zurich.
