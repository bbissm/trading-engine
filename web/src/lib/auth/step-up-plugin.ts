import { base64 } from "@better-auth/utils/base64";
import { createOTP } from "@better-auth/utils/otp";
import { generateAuthenticationOptions, verifyAuthenticationResponse, type AuthenticatorTransportFuture } from "@simplewebauthn/server";
import type { BetterAuthPlugin } from "better-auth";
import { APIError, createAuthEndpoint, sessionMiddleware } from "better-auth/api";
import { symmetricDecrypt } from "better-auth/crypto";
import * as z from "zod";
import { requestMeta, writeAuthAudit } from "./audit";

/**
 * Better-Auth-Plugin «step-up»: erneute Bestätigung einer bestehenden Sitzung mit Passkey oder TOTP.
 * Erfolgreich → `auth_session.step_up_at = now()` für genau diese Sitzung. Geprüft wird das Alter in
 * `requireStepUp()` (src/lib/auth/step-up.ts) bzw. im Hook für Passkey-Entfernen und Backup-Codes.
 *
 * Endpunkte (unter /api/auth, Rate-Limit 10 je Minute und IP):
 *   POST /step-up/totp                { code }
 *   GET  /step-up/passkey/options     → WebAuthn-Optionen (nur eigene Passkeys, Benutzerverifikation Pflicht)
 *   POST /step-up/passkey/verify      { response }
 * Ohne Sitzung (z. B. API-Token) gibt es keinen Step-up.
 */
export interface StepUpPluginOptions {
  rpID: string;
  origin: string;
}

type PasskeyRow = { id: string; userId: string; credentialID: string; publicKey: string; counter: number; transports?: string | null };
type TwoFactorRow = { id: string; userId: string; secret: string; verified?: boolean | null };

const CHALLENGE_TTL_SECONDS = 300;
const challengeId = (sessionId: string) => `step-up-passkey:${sessionId}`;

export const stepUp = (opts: StepUpPluginOptions) =>
  ({
    id: "step-up",
    schema: {
      session: {
        fields: {
          stepUpAt: { type: "date", required: false, input: false, fieldName: "stepUpAt" },
        },
      },
    },
    endpoints: {
      stepUpTotp: createAuthEndpoint(
        "/step-up/totp",
        { method: "POST", use: [sessionMiddleware], body: z.object({ code: z.string().min(6).max(10) }) },
        async (ctx) => {
          const { session, user } = ctx.context.session;
          const meta = { ...requestMeta(ctx.headers), method: "totp" };
          const fail = async (reason: string) => {
            await writeAuthAudit("auth.step_up.failure", { ...meta, reason }, `session:${session.id}`, user.email);
            throw new APIError("UNAUTHORIZED", { message: "Code ungültig.", code: "STEP_UP_INVALID_CODE" });
          };
          const row = (await ctx.context.adapter.findOne({ model: "twoFactor", where: [{ field: "userId", value: user.id }] })) as TwoFactorRow | null;
          if (!row || row.verified === false) return fail("totp_not_enabled");
          const code = ctx.body.code.replace(/\s+/g, "");
          const secret = await symmetricDecrypt({ key: ctx.context.secretConfig, data: row.secret });
          if (!(await createOTP(secret, { digits: 6, period: 30 }).verify(code))) return fail("invalid_code");
          // Jeder Code höchstens einmal (gültig ±30 s → 120 s Sperre reicht)
          const replayKey = `step-up-totp:${user.id}:${code}`;
          if (await ctx.context.internalAdapter.findVerificationValue(replayKey)) return fail("replayed_code");
          await ctx.context.internalAdapter.createVerificationValue({ identifier: replayKey, value: "1", expiresAt: new Date(Date.now() + 120_000) });
          const at = new Date();
          await ctx.context.internalAdapter.updateSession(session.token, { stepUpAt: at } as Record<string, unknown>);
          await writeAuthAudit("auth.step_up.success", meta, `session:${session.id}`, user.email);
          return ctx.json({ ok: true, at: at.toISOString() });
        },
      ),

      stepUpPasskeyOptions: createAuthEndpoint("/step-up/passkey/options", { method: "GET", use: [sessionMiddleware] }, async (ctx) => {
        const { session, user } = ctx.context.session;
        const passkeys = (await ctx.context.adapter.findMany({ model: "passkey", where: [{ field: "userId", value: user.id }] })) as PasskeyRow[];
        if (!passkeys.length) throw new APIError("BAD_REQUEST", { message: "Kein Passkey registriert.", code: "NO_PASSKEY" });
        const options = await generateAuthenticationOptions({
          rpID: opts.rpID,
          userVerification: "required",
          allowCredentials: passkeys.map((p) => ({ id: p.credentialID, transports: (p.transports?.split(",").filter(Boolean) ?? []) as AuthenticatorTransportFuture[] })),
        });
        await ctx.context.internalAdapter.deleteVerificationByIdentifier(challengeId(session.id)).catch(() => undefined);
        await ctx.context.internalAdapter.createVerificationValue({
          identifier: challengeId(session.id),
          value: options.challenge,
          expiresAt: new Date(Date.now() + CHALLENGE_TTL_SECONDS * 1000),
        });
        return ctx.json(options);
      }),

      stepUpPasskeyVerify: createAuthEndpoint(
        "/step-up/passkey/verify",
        { method: "POST", use: [sessionMiddleware], body: z.object({ response: z.record(z.string(), z.any()) }) },
        async (ctx) => {
          const { session, user } = ctx.context.session;
          const meta = { ...requestMeta(ctx.headers), method: "passkey" };
          const fail = async (reason: string) => {
            await writeAuthAudit("auth.step_up.failure", { ...meta, reason }, `session:${session.id}`, user.email);
            throw new APIError("UNAUTHORIZED", { message: "Passkey-Bestätigung fehlgeschlagen.", code: "STEP_UP_PASSKEY_FAILED" });
          };
          const challenge = await ctx.context.internalAdapter.consumeVerificationValue(challengeId(session.id));
          if (!challenge || new Date(challenge.expiresAt).getTime() < Date.now()) return fail("challenge_missing");
          const resp = ctx.body.response as { id?: string };
          if (typeof resp.id !== "string") return fail("malformed_response");
          const passkey = (await ctx.context.adapter.findOne({ model: "passkey", where: [{ field: "credentialID", value: resp.id }] })) as PasskeyRow | null;
          if (!passkey || passkey.userId !== user.id) return fail("unknown_passkey");
          let verified = false;
          let newCounter = passkey.counter;
          try {
            const v = await verifyAuthenticationResponse({
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              response: ctx.body.response as any,
              expectedChallenge: challenge.value,
              expectedOrigin: opts.origin,
              expectedRPID: opts.rpID,
              credential: {
                id: passkey.credentialID,
                publicKey: base64.decode(passkey.publicKey),
                counter: passkey.counter,
                transports: (passkey.transports?.split(",").filter(Boolean) ?? []) as AuthenticatorTransportFuture[],
              },
              requireUserVerification: true,
            });
            verified = v.verified && v.authenticationInfo.userVerified;
            newCounter = v.authenticationInfo.newCounter;
          } catch (e) {
            ctx.context.logger.error("step-up passkey verification failed", e);
          }
          if (!verified) return fail("verification_failed");
          await ctx.context.adapter.update({ model: "passkey", where: [{ field: "id", value: passkey.id }], update: { counter: newCounter } });
          const at = new Date();
          await ctx.context.internalAdapter.updateSession(session.token, { stepUpAt: at } as Record<string, unknown>);
          await writeAuthAudit("auth.step_up.success", { ...meta, passkeyId: passkey.id }, `session:${session.id}`, user.email);
          return ctx.json({ ok: true, at: at.toISOString() });
        },
      ),
    },
    rateLimit: [{ pathMatcher: (path: string) => path.startsWith("/step-up/"), window: 60, max: 10 }],
  }) satisfies BetterAuthPlugin;
