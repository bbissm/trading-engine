/**
 * Kennung des einzigen Benutzers für `command.issued_by` und fachliche Audit-Einträge (`user:<name>`).
 *
 * Die Anmeldung selbst (Better Auth: Passwort + TOTP oder Passkey, Step-up) liegt in src/lib/auth/.
 * Weil es genau einen Benutzer gibt, ist die Kennung eine Konfiguration und keine Sitzungsabfrage:
 * TE_USER, sonst der lokale Teil von TE_ADMIN_EMAIL, sonst "admin". Bleibt stabil, solange sich die
 * Konfiguration nicht ändert. Zugriffskontrolle macht der Proxy (src/proxy.ts), nicht diese Funktion.
 */
export const userName = (): string => {
  const configured = process.env.TE_USER?.trim();
  if (configured) return configured;
  const local = process.env.TE_ADMIN_EMAIL?.trim().toLowerCase().split("@")[0];
  return local || "admin";
};
