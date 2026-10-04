import type { Metadata } from "next";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { adminEmail, authMode, safeNext, SESSION_EXPIRES_SECONDS } from "@/lib/auth/config";
import { getAuth } from "@/lib/auth/server";
import { AuthCard } from "./auth-card";
import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Anmelden · TradingEngine" };
export const dynamic = "force-dynamic";

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const m = authMode();
  // lokale Entwicklung ohne Passwort: nichts anzumelden
  if (m.mode === "open") redirect("/");
  const { next } = await searchParams;
  const target = safeNext(next);

  if (m.mode === "unconfigured") {
    return (
      <AuthCard subtitle="Anmeldung nicht konfiguriert">
        <p className="text-center text-sm text-ink-2">Es fehlen Umgebungsvariablen: {m.missing.join(", ")}.</p>
      </AuthCard>
    );
  }

  const session = await getAuth()
    .api.getSession({ headers: await headers() })
    .catch(() => null);
  if (session && session.user.email.toLowerCase() === adminEmail()) {
    redirect((session.user as { twoFactorEnabled?: boolean | null }).twoFactorEnabled ? target : "/setup");
  }

  return (
    <AuthCard subtitle={`Angemeldet bleiben für ${SESSION_EXPIRES_SECONDS / 86_400} Tage, bei Nutzung verlängert`}>
      <LoginForm next={target} />
    </AuthCard>
  );
}
