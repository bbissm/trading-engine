import { authMode } from "@/lib/auth/config";
import { getAuth } from "@/lib/auth/server";

export const dynamic = "force-dynamic";

/** Better Auth (Anmeldung, Passkey, TOTP, Step-up). Vom Proxy ausgenommen, prüft selbst. */
async function handler(req: Request): Promise<Response> {
  const m = authMode();
  if (m.mode !== "enabled") return Response.json({ error: m.mode === "open" ? "Anmeldung lokal deaktiviert (TE_PASSWORD fehlt)" : `Nicht konfiguriert: ${m.missing.join(", ")}` }, { status: 503 });
  return getAuth().handler(req);
}

export { handler as GET, handler as POST };
