import { logoutAction } from "@/app/login/actions";
import { Icon } from "./icons";

export function LogoutButton({ variant = "icon" }: { variant?: "icon" | "row" }) {
  return (
    <form action={logoutAction}>
      {variant === "icon" ? (
        <button type="submit" title="Abmelden" aria-label="Abmelden" className="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-line bg-surface text-muted hover:text-ink">
          <Icon name="logout" size={15} />
        </button>
      ) : (
        <button type="submit" className="flex min-h-12 w-full items-center justify-center gap-2 rounded-xl border border-line bg-surface text-[15px] font-medium text-critical active:bg-surface-2">
          <Icon name="logout" size={20} />
          Abmelden
        </button>
      )}
    </form>
  );
}
