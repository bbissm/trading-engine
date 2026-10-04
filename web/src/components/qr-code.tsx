import { encode } from "uqr";

/** QR-Code als SVG (immer schwarz auf weiss, damit Kamera-Apps ihn auch im dunklen Modus lesen). */
export function QrCode({ value, size = 208, label }: { value: string; size?: number; label: string }) {
  const qr = encode(value, { ecc: "M", border: 2 });
  let d = "";
  qr.data.forEach((row, y) =>
    row.forEach((on, x) => {
      if (on) d += `M${x} ${y}h1v1h-1z`;
    }),
  );
  return (
    <svg role="img" aria-label={label} viewBox={`0 0 ${qr.size} ${qr.size}`} width={size} height={size} shapeRendering="crispEdges" className="rounded-lg">
      <rect width={qr.size} height={qr.size} fill="#fff" />
      <path d={d} fill="#000" />
    </svg>
  );
}
