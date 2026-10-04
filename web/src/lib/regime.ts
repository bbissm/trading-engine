/**
 * Regime colours (CSS tokens) of the strip in the chart and its legend. Lives outside the client component
 * so server components can read it too. The legend always names the regime in words.
 */
export const REGIME_TOKEN: Record<string, string> = {
  UP: "--good",
  DOWN: "--critical",
  SIDEWAYS: "--other",
  STRESS: "--warning",
  UNKNOWN: "--axis",
};
