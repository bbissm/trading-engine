import type { MetadataRoute } from "next";

/** Home-screen app (iOS/Android): own window without browser bars, starts on the overview. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "TradingEngine",
    short_name: "Trading",
    description: "Persönliche Handelsplattform: Signale, Paper- und Live-Autopilot, Lernlabor",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#f9f9f7",
    theme_color: "#f9f9f7",
    icons: [
      { src: "/pwa-icon/192", sizes: "192x192", type: "image/png" },
      { src: "/pwa-icon/512", sizes: "512x512", type: "image/png" },
      { src: "/pwa-icon/maskable", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
