import { appIcon } from "@/lib/app-icon";

/** /pwa-icon/192, /pwa-icon/512, /pwa-icon/maskable — referenced by the manifest. */
export async function GET(_req: Request, { params }: { params: Promise<{ size: string }> }) {
  const { size } = await params;
  if (size === "maskable") return appIcon(512, { inset: 0.3 });
  const n = Number(size);
  if (n !== 192 && n !== 512) return new Response("not found", { status: 404 });
  return appIcon(n);
}
