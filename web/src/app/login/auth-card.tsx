import type { ReactNode } from "react";

/** Rahmen der Anmelde- und Einrichtungsseiten (ohne App-Navigation). */
export function AuthCard({ subtitle, children, wide = false }: { subtitle?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-8 pb-[max(2rem,env(safe-area-inset-bottom))] pt-[max(2rem,env(safe-area-inset-top))]">
      <div className={`w-full ${wide ? "max-w-md" : "max-w-sm"}`}>
        <div className="mb-8 text-center">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/pwa-icon/192" alt="" width={64} height={64} className="mx-auto mb-4 rounded-2xl" />
          <h1 className="text-xl font-semibold">TradingEngine</h1>
          {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
        </div>
        {children}
      </div>
    </main>
  );
}
