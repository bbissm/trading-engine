import { NextResponse, type NextRequest } from "next/server";
import { checkCredentials, createSessionToken, SESSION_COOKIE, sessionCookieOptions, shouldRenew, verifySessionToken } from "@/lib/session";

/**
 * Access to the whole app (UI + server actions): session cookie from the login page,
 * or Basic Auth (scripts, curl).
 * Excluded: login, manifest and app icons (iOS fetches them without a session), and the machine endpoints
 * /api/telegram (checks the Telegram secret header and chat id) and /api/cron/* (checks the CRON_SECRET bearer).
 *
 * TODO(E0-4): interim password session — passkey + TOTP + step-up (Better Auth) must replace this
 * before any live-trading control exists. See src/lib/session.ts.
 */
export async function proxy(req: NextRequest) {
  if (!process.env.TE_PASSWORD) {
    if (process.env.VERCEL_ENV === "production" || process.env.VERCEL_ENV === "preview") {
      return new NextResponse("TE_PASSWORD is not configured", { status: 503 });
    }
    return NextResponse.next(); // local development
  }

  const exp = await verifySessionToken(req.cookies.get(SESSION_COOKIE)?.value);
  if (exp) {
    const res = NextResponse.next();
    // sliding session: whoever uses the app stays logged in
    if (shouldRenew(exp)) {
      const { token, expires } = await createSessionToken();
      res.cookies.set(SESSION_COOKIE, token, sessionCookieOptions(expires));
    }
    return res;
  }

  const header = req.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    const [u, ...rest] = atob(header.slice(6)).split(":");
    if (checkCredentials(u, rest.join(":"))) return NextResponse.next();
  }

  if (req.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "Authentication required" }, { status: 401 });
  }
  const login = req.nextUrl.clone();
  login.pathname = "/login";
  login.search = "";
  const next = req.nextUrl.pathname + req.nextUrl.search;
  if (next !== "/") login.searchParams.set("next", next);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: [{ source: "/((?!_next/|favicon.ico|icon|apple-icon|pwa-icon/|manifest.webmanifest|login|api/telegram|api/cron/).*)" }],
};
