import { ImageResponse } from "next/og";

/**
 * App icon (home screen, favicon, manifest), rendered on demand: accent square with three rising bars.
 * `inset` shrinks the glyph for maskable icons (Android crops to a circle).
 */
export function appIcon(size: number, { inset = 0.22, radius = 0 }: { inset?: number; radius?: number } = {}) {
  const pad = Math.round(size * inset);
  const gap = Math.round(size * 0.06);
  const bar = Math.round((size - 2 * pad - 2 * gap) / 3);
  const inner = size - 2 * pad;
  return new ImageResponse(
    (
      <div style={{ width: size, height: size, display: "flex", alignItems: "flex-end", justifyContent: "center", gap, padding: pad, background: "#2a78d6", borderRadius: radius }}>
        {[0.42, 0.68, 1].map((h) => (
          <div key={h} style={{ width: bar, height: Math.round(inner * h), background: "#ffffff", borderRadius: Math.round(bar * 0.22) }} />
        ))}
      </div>
    ),
    { width: size, height: size },
  );
}
