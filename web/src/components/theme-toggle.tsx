"use client";

import { useEffect, useState } from "react";

type Theme = "system" | "light" | "dark";
const KEY = "theme";
const OPTIONS: { id: Theme; label: string; icon: string }[] = [
  { id: "light", label: "Hell", icon: "☀" },
  { id: "system", label: "System", icon: "◐" },
  { id: "dark", label: "Dunkel", icon: "☾" },
];

function apply(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
}

/** Runs before first paint (in <head>) so the stored theme never flashes. */
export const themeInitScript = `try{var t=localStorage.getItem("${KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    try {
      const stored = localStorage.getItem(KEY);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- sync with localStorage after hydration
      if (stored === "light" || stored === "dark") setTheme(stored);
    } catch {}
  }, []);

  const choose = (t: Theme) => {
    setTheme(t);
    apply(t);
    try {
      if (t === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, t);
    } catch {}
  };

  return (
    <div role="radiogroup" aria-label="Farbschema" className="inline-flex rounded-lg border border-line bg-surface p-0.5">
      {OPTIONS.map((o) => (
        <button
          key={o.id}
          type="button"
          role="radio"
          aria-checked={theme === o.id}
          title={o.label}
          onClick={() => choose(o.id)}
          className={`inline-flex items-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-xs ${theme === o.id ? "bg-surface-2 font-medium text-ink" : "text-muted hover:text-ink"}`}
        >
          <span aria-hidden>{o.icon}</span> <span className="sr-only">{o.label}</span>
        </button>
      ))}
    </div>
  );
}
