import type { BetterAuthPlugin } from "better-auth";
import { APIError, createAuthMiddleware, getSessionFromCtx, isAPIError } from "better-auth/api";
import { adminEmail } from "./config";
import { requestMeta, writeAuthAudit, type AuthAuditKind } from "./audit";
import { constantTimeEqualStrings } from "./api-token";
import { evaluateStepUp, STEP_UP_DEFAULT_MAX_AGE_SECONDS, effectiveMaxAge } from "./step-up-core";

/**
 * Better-Auth-Plugin «te-guard»: genau ein Benutzer, TOTP vor allem anderen, Step-up für heikle Kontoänderungen,
 * Audit der Anmeldeereignisse. Muss in `plugins` NACH twoFactor/passkey stehen (After-Hooks laufen in Reihenfolge),
 * aber vor nextCookies.
 *
 * Passwortanmeldung (/sign-in/email):
 *   - nur TE_ADMIN_EMAIL; Passwort muss TE_PASSWORD entsprechen (konstante Laufzeit). Die Umgebung ist massgebend:
 *     ändert TE_PASSWORD, wird der gespeicherte Hash beim nächsten Login nachgezogen, das alte Passwort gilt nicht mehr.
 *   - erster Login legt den einzigen Benutzer an (Registrierung ist sonst gesperrt). Existiert schon ein Benutzer mit
 *     anderer E-Mail, wird abgelehnt (E-Mail-Wechsel nur bewusst in der DB).
 */
const INVALID = () => new APIError("UNAUTHORIZED", { message: "E-Mail oder Passwort falsch.", code: "INVALID_EMAIL_OR_PASSWORD" });

/** Pfade, die eine Sitzung mit bestätigtem TOTP voraussetzen (die Einrichtung selbst ausgenommen). */
const NEEDS_TOTP = (path: string) =>
  path.startsWith("/passkey/") && path !== "/passkey/generate-authenticate-options" && path !== "/passkey/verify-authentication"
    ? true
    : path.startsWith("/step-up/") || path === "/list-sessions" || path === "/revoke-session" || path === "/revoke-other-sessions" || path === "/two-factor/generate-backup-codes";

/** Pfade, die zusätzlich eine frische Step-up-Bestätigung (≤ 5 min) verlangen. */
const NEEDS_STEP_UP = new Set(["/passkey/delete-passkey", "/two-factor/generate-backup-codes"]);

/** So lange nach der Anmeldung darf ohne Step-up ein Passkey hinzugefügt werden (Einrichtungsablauf). */
const NEW_SESSION_GRACE_SECONDS = 600;

type Body = Record<string, unknown> | undefined;

export const teGuard = () =>
  ({
    id: "te-guard",
    hooks: {
      before: [
        {
          matcher: (ctx) => ctx.path === "/sign-in/email",
          handler: createAuthMiddleware(async (ctx) => {
            const body = ctx.body as Body;
            const email = String(body?.email ?? "").trim().toLowerCase();
            const password = String(body?.password ?? "");
            const expectedEmail = adminEmail();
            const expectedPassword = process.env.TE_PASSWORD ?? "";
            const meta = { ...requestMeta(ctx.headers), method: "password" };
            // beide Vergleiche immer ausführen (kein früher Abbruch)
            const emailOk = constantTimeEqualStrings(email, expectedEmail);
            const passwordOk = constantTimeEqualStrings(password, expectedPassword);
            if (!expectedEmail || !expectedPassword || !emailOk || !passwordOk) {
              await writeAuthAudit("auth.login.failure", { ...meta, reason: emailOk ? "wrong_password" : "unknown_email" }, undefined, expectedEmail);
              throw INVALID();
            }
            const users = await ctx.context.internalAdapter.countTotalUsers();
            const existing = await ctx.context.internalAdapter.findUserByEmail(email);
            if (!existing) {
              if (users > 0) {
                ctx.context.logger.error("[te-guard] a user with a different email exists; refusing to create a second user");
                await writeAuthAudit("auth.login.failure", { ...meta, reason: "second_user_refused" }, undefined, expectedEmail);
                throw INVALID();
              }
              const user = await ctx.context.internalAdapter.createUser({ email, name: email.split("@")[0] || email, emailVerified: true }, { method: "email-password" });
              await ctx.context.internalAdapter.linkAccount({ userId: user.id, providerId: "credential", accountId: user.id, password: await ctx.context.password.hash(password) });
              return;
            }
            const credential = await ctx.context.internalAdapter.findCredentialAccount(existing.user.id);
            if (!credential) {
              await ctx.context.internalAdapter.linkAccount({ userId: existing.user.id, providerId: "credential", accountId: existing.user.id, password: await ctx.context.password.hash(password) });
            } else if (!credential.password || !(await ctx.context.password.verify({ hash: credential.password, password }))) {
              await ctx.context.internalAdapter.updatePassword(existing.user.id, await ctx.context.password.hash(password));
            }
          }),
        },
        {
          // TOTP-Einrichtung nur, solange sie fehlt (kein Austausch des Geheimnisses mit blosser Sitzung)
          matcher: (ctx) => ctx.path === "/two-factor/enable" || ctx.path === "/two-factor/verify-totp" || ctx.path === "/two-factor/verify-backup-code",
          handler: createAuthMiddleware(async (ctx) => {
            const session = await getSessionFromCtx(ctx);
            if (session && (session.user as { twoFactorEnabled?: boolean | null }).twoFactorEnabled)
              throw new APIError("FORBIDDEN", { message: "TOTP ist bereits eingerichtet.", code: "TOTP_ALREADY_ENABLED" });
          }),
        },
        {
          matcher: (ctx) => NEEDS_TOTP(ctx.path ?? ""),
          handler: createAuthMiddleware(async (ctx) => {
            const session = await getSessionFromCtx(ctx);
            if (!session) return; // die Endpunkte selbst antworten mit 401
            const user = session.user as { twoFactorEnabled?: boolean | null };
            if (!user.twoFactorEnabled) throw new APIError("FORBIDDEN", { message: "Zuerst TOTP einrichten.", code: "TOTP_SETUP_REQUIRED" });
            const path = ctx.path ?? "";
            const stepUpAt = (session.session as { stepUpAt?: Date | string | null }).stepUpAt;
            const now = new Date();
            if (NEEDS_STEP_UP.has(path)) {
              const r = evaluateStepUp(stepUpAt, now, effectiveMaxAge(STEP_UP_DEFAULT_MAX_AGE_SECONDS));
              if (!r.ok) throw new APIError("FORBIDDEN", { message: r.reason, code: "STEP_UP_REQUIRED" });
            }
            // Passkey hinzufügen: direkt nach der Anmeldung (Sitzung ≤ 10 min alt) oder mit frischem Step-up
            if (path === "/passkey/generate-register-options") {
              const fresh = evaluateStepUp(session.session.createdAt, now, NEW_SESSION_GRACE_SECONDS).ok;
              if (!fresh && !evaluateStepUp(stepUpAt, now, effectiveMaxAge(STEP_UP_DEFAULT_MAX_AGE_SECONDS)).ok)
                throw new APIError("FORBIDDEN", { message: "Bitte zuerst mit Passkey oder TOTP bestätigen.", code: "STEP_UP_REQUIRED" });
            }
          }),
        },
      ],
      after: [
        {
          matcher: () => true,
          handler: createAuthMiddleware(async (ctx) => {
            const path = ctx.path ?? "";
            const returned = ctx.context.returned;
            const failed = isAPIError(returned);
            const email = adminEmail();
            const meta = requestMeta(ctx.headers);
            const audit = (kind: AuthAuditKind, data: Record<string, unknown> = {}, object?: string) => writeAuthAudit(kind, { ...meta, ...data }, object, email);
            // Anmeldung mit zweitem Faktor trägt das «two_factor»-Cookie; ohne es ist es die TOTP-Einrichtung einer Sitzung
            const inTwoFactorSignIn = path.startsWith("/two-factor/verify-") && !!(await ctx.getSignedCookie(ctx.context.createAuthCookie("two_factor").name, ctx.context.secret));

            switch (path) {
              case "/sign-in/email":
                if (failed) await audit("auth.login.failure", { method: "password", reason: "rejected" });
                else if (ctx.context.newSession) await audit("auth.login.success", { method: "password", note: "TOTP noch nicht eingerichtet" });
                else await audit("auth.login.password_ok", { method: "password", next: "totp" });
                break;
              case "/two-factor/verify-totp":
              case "/two-factor/verify-backup-code": {
                const method = path.endsWith("totp") ? "totp" : "backup_code";
                if (inTwoFactorSignIn) await audit(failed ? "auth.login.failure" : "auth.login.success", { method });
                else if (!failed && method === "totp") await audit("auth.totp.enrolled");
                break;
              }
              case "/passkey/verify-authentication":
                await audit(failed ? "auth.login.failure" : "auth.login.success", { method: "passkey" });
                break;
              case "/passkey/verify-registration":
                if (!failed) await audit("auth.passkey.added", { name: (returned as { name?: string } | undefined)?.name ?? null }, `passkey:${(returned as { id?: string } | undefined)?.id ?? "?"}`);
                break;
              case "/passkey/delete-passkey":
                if (!failed) await audit("auth.passkey.removed", {}, `passkey:${String((ctx.body as Body)?.id ?? "?")}`);
                break;
              case "/two-factor/generate-backup-codes":
                if (!failed) await audit("auth.backup_codes.regenerated");
                break;
              case "/sign-out":
                if (!failed) await audit("auth.logout");
                break;
              case "/revoke-sessions":
              case "/revoke-other-sessions":
              case "/revoke-session":
                if (!failed) await audit("auth.sessions.revoked", { scope: path.slice(1) });
                break;
            }
          }),
        },
      ],
    },
  }) satisfies BetterAuthPlugin;
