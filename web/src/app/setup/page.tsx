import type { Metadata } from "next";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { AuthCard } from "@/app/login/auth-card";
import { authMode } from "@/lib/auth/config";
import { getAuth } from "@/lib/auth/server";
import { SetupFlow } from "./setup-flow";

export const metadata: Metadata = { title: "Sicherheit einrichten · TradingEngine" };
export const dynamic = "force-dynamic";

/**
 * Erste Einrichtung nach dem ersten Login mit Passwort: TOTP ist Pflicht (vorher ist nichts anderes erreichbar,
 * siehe src/proxy.ts), danach wird ein Passkey empfohlen.
 */
export default async function SetupPage() {
  if (authMode().mode !== "enabled") redirect("/");
  const h = await headers();
  const auth = getAuth();
  const session = await auth.api.getSession({ headers: h }).catch(() => null);
  if (!session) redirect("/login");
  const totpEnabled = !!(session.user as { twoFactorEnabled?: boolean | null }).twoFactorEnabled;
  if (totpEnabled) {
    const passkeys = await auth.api.listPasskeys({ headers: h }).catch(() => []);
    if (passkeys.length) redirect("/");
  }
  return (
    <AuthCard wide subtitle={totpEnabled ? "Passkey hinzufügen" : "Zwei-Faktor-Anmeldung einrichten"}>
      <SetupFlow email={session.user.email} totpEnabled={totpEnabled} />
    </AuthCard>
  );
}
