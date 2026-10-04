"use client";

import { CandlestickSeries, ColorType, createChart, createSeriesMarkers, CrosshairMode, HistogramSeries, LineStyle, type Time, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { ChartCandle, ChartMarker, ChartRegime } from "@/lib/data/instrument";
import { REGIME_TOKEN } from "@/lib/regime";

export interface PriceLine {
  price: number;
  title: string;
  kind: "stop" | "entry";
}

const ZURICH = "Europe/Zurich";
const asDate = (t: Time) => new Date((t as number) * 1000);
const fmtDay = new Intl.DateTimeFormat("de-CH", { timeZone: ZURICH, day: "2-digit", month: "2-digit" });
const fmtMonth = new Intl.DateTimeFormat("de-CH", { timeZone: ZURICH, month: "short", year: "2-digit" });
const fmtYear = new Intl.DateTimeFormat("de-CH", { timeZone: ZURICH, year: "numeric" });
const fmtTime = new Intl.DateTimeFormat("de-CH", { timeZone: ZURICH, hour: "2-digit", minute: "2-digit" });
const fmtFull = new Intl.DateTimeFormat("de-CH", { timeZone: ZURICH, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });

/** Reads the design tokens of the current theme (canvas needs concrete colours). */
function tokens() {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return { surface: v("--surface"), ink2: v("--ink-2"), ink: v("--ink"), grid: v("--grid"), axis: v("--axis"), good: v("--good"), critical: v("--critical"), accent: v("--accent"), serious: v("--serious"), v };
}

/**
 * Candlestick chart (lightweight-charts, Apache-2.0; attribution via `attributionLogo`).
 * Draws only what the server passes in: stored candles, markers from stored `signal` rows,
 * stop/entry of the latest stored BUY signal and stored regimes. Nothing is computed in the browser.
 * Times are shown in Europe/Zurich.
 */
export function CandleChart({ candles, markers, regimes, lines }: { candles: ChartCandle[]; markers: ChartMarker[]; regimes: ChartRegime[]; lines: PriceLine[] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let t = tokens();

    const chart = createChart(el, {
      autoSize: true,
      layout: { attributionLogo: true, background: { type: ColorType.Solid, color: t.surface }, textColor: t.ink2, fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif', fontSize: 11 },
      grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
      rightPriceScale: { borderColor: t.axis, scaleMargins: { top: 0.1, bottom: 0.08 } },
      timeScale: {
        borderColor: t.axis,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 3,
        // tick mark types: 0 year, 1 month, 2 day, 3 time, 4 time with seconds
        tickMarkFormatter: (time: Time, type: number) => {
          const d = asDate(time);
          return type === 0 ? fmtYear.format(d) : type === 1 ? fmtMonth.format(d) : type === 2 ? fmtDay.format(d) : fmtTime.format(d);
        },
      },
      localization: { locale: "de-CH", timeFormatter: (time: Time) => fmtFull.format(asDate(time)) },
      crosshair: { mode: CrosshairMode.Normal },
      // one finger scrolls the page vertically; horizontal drag and pinch move/zoom the chart
      handleScroll: { vertTouchDrag: false },
    });

    const series = chart.addSeries(CandlestickSeries, { upColor: t.good, downColor: t.critical, wickUpColor: t.good, wickDownColor: t.critical, borderVisible: false, priceLineVisible: false });
    series.setData(candles.map((c) => ({ ...c, time: c.time as UTCTimestamp })));

    // regime strip: thin band at the top on its own hidden scale (a category, not a second measure)
    const strip = chart.addSeries(HistogramSeries, { priceScaleId: "regime", priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
    strip.priceScale().applyOptions({ scaleMargins: { top: 0, bottom: 0.965 }, visible: false });
    const setRegimes = () => strip.setData(regimes.map((r) => ({ time: r.time as UTCTimestamp, value: 1, color: t.v(REGIME_TOKEN[r.regime] ?? "--axis") })));
    setRegimes();

    const marks = createSeriesMarkers(series, []);
    const setMarkers = () => marks.setMarkers(markers.map((m) => ({ time: m.time as UTCTimestamp, position: "belowBar" as const, shape: "arrowUp" as const, color: t.accent, text: m.text })));
    setMarkers();

    const lineColor = (kind: PriceLine["kind"]) => (kind === "stop" ? t.critical : t.accent);
    const priceLines = lines.map((l) => ({ l, api: series.createPriceLine({ price: l.price, title: l.title, color: lineColor(l.kind), lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true }) }));

    // phones show the latest ~60 candles, wider screens ~160; everything else via drag/pinch
    const visible = Math.min(candles.length, el.clientWidth < 500 ? 60 : 160);
    if (candles.length) chart.timeScale().setVisibleLogicalRange({ from: candles.length - visible, to: candles.length + 2 });

    // follow the theme: data-theme attribute (toggle) and the system preference
    const retheme = () => {
      t = tokens();
      chart.applyOptions({
        layout: { background: { type: ColorType.Solid, color: t.surface }, textColor: t.ink2 },
        grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
        rightPriceScale: { borderColor: t.axis },
        timeScale: { borderColor: t.axis },
      });
      series.applyOptions({ upColor: t.good, downColor: t.critical, wickUpColor: t.good, wickDownColor: t.critical });
      setRegimes();
      setMarkers();
      for (const p of priceLines) p.api.applyOptions({ color: lineColor(p.l.kind) });
    };
    const observer = new MutationObserver(retheme);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", retheme);

    return () => {
      observer.disconnect();
      media.removeEventListener("change", retheme);
      chart.remove();
    };
  }, [candles, markers, regimes, lines]);

  return <div ref={ref} className="h-[340px] w-full md:h-[480px]" role="img" aria-label="Kerzenchart mit gespeicherten Signalmarkern, Stop-Linie und Regime-Streifen" />;
}
