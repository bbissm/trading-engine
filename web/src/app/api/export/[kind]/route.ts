import type { NextRequest } from "next/server";
import { hasDb } from "@/db/client";
import { buildExport, EXPORT_KINDS, isExportKind, parseExportFilter } from "@/lib/data/export";

export const dynamic = "force-dynamic";

/**
 * CSV export: /api/export/{orders|fills|trades|fees|fx|year}?account=&episode=&year=
 * Read-only; protected by the app login like every other route (src/proxy.ts). Every row carries the mode.
 */
export async function GET(req: NextRequest, { params }: { params: Promise<{ kind: string }> }) {
  const { kind } = await params;
  if (!isExportKind(kind)) return Response.json({ error: `Unbekannter Export. Erlaubt: ${EXPORT_KINDS.join(", ")}` }, { status: 404 });
  if (!hasDb()) return Response.json({ error: "DATABASE_URL is not configured" }, { status: 503 });
  try {
    const file = await buildExport(kind, parseExportFilter(req.nextUrl.searchParams));
    return new Response(file.csv, {
      headers: {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": `attachment; filename="${file.filename}"`,
        "Cache-Control": "no-store",
        "X-Row-Count": String(file.rows),
      },
    });
  } catch (e) {
    console.error("[export]", e);
    return Response.json({ error: "Export fehlgeschlagen." }, { status: 500 });
  }
}
