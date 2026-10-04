import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { SESSION_DAYS } from "@/lib/session";
import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Anmelden · TradingEngine" };
export const dynamic = "force-dynamic";

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  // local development without password: nothing to log in to
  if (!process.env.TE_PASSWORD) redirect("/");
  const { next } = await searchParams;
  return (
    <main className="flex min-h-dvh items-center justify-center px-6 pb-[env(safe-area-inset-bottom)] pt-[env(safe-area-inset-top)]">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/pwa-icon/192" alt="" width={64} height={64} className="mx-auto mb-4 rounded-2xl" />
          <h1 className="text-xl font-semibold">TradingEngine</h1>
          <p className="mt-1 text-sm text-muted">Angemeldet bleiben für {SESSION_DAYS} Tage</p>
        </div>
        <LoginForm next={next ?? "/"} />
      </div>
    </main>
  );
}
