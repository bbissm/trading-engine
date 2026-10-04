import Link from "next/link";
import { Icon } from "@/components/icons";
import { LogoutButton } from "@/components/logout-button";
import { ThemeToggle } from "@/components/theme-toggle";
import { PageHeader } from "@/components/ui";
import { MORE } from "@/lib/nav";

export const dynamic = "force-dynamic";

/** "Mehr" (gear in the mobile tab bar): remaining sections, theme, logout. */
export default function MorePage() {
  return (
    <div className="space-y-5">
      <PageHeader title="Mehr" />
      <ul className="overflow-hidden rounded-xl border border-line bg-surface">
        {MORE.map((m) => (
          <li key={m.href} className="border-b border-line last:border-b-0">
            <Link href={m.href} className="flex min-h-14 items-center gap-3 px-4 active:bg-surface-2">
              <Icon name={m.icon} size={22} className="text-muted" />
              <span className="text-[15px] font-medium">{m.label}</span>
              <Icon name="chevronRight" size={18} className="ml-auto text-muted" />
            </Link>
          </li>
        ))}
      </ul>
      <div className="flex min-h-14 items-center justify-between rounded-xl border border-line bg-surface px-4">
        <span className="text-[15px] font-medium">Farbschema</span>
        <ThemeToggle />
      </div>
      {process.env.TE_PASSWORD && (
        <Link href="/settings/security" className="flex min-h-14 items-center gap-3 rounded-xl border border-line bg-surface px-4 active:bg-surface-2">
          <Icon name="shield" size={22} className="text-muted" />
          <span className="text-[15px] font-medium">Sicherheit</span>
          <span className="text-xs text-muted">Passkeys, TOTP, Sitzungen</span>
          <Icon name="chevronRight" size={18} className="ml-auto text-muted" />
        </Link>
      )}
      {process.env.TE_PASSWORD && <LogoutButton variant="row" />}
    </div>
  );
}
