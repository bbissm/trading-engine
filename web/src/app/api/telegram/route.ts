import { NextResponse } from "next/server";
import { hasDb } from "@/db/client";
import { issueTelegramCommand, telegramStatusText } from "@/lib/data/alerts";
import { answerCallbackQuery, sendTelegram } from "@/lib/notify/senders";
import { CALLBACK_ANSWER, fromAllowedChat, parseUpdate, verifySecret, type TelegramUpdate } from "@/lib/notify/telegram";

export const dynamic = "force-dynamic";

/**
 * Telegram webhook. Excluded from the login guard (src/proxy.ts); authenticates itself:
 * header `X-Telegram-Bot-Api-Secret-Token` = TELEGRAM_WEBHOOK_SECRET and chat id = TELEGRAM_CHAT_ID.
 * Buttons write a TELEGRAM_CALLBACK command (+ audit) for the engine; `/status` is read-only; `/pause <konto>` is paper only.
 * Nothing here can increase risk or touch live. Registering the webhook: see src/lib/notify/telegram.ts.
 * Always answers 200 for authenticated requests so Telegram does not retry endlessly.
 */
export async function POST(req: Request) {
  if (!verifySecret(req.headers.get("x-telegram-bot-api-secret-token"))) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  let update: TelegramUpdate;
  try {
    update = (await req.json()) as TelegramUpdate;
  } catch {
    return NextResponse.json({ ok: true, ignored: "invalid json" });
  }
  if (!fromAllowedChat(update)) return NextResponse.json({ ok: true, ignored: "chat" });
  if (!hasDb()) return NextResponse.json({ ok: true, ignored: "no database" });

  const parsed = parseUpdate(update);
  try {
    if (parsed.kind === "callback") {
      const r = await issueTelegramCommand({ action: parsed.action, alert_id: parsed.alertId });
      await answerCallbackQuery(parsed.callbackId, r.ok ? CALLBACK_ANSWER[parsed.action] : r.error);
      return NextResponse.json({ ok: true, command: r.ok ? r.commandId : null });
    }
    if (parsed.kind === "status") {
      await sendTelegram(await telegramStatusText(), { silent: true });
      return NextResponse.json({ ok: true });
    }
    if (parsed.kind === "pause") {
      const r = await issueTelegramCommand({ action: "pause", account_id: parsed.accountId });
      await sendTelegram(r.ok ? `[PAPER] Pause angefordert für ${parsed.accountId} – die Engine führt sie innert etwa einer Minute aus.` : `[SYSTEM] ${r.error}`, { silent: true });
      return NextResponse.json({ ok: true, command: r.ok ? r.commandId : null });
    }
    if (parsed.callbackId) await answerCallbackQuery(parsed.callbackId, "Nicht unterstützt");
    return NextResponse.json({ ok: true, ignored: "unsupported" });
  } catch (e) {
    console.error("[telegram]", e instanceof Error ? e.name : "Fehler");
    return NextResponse.json({ ok: true, error: "internal" });
  }
}
