import { timingSafeEqual } from "node:crypto";
import { telegramCall, type Fetch } from "./senders";

/**
 * Telegram webhook: authentication and parsing (pure functions, tested without network).
 *
 * Only two things can come from Telegram, and neither increases risk or touches live:
 * - button «Bestätigen» (callback `a:<alertId>`) → stops the escalation of that alert, approves nothing;
 * - button «Einstiege pausieren» (callback `p:<alertId>`) or `/pause <account>` → paper accounts only (the engine checks).
 * Plus `/status` (read-only). Everything else is ignored.
 *
 * Registering the webhook (once, manually — never during the build):
 *   curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
 *     -d url="https://<web-app-domain>/api/telegram" -d secret_token="$TELEGRAM_WEBHOOK_SECRET" \
 *     -d allowed_updates='["message","callback_query"]'
 * or call `registerWebhook()` below from a one-off script. `TELEGRAM_WEBHOOK_SECRET`: 1–256 characters A–Z, a–z, 0–9, _ and -.
 */

type Env = Record<string, string | undefined>;

export interface TelegramUpdate {
  update_id?: number;
  message?: { chat?: { id?: number | string }; from?: { id?: number | string }; text?: string };
  callback_query?: { id?: string; from?: { id?: number | string }; data?: string; message?: { chat?: { id?: number | string } } };
}

export type ParsedUpdate =
  | { kind: "callback"; callbackId: string; action: "ack" | "pause"; alertId: number }
  | { kind: "status" }
  | { kind: "pause"; accountId: string }
  | { kind: "ignore"; callbackId?: string };

const ACCOUNT_ID = /^[a-z0-9][a-z0-9-]{0,79}$/;

/** Header `X-Telegram-Bot-Api-Secret-Token` must equal TELEGRAM_WEBHOOK_SECRET (constant-time comparison). */
export function verifySecret(header: string | null, env: Env = process.env): boolean {
  const secret = (env.TELEGRAM_WEBHOOK_SECRET ?? "").trim();
  if (!secret || !header) return false;
  const a = Buffer.from(header);
  const b = Buffer.from(secret);
  return a.length === b.length && timingSafeEqual(a, b);
}

/** Only updates from the configured chat are accepted. */
export function fromAllowedChat(update: TelegramUpdate, env: Env = process.env): boolean {
  const allowed = (env.TELEGRAM_CHAT_ID ?? "").trim();
  if (!allowed) return false;
  const chat = update.callback_query ? update.callback_query.message?.chat?.id : update.message?.chat?.id;
  return chat !== undefined && String(chat) === allowed;
}

export function parseUpdate(update: TelegramUpdate): ParsedUpdate {
  const cb = update.callback_query;
  if (cb) {
    const m = /^([ap]):(\d{1,12})$/.exec(cb.data ?? "");
    if (!cb.id || !m) return { kind: "ignore", callbackId: cb.id };
    return { kind: "callback", callbackId: cb.id, action: m[1] === "a" ? "ack" : "pause", alertId: Number(m[2]) };
  }
  const text = (update.message?.text ?? "").trim();
  const [cmd, ...args] = text.split(/\s+/);
  const command = (cmd ?? "").toLowerCase().replace(/@[\w_]+$/, "");
  if (command === "/status" && args.length === 0) return { kind: "status" };
  if (command === "/pause" && args.length === 1 && ACCOUNT_ID.test(args[0])) return { kind: "pause", accountId: args[0] };
  return { kind: "ignore" };
}

export const CALLBACK_ANSWER = { ack: "Bestätigt – genehmigt keinen Trade", pause: "Pause angefordert" } as const;

/** One-off helper to register the webhook (see the note above). Not called by the app itself. */
export function registerWebhook(url: string, env: Env = process.env, f: Fetch = fetch) {
  return telegramCall("setWebhook", { url, secret_token: (env.TELEGRAM_WEBHOOK_SECRET ?? "").trim(), allowed_updates: ["message", "callback_query"] }, env, f);
}
