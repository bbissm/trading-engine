/**
 * Environment variable names of the notification channels (same names in the engine and the web app).
 * Only names are ever shown or logged — never values.
 */

export const CHANNELS = ["TELEGRAM", "PUSHOVER", "EMAIL"] as const;
export type Channel = (typeof CHANNELS)[number];

/** Variables the engine (project trading-engine-worker) needs per channel. */
export const ENGINE_ENV: Record<Channel, string[]> = {
  TELEGRAM: ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
  PUSHOVER: ["PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY"],
  EMAIL: ["RESEND_API_KEY", "ALERT_EMAIL_TO", "ALERT_EMAIL_FROM"],
};

/** Variables this web app needs for the Telegram webhook and the watchdog (second, independent path). */
export const WEB_ENV = {
  webhook: ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_WEBHOOK_SECRET"],
  watchdogTelegram: ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
  watchdogPushover: ["PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY"],
  cron: ["CRON_SECRET"],
} as const;

export const CHANNEL_LABEL: Record<string, string> = { TELEGRAM: "Telegram", PUSHOVER: "Pushover", EMAIL: "E-Mail (Resend)", IN_APP: "In-App", SMS: "SMS" };

type Env = Record<string, string | undefined>;

/** Names of the variables in `names` that are missing or empty. */
export const missing = (names: readonly string[], env: Env = process.env): string[] => names.filter((n) => !(env[n] ?? "").trim());

/** Removes known secret values from a text (e.g. an error message that contains a URL with the bot token). */
export function redact(text: string, env: Env = process.env): string {
  const secrets = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY", "RESEND_API_KEY", "CRON_SECRET"]
    .map((k) => (env[k] ?? "").trim())
    .filter((s) => s.length >= 4)
    .sort((a, b) => b.length - a.length);
  return secrets.reduce((t, s) => t.split(s).join("***"), text);
}
