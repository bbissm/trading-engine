import { passkey } from "@better-auth/passkey";
import { betterAuth } from "better-auth";
import { drizzleAdapter } from "better-auth/adapters/drizzle";
import { nextCookies } from "better-auth/next-js";
import { twoFactor } from "better-auth/plugins/two-factor";
import { authSchema } from "@/db/auth-schema";
import { db } from "@/db/client";
import { SESSION_EXPIRES_SECONDS, SESSION_UPDATE_AGE_SECONDS } from "./config";
import { teGuard } from "./guard-plugin";
import { stepUp } from "./step-up-plugin";

/**
 * Better Auth (E0-4): ein Benutzer, Passwort + TOTP oder Passkey, Step-up.
 *
 * Datenbank: Drizzle-Adapter auf derselben Instanz wie die App (`db()`): in Produktion Neon-HTTP, lokal `pg`.
 * Der Neon-HTTP-Treiber kennt keine interaktiven Transaktionen → `transaction: false` (Better Auth führt dann
 * mehrteilige Schritte nacheinander aus; die genutzten Abläufe brauchen keine Transaktion, Passkey-Registrierung
 * mit `createSession` – die einzige Stelle mit `runWithTransaction` – wird nicht verwendet).
 *
 * Lazy, weil Build und Seiten ohne DATABASE_URL/BETTER_AUTH_SECRET funktionieren müssen.
 */
function createAuth() {
  const baseURL = process.env.BETTER_AUTH_URL;
  if (!baseURL) throw new Error("BETTER_AUTH_URL is not configured");
  const url = new URL(baseURL);
  const rpID = process.env.TE_PASSKEY_RP_ID || url.hostname;
  const origin = url.origin;
  const secure = url.protocol === "https:" || process.env.NODE_ENV === "production";

  return betterAuth({
    appName: "TradingEngine",
    baseURL,
    basePath: "/api/auth",
    secret: process.env.BETTER_AUTH_SECRET,
    database: drizzleAdapter(db(), { provider: "pg", schema: authSchema, transaction: false }),
    telemetry: { enabled: false },
    emailAndPassword: { enabled: true, disableSignUp: true, minPasswordLength: 12, maxPasswordLength: 256 },
    user: { deleteUser: { enabled: false } },
    session: {
      expiresIn: SESSION_EXPIRES_SECONDS,
      updateAge: SESSION_UPDATE_AGE_SECONDS,
      // «fresh session» von Better Auth aus (würde u. a. die Sitzungsliste nach kurzer Zeit sperren); heikle Schritte
      // sichert stattdessen der Step-up (te-guard)
      freshAge: 0,
      // kein Cookie-Cache: jede Prüfung liest die Sitzung aus der DB (Widerruf wirkt sofort, Step-up-Zeitpunkt aktuell)
      cookieCache: { enabled: false },
    },
    advanced: {
      useSecureCookies: secure,
      defaultCookieAttributes: { httpOnly: true, secure, sameSite: "lax", path: "/" },
      cookiePrefix: "te",
      ipAddress: { ipAddressHeaders: ["x-forwarded-for", "x-real-ip"] },
    },
    rateLimit: {
      enabled: true,
      storage: "database",
      window: 60,
      max: 100,
      customRules: {
        "/sign-in/email": { window: 60, max: 5 },
        "/two-factor/*": { window: 60, max: 10 },
        "/passkey/verify-authentication": { window: 60, max: 10 },
        "/passkey/generate-authenticate-options": { window: 60, max: 20 },
      },
    },
    // Nicht benötigte oder für ein Ein-Benutzer-System gefährliche Endpunkte
    disabledPaths: [
      "/sign-up/email",
      "/sign-in/social",
      "/callback/:id",
      "/link-social",
      "/unlink-account",
      "/list-accounts",
      "/account-info",
      "/get-access-token",
      "/refresh-token",
      "/change-email",
      "/change-password",
      "/update-user",
      "/update-session",
      "/delete-user",
      "/delete-user/callback",
      "/request-password-reset",
      "/reset-password",
      "/reset-password/:token",
      "/send-verification-email",
      "/verify-email",
      "/verify-password",
      "/two-factor/disable",
      "/two-factor/get-totp-uri",
      "/two-factor/send-otp",
      "/two-factor/verify-otp",
    ],
    databaseHooks: {
      user: {
        create: {
          // zweite Absicherung neben disableSignUp: nie mehr als ein Benutzer
          before: async (user) => {
            const existing = await db().select({ id: authSchema.user.id }).from(authSchema.user).limit(1);
            return existing.length ? false : { data: user };
          },
        },
      },
    },
    plugins: [
      twoFactor({
        issuer: "TradingEngine",
        totpOptions: { digits: 6, period: 30 },
        backupCodeOptions: { amount: 10, length: 10, storeBackupCodes: "encrypted" },
        accountLockout: { enabled: true, maxFailedAttempts: 10, durationSeconds: 900 },
      }),
      passkey({
        rpID,
        rpName: "TradingEngine",
        origin,
        authenticatorSelection: { residentKey: "required", userVerification: "required" },
        authentication: {
          // Passkey allein genügt nur mit Benutzerverifikation (PIN/Biometrie) – sonst wäre es ein einzelner Besitzfaktor
          afterVerification: async ({ verification }) => {
            if (!verification.authenticationInfo.userVerified) throw new Error("user verification required");
          },
        },
      }),
      stepUp({ rpID, origin }),
      teGuard(),
      nextCookies(),
    ],
  });
}

export type Auth = ReturnType<typeof createAuth>;

let instance: Auth | undefined;

export function getAuth(): Auth {
  instance ??= createAuth();
  return instance;
}
