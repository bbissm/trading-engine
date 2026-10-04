import { missing, redact, WEB_ENV } from "./env";

/**
 * Minimal Telegram and Pushover senders for the web app (watchdog, webhook replies). Same env names as the engine.
 * Short timeouts; errors are returned (redacted), never thrown, and never contain tokens.
 */

type Env = Record<string, string | undefined>;
export type Fetch = typeof fetch;

export interface SendResult {
  ok: boolean;
  ref?: string;
  error?: string;
}

const TIMEOUT_MS = 6000;
const TELEGRAM_API = "https://api.telegram.org";
const PUSHOVER_API = "https://api.pushover.net/1";

async function call(f: Fetch, url: string, init: RequestInit, env: Env): Promise<{ status: number; body: Record<string, unknown> } | { error: string }> {
  try {
    const res = await f(url, { ...init, signal: AbortSignal.timeout(TIMEOUT_MS) });
    let body: Record<string, unknown> = {};
    try {
      body = (await res.json()) as Record<string, unknown>;
    } catch {
      body = {};
    }
    return { status: res.status, body };
  } catch (e) {
    return { error: redact(e instanceof Error ? `${e.name}: ${e.message}` : String(e), env).slice(0, 300) };
  }
}

export const telegramConfigured = (env: Env = process.env) => missing(WEB_ENV.watchdogTelegram, env).length === 0;
export const pushoverConfigured = (env: Env = process.env) => missing(WEB_ENV.watchdogPushover, env).length === 0;

export async function telegramCall(method: string, payload: Record<string, unknown>, env: Env = process.env, f: Fetch = fetch): Promise<SendResult> {
  const token = (env.TELEGRAM_BOT_TOKEN ?? "").trim();
  if (!token) return { ok: false, error: "TELEGRAM_BOT_TOKEN fehlt" };
  const r = await call(f, `${TELEGRAM_API}/bot${token}/${method}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) }, env);
  if ("error" in r) return { ok: false, error: r.error };
  if (r.body.ok !== true) return { ok: false, error: redact(`HTTP ${r.status}: ${String(r.body.description ?? "abgelehnt")}`, env) };
  const result = r.body.result as { message_id?: number } | undefined;
  return { ok: true, ref: result?.message_id !== undefined ? String(result.message_id) : undefined };
}

export function sendTelegram(text: string, opts: { silent?: boolean; buttons?: { text: string; data: string }[] } = {}, env: Env = process.env, f: Fetch = fetch) {
  const payload: Record<string, unknown> = { chat_id: (env.TELEGRAM_CHAT_ID ?? "").trim(), text: text.slice(0, 4000), disable_notification: !!opts.silent };
  if (opts.buttons?.length) payload.reply_markup = { inline_keyboard: [opts.buttons.map((b) => ({ text: b.text, callback_data: b.data }))] };
  return telegramCall("sendMessage", payload, env, f);
}

export function answerCallbackQuery(id: string, text: string, env: Env = process.env, f: Fetch = fetch) {
  return telegramCall("answerCallbackQuery", { callback_query_id: id, text: text.slice(0, 200) }, env, f);
}

/** Pushover; priority 2 repeats every `retry` seconds until acknowledged or `expire` seconds have passed. */
export async function sendPushover(title: string, message: string, opts: { priority?: number; retry?: number; expire?: number } = {}, env: Env = process.env, f: Fetch = fetch): Promise<SendResult> {
  if (!pushoverConfigured(env)) return { ok: false, error: "Pushover nicht eingerichtet" };
  const form = new URLSearchParams({ token: env.PUSHOVER_APP_TOKEN!.trim(), user: env.PUSHOVER_USER_KEY!.trim(), title: title.slice(0, 250), message: message.slice(0, 1000), priority: String(opts.priority ?? 0) });
  if (opts.priority === 2) {
    form.set("retry", String(Math.max(30, opts.retry ?? 60)));
    form.set("expire", String(Math.min(10800, opts.expire ?? 10800)));
  }
  const r = await call(f, `${PUSHOVER_API}/messages.json`, { method: "POST", body: form }, env);
  if ("error" in r) return { ok: false, error: r.error };
  if (r.body.status !== 1) return { ok: false, error: `HTTP ${r.status}: abgelehnt` };
  return { ok: true, ref: typeof r.body.receipt === "string" ? r.body.receipt : undefined };
}

/** Pushover receipt: `acknowledged` once confirmed on the device. */
export async function pushoverAcknowledged(receipt: string, env: Env = process.env, f: Fetch = fetch): Promise<boolean> {
  if (!pushoverConfigured(env) || !/^[A-Za-z0-9]+$/.test(receipt)) return false;
  const r = await call(f, `${PUSHOVER_API}/receipts/${receipt}.json?token=${encodeURIComponent(env.PUSHOVER_APP_TOKEN!.trim())}`, { method: "GET" }, env);
  return !("error" in r) && r.body.status === 1 && Number(r.body.acknowledged) === 1;
}
