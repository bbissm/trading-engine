import { NextResponse, type NextRequest } from "next/server";
import { isValidApiToken } from "@/lib/auth/api-token";
import { adminEmail, authMode, isSelfAuthenticatedPath } from "@/lib/auth/config";
import { getAuth } from "@/lib/auth/server";

/**
 * Zugriffsschutz für die ganze App (Seiten, Server Actions, API). Läuft in der Node.js-Runtime (Standard für
 * Proxy ab Next.js 16) und prüft die Sitzung bei jeder Anfrage in der DB (Better Auth, kein Cookie-Cache),
 * damit ein Widerruf sofort wirkt.
 *
 * - Sitzung (Cookie) mit eingerichtetem TOTP → Zugriff. Gleitende Verlängerung: neue Cookies werden weitergereicht.
 * - Sitzung ohne TOTP (erster Login) → nur /setup.
 * - `/api/ops/*` zusätzlich mit `Authorization: Bearer <TE_API_TOKEN>` (Skripte). Basic Auth gibt es nicht mehr.
 * - Ausgenommen (siehe matcher und isSelfAuthenticatedPath): /login, /api/auth/* (Better Auth), /api/telegram,
 *   /api/cron/*, Icons und Manifest (iOS lädt sie ohne Sitzung).
 * - Ohne TE_PASSWORD lokal offen; auf Vercel ohne vollständige Konfiguration 503.
 */
export async function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (isSelfAuthenticatedPath(pathname)) return NextResponse.next();

  const m = authMode();
  if (m.mode === "open") return NextResponse.next(); // lokale Entwicklung
  if (m.mode === "unconfigured") return new NextResponse(`Anmeldung nicht konfiguriert: ${m.missing.join(", ")}`, { status: 503 });

  const isApi = pathname.startsWith("/api/");
  const authorization = req.headers.get("authorization");
  if (authorization) {
    // Maschinenzugang nur für /api/ops/*, nur mit Bearer-Token; ein Header ohne gültiges Token wird nie auf die Sitzung zurückgestuft
    if (pathname.startsWith("/api/ops/") && isValidApiToken(authorization)) return NextResponse.next();
    return NextResponse.json({ error: "Authentication required" }, { status: 401, headers: { "www-authenticate": 'Bearer realm="tradingengine"' } });
  }

  let result: { headers: Headers; response: { user: { email: string; twoFactorEnabled?: boolean | null } } | null };
  try {
    result = (await getAuth().api.getSession({ headers: req.headers, returnHeaders: true })) as typeof result;
  } catch (e) {
    console.error("[proxy] session check failed", e);
    return new NextResponse("Sitzung konnte nicht geprüft werden", { status: 503 });
  }
  const user = result.response?.user;
  const valid = !!user && user.email.toLowerCase() === adminEmail();

  let res: NextResponse;
  if (!valid) {
    if (isApi) res = NextResponse.json({ error: "Authentication required" }, { status: 401 });
    else {
      const login = req.nextUrl.clone();
      login.pathname = "/login";
      login.search = "";
      const next = pathname + req.nextUrl.search;
      if (next !== "/") login.searchParams.set("next", next);
      res = NextResponse.redirect(login);
    }
  } else if (!user.twoFactorEnabled && pathname !== "/setup") {
    // erster Login: TOTP einrichten, bevor irgendetwas anderes erreichbar ist
    if (isApi) res = NextResponse.json({ error: "TOTP setup required" }, { status: 403 });
    else {
      const setup = req.nextUrl.clone();
      setup.pathname = "/setup";
      setup.search = "";
      res = NextResponse.redirect(setup);
    }
  } else res = NextResponse.next();

  // gleitende Sitzung: von Better Auth erneuerte Cookies mitsenden
  for (const cookie of result.headers?.getSetCookie?.() ?? []) res.headers.append("set-cookie", cookie);
  return res;
}

export const config = {
  matcher: [{ source: "/((?!_next/|favicon.ico|icon|apple-icon|pwa-icon/|manifest.webmanifest|login|api/auth/|api/telegram|api/cron/).*)" }],
};
