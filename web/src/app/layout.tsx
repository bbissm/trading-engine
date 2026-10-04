import type { Metadata, Viewport } from "next";
import Link from "next/link";
import { AppShell } from "@/components/app-shell";
import { Icon } from "@/components/icons";
import { LogoutButton } from "@/components/logout-button";
import { ThemeToggle, themeInitScript } from "@/components/theme-toggle";
import "./globals.css";

export const metadata: Metadata = {
  title: "TradingEngine",
  description: "Persönliche Handelsplattform: Signale, Paper- und Live-Autopilot, Lernlabor",
  robots: { index: false, follow: false },
  applicationName: "TradingEngine",
  appleWebApp: { capable: true, title: "Trading", statusBarStyle: "default" },
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // content reaches into notch/home indicator; spacing via env(safe-area-inset-*)
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f9f9f7" },
    { media: "(prefers-color-scheme: dark)", color: "#0d0d0d" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="de-CH" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body className="min-h-dvh">
        <AppShell
          navActions={
            <>
              <ThemeToggle />
              {process.env.TE_PASSWORD && (
                <Link href="/settings/security" title="Sicherheit" aria-label="Sicherheit" className="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-line bg-surface text-muted hover:text-ink">
                  <Icon name="shield" size={15} />
                </Link>
              )}
              {process.env.TE_PASSWORD && <LogoutButton />}
            </>
          }
        >
          {children}
        </AppShell>
      </body>
    </html>
  );
}
