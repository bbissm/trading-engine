import { bigint, boolean, index, integer, pgTable, text, timestamp } from "drizzle-orm/pg-core";

/**
 * Tabellen von Better Auth (Anmeldung, E0-4). Nur die Web-App nutzt sie; die Engine-Rolle hat darauf
 * keinerlei Rechte (scripts/roles.mjs). Tabellennamen mit Präfix `auth_`, weil `account` und `session`
 * im fachlichen Schema bzw. als Begriff schon belegt sind.
 *
 * Die Objekt-Schlüssel in `authSchema` (user, session, …) und die Eigenschaftsnamen der Spalten
 * (emailVerified, …) sind die Modell- und Feldnamen, mit denen Better Auth über den Drizzle-Adapter
 * zugreift; Better Auth prüft das beim ersten Zugriff (runtime schema check). Spalten in der DB: snake_case.
 */
const ts = (name: string) => timestamp(name, { withTimezone: true });

export const authUser = pgTable("auth_user", {
  id: text("id").primaryKey(),
  name: text("name").notNull(),
  email: text("email").notNull().unique(),
  emailVerified: boolean("email_verified").notNull().default(false),
  image: text("image"),
  createdAt: ts("created_at").notNull().defaultNow(),
  updatedAt: ts("updated_at").notNull().defaultNow(),
  /** two-factor-Plugin: erst nach bestätigter TOTP-Einrichtung true. */
  twoFactorEnabled: boolean("two_factor_enabled").default(false),
});

export const authSession = pgTable(
  "auth_session",
  {
    id: text("id").primaryKey(),
    expiresAt: ts("expires_at").notNull(),
    token: text("token").notNull().unique(),
    createdAt: ts("created_at").notNull().defaultNow(),
    updatedAt: ts("updated_at").notNull().defaultNow(),
    ipAddress: text("ip_address"),
    userAgent: text("user_agent"),
    userId: text("user_id")
      .notNull()
      .references(() => authUser.id, { onDelete: "cascade" }),
    /** Letzte erfolgreiche Step-up-Bestätigung (Passkey oder TOTP) in dieser Sitzung; siehe src/lib/auth/step-up.ts. */
    stepUpAt: ts("step_up_at"),
  },
  (t) => [index("auth_session_user_idx").on(t.userId)],
);

export const authAccount = pgTable(
  "auth_account",
  {
    id: text("id").primaryKey(),
    accountId: text("account_id").notNull(),
    providerId: text("provider_id").notNull(),
    userId: text("user_id")
      .notNull()
      .references(() => authUser.id, { onDelete: "cascade" }),
    accessToken: text("access_token"),
    refreshToken: text("refresh_token"),
    idToken: text("id_token"),
    accessTokenExpiresAt: ts("access_token_expires_at"),
    refreshTokenExpiresAt: ts("refresh_token_expires_at"),
    scope: text("scope"),
    /** Passwort-Hash (scrypt) des Kontos «credential». */
    password: text("password"),
    createdAt: ts("created_at").notNull().defaultNow(),
    updatedAt: ts("updated_at").notNull().defaultNow(),
  },
  (t) => [index("auth_account_user_idx").on(t.userId)],
);

export const authVerification = pgTable(
  "auth_verification",
  {
    id: text("id").primaryKey(),
    identifier: text("identifier").notNull(),
    value: text("value").notNull(),
    expiresAt: ts("expires_at").notNull(),
    createdAt: ts("created_at").notNull().defaultNow(),
    updatedAt: ts("updated_at").notNull().defaultNow(),
  },
  (t) => [index("auth_verification_identifier_idx").on(t.identifier)],
);

export const authTwoFactor = pgTable(
  "auth_two_factor",
  {
    id: text("id").primaryKey(),
    /** TOTP-Geheimnis, symmetrisch verschlüsselt mit BETTER_AUTH_SECRET. */
    secret: text("secret").notNull(),
    /** Backup-Codes, verschlüsselt. */
    backupCodes: text("backup_codes").notNull(),
    userId: text("user_id")
      .notNull()
      .references(() => authUser.id, { onDelete: "cascade" }),
    verified: boolean("verified").default(true),
    failedVerificationCount: integer("failed_verification_count").default(0),
    lockedUntil: ts("locked_until"),
  },
  (t) => [index("auth_two_factor_user_idx").on(t.userId), index("auth_two_factor_secret_idx").on(t.secret)],
);

export const authPasskey = pgTable(
  "auth_passkey",
  {
    id: text("id").primaryKey(),
    name: text("name"),
    publicKey: text("public_key").notNull(),
    userId: text("user_id")
      .notNull()
      .references(() => authUser.id, { onDelete: "cascade" }),
    credentialID: text("credential_id").notNull(),
    counter: integer("counter").notNull(),
    deviceType: text("device_type").notNull(),
    backedUp: boolean("backed_up").notNull(),
    transports: text("transports"),
    createdAt: ts("created_at"),
    aaguid: text("aaguid"),
  },
  (t) => [index("auth_passkey_user_idx").on(t.userId), index("auth_passkey_credential_idx").on(t.credentialID)],
);

/** Rate-Limit-Zähler von Better Auth (storage "database"; im Speicher wäre er pro Serverless-Instanz). */
export const authRateLimit = pgTable("auth_rate_limit", {
  id: text("id").primaryKey(),
  key: text("key").notNull().unique(),
  count: integer("count").notNull(),
  lastRequest: bigint("last_request", { mode: "number" }).notNull(),
});

/** Modellname (Better Auth) → Tabelle; so an den Drizzle-Adapter übergeben. */
export const authSchema = {
  user: authUser,
  session: authSession,
  account: authAccount,
  verification: authVerification,
  twoFactor: authTwoFactor,
  passkey: authPasskey,
  rateLimit: authRateLimit,
};

/** SQL-Namen aller Auth-Tabellen (für die Rechtevergabe in scripts/roles.mjs und Tests). */
export const AUTH_TABLES = ["auth_user", "auth_session", "auth_account", "auth_verification", "auth_two_factor", "auth_passkey", "auth_rate_limit"] as const;
