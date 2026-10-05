-- Datenbankrollen der Engine (minimale Rechte, docs/05 3.1). Wird von scripts/roles.mjs im Vercel-Build und von den
-- Engine-Tests (engine/tests/test_roles_pg.py) ausgeführt. Idempotent. Anweisungen sind durch «-- @@» getrennt.
--
--   te_engine  Signalbetrieb, Paper-Autopilot, Wächter, Meldungen, Lernlabor (Vercel-Projekt trading-engine-worker)
--   te_live    ausschliesslich der Live-Autopilot (eigenes Vercel-Projekt trading-engine-live)
--
-- Beide lesen alles ausser den Anmeldetabellen. Geschrieben wird nur, was der jeweilige Prozess braucht; bei Konten,
-- Orders, Fills, Trades, Reservierungen, Autopilot-Zuständen, Eigenkapital und Signal-Ergebnissen erzwingt
-- Row-Level-Security den Modus: te_engine nur PAPER, te_live nur LIVE. Der Tabelleneigentümer (Web-App, Migrationen)
-- ist davon nicht betroffen.

do $$ begin
  if not exists (select from pg_roles where rolname = 'te_engine') then
    create role te_engine login password '{{ENGINE_PASSWORD}}';
  else
    alter role te_engine login password '{{ENGINE_PASSWORD}}';
  end if;
  if not exists (select from pg_roles where rolname = 'te_live') then
    create role te_live login password '{{LIVE_PASSWORD}}';
  else
    alter role te_live login password '{{LIVE_PASSWORD}}';
  end if;
end $$
-- @@
-- Hilfsfunktionen für die Policies (security definer: lesen den Modus unabhängig von den Rechten des Aufrufers)
create or replace function te_account_mode(p_account_id text) returns text language sql stable security definer set search_path = public as
$$ select mode from account where id = p_account_id $$
-- @@
create or replace function te_order_mode(p_order_id text) returns text language sql stable security definer set search_path = public as
$$ select mode from trade_order where id = p_order_id $$
-- @@
revoke all on function te_account_mode(text) from public
-- @@
revoke all on function te_order_mode(text) from public
-- @@
grant execute on function te_account_mode(text) to te_engine, te_live
-- @@
grant execute on function te_order_mode(text) to te_engine, te_live
-- @@
grant usage on schema public to te_engine, te_live
-- @@
revoke all on all tables in schema public from te_engine, te_live
-- @@
grant select on all tables in schema public to te_engine, te_live
-- @@
grant usage, select on all sequences in schema public to te_engine, te_live
-- @@
-- Anmeldetabellen (Passwort-Hash, TOTP, Sitzungen): nicht einmal lesbar
revoke all privileges on auth_user, auth_session, auth_account, auth_verification, auth_two_factor, auth_passkey, auth_rate_limit
  from te_engine, te_live
-- @@
-- ───────── te_engine: Signale, Paper, Meldungen, Lernlabor ─────────
grant insert, update on instrument, feed_status, heartbeat, fx_rate, setting, alert, channel_status, experiment to te_engine
-- @@
grant insert on strategy_version, candle, feature_snapshot, signal, audit_event, command, alert_delivery, experiment_trial,
  gate_evaluation, holdout_access, approval to te_engine
-- @@
grant insert on account, fill, equity_snapshot, signal_outcome to te_engine
-- @@
grant insert, update on episode, autopilot, trade, trade_order to te_engine
-- @@
grant insert, update, delete on reservation to te_engine
-- @@
grant update (lifecycle_status) on strategy_version to te_engine
-- @@
grant update (status, result, handled_at) on command to te_engine
-- @@
grant update (decision, decided_at, decided_by, note) on approval to te_engine
-- @@
-- ───────── te_live: nur der Live-Autopilot ─────────
grant insert, update on heartbeat, alert, autopilot, trade, trade_order, order_approval to te_live
-- @@
grant insert on account, fill, equity_snapshot, signal_outcome, audit_event, reconciliation, execution_metric to te_live
-- @@
grant insert, update, delete on reservation to te_live
-- @@
grant update (permissions) on account to te_live
-- @@
grant update (status, ended_at, end_reason) on mandate to te_live
-- @@
grant update (status, applied_at) on policy_change to te_live
-- @@
grant update (status, result, handled_at) on command to te_live
-- @@
-- ───────── Row-Level-Security: Modus je Rolle ─────────
do $$
declare
  t text;
  pol record;
begin
  foreach t in array array['account', 'autopilot', 'trade', 'trade_order', 'fill', 'reservation', 'equity_snapshot', 'signal_outcome'] loop
    execute format('alter table %I enable row level security', t);
    for pol in select policyname from pg_policies where schemaname = 'public' and tablename = t and policyname like 'te\_%' loop
      execute format('drop policy %I on %I', pol.policyname, t);
    end loop;
    execute format('create policy te_read on %I for select to te_engine, te_live using (true)', t);
  end loop;
end $$
-- @@
create policy te_engine_write on account for insert to te_engine with check (mode = 'PAPER')
-- @@
create policy te_live_write on account for insert to te_live with check (mode = 'LIVE')
-- @@
create policy te_live_update on account for update to te_live using (mode = 'LIVE') with check (mode = 'LIVE')
-- @@
create policy te_engine_write on trade_order for all to te_engine using (mode = 'PAPER') with check (mode = 'PAPER')
-- @@
create policy te_live_write on trade_order for all to te_live using (mode = 'LIVE') with check (mode = 'LIVE')
-- @@
create policy te_engine_write on fill for insert to te_engine with check (te_order_mode(order_id) = 'PAPER')
-- @@
create policy te_live_write on fill for insert to te_live with check (te_order_mode(order_id) = 'LIVE')
-- @@
do $$
declare
  t text;
begin
  foreach t in array array['autopilot', 'trade', 'reservation', 'equity_snapshot', 'signal_outcome'] loop
    execute format('create policy te_engine_write on %I for all to te_engine using (te_account_mode(account_id) = %L) with check (te_account_mode(account_id) = %L)', t, 'PAPER', 'PAPER');
    execute format('create policy te_live_write on %I for all to te_live using (te_account_mode(account_id) = %L) with check (te_account_mode(account_id) = %L)', t, 'LIVE', 'LIVE');
  end loop;
end $$
