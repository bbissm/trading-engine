import { appIcon } from "@/lib/app-icon";

export const size = { width: 64, height: 64 };
export const contentType = "image/png";

export default function Icon() {
  return appIcon(64, { inset: 0.18, radius: 14 });
}
