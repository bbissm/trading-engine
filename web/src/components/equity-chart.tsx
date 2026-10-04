"use client";

import { ColorType, createChart, CrosshairMode, LineSeries, LineStyle, type Time, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { EquityPoint } from "@/lib/data/paper";

const ZURICH = "Europe/Zurich";
const asDate = (t: Time) => new Date((t as number) * 1000);
const fmtDay = new Intl.DateTimeFormat("de-CH", { timeZone: ZURICH, day: "2-digit", month: "2-digit" });
const fmtMonth = new Intl.DateTimeFormat("de-CH", { timeZone: ZURICH, month: "short", year: "2-digit" });
const fmtYear = new Intl.DateTimeFormat("de-CH", { timeZone: ZURICH, year: "numeric" });
const fmtTime = new Intl.DateTimeFormat("de-CH", { timeZone: ZURICH, hour: "2-digit", minute: "2-digit" });
const fmtFull = new Intl.DateTimeFormat("de-CH", { timeZone: ZURICH, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
const fmtValue = new Intl.NumberFormat("de-CH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Reads the design tokens of the current theme (canvas needs concrete colours). */
function tokens() {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return { surface: v("--surface"), ink2: v("--ink-2"), grid: v("--grid"), axis: v("--axis"), series: v("--s1"), muted: v("--muted") };
}

/**
 * Simulated equity per simulated candle close (stored `equity_snapshot` rows) as one line, with the start capital
 * as a dashed reference line. One series, one axis; nothing is computed in the browser. Times in Europe/Zurich.
 */
export function EquityChart({ points, startCash }: { points: EquityPoint[]; startCash: number }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let t = tokens();

    const chart = createChart(el, {
      autoSize: true,
      layout: { attributionLogo: true, background: { type: ColorType.Solid, color: t.surface }, textColor: t.ink2, fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif', fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { color: t.grid } },
      rightPriceScale: { borderColor: t.axis, scaleMargins: { top: 0.12, bottom: 0.12 } },
      timeScale: {
        borderColor: t.axis,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 2,
        // tick mark types: 0 year, 1 month, 2 day, 3 time, 4 time with seconds
        tickMarkFormatter: (time: Time, type: number) => {
          const d = asDate(time);
          return type === 0 ? fmtYear.format(d) : type === 1 ? fmtMonth.format(d) : type === 2 ? fmtDay.format(d) : fmtTime.format(d);
        },
      },
      localization: { locale: "de-CH", timeFormatter: (time: Time) => fmtFull.format(asDate(time)), priceFormatter: (p: number) => fmtValue.format(p) },
      crosshair: { mode: CrosshairMode.Magnet },
      // one finger scrolls the page vertically; horizontal drag and pinch move/zoom the chart
      handleScroll: { vertTouchDrag: false },
    });

    const series = chart.addSeries(LineSeries, {
      color: t.series,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerRadius: 4,
      // keep the start capital inside the visible range so the reference line never falls off the chart
      autoscaleInfoProvider: (original: () => { priceRange: { minValue: number; maxValue: number } | null } | null) => {
        const info = original();
        if (!info || !info.priceRange) return info;
        return { ...info, priceRange: { minValue: Math.min(info.priceRange.minValue, startCash), maxValue: Math.max(info.priceRange.maxValue, startCash) } };
      },
    });
    series.setData(points.map((p) => ({ time: p.time as UTCTimestamp, value: p.equity })));
    const start = series.createPriceLine({ price: startCash, title: "Startkapital", color: t.muted, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true });
    chart.timeScale().fitContent();

    // follow the theme: data-theme attribute (toggle) and the system preference
    const retheme = () => {
      t = tokens();
      chart.applyOptions({
        layout: { background: { type: ColorType.Solid, color: t.surface }, textColor: t.ink2 },
        grid: { horzLines: { color: t.grid } },
        rightPriceScale: { borderColor: t.axis },
        timeScale: { borderColor: t.axis },
      });
      series.applyOptions({ color: t.series });
      start.applyOptions({ color: t.muted });
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
  }, [points, startCash]);

  return <div ref={ref} className="h-[240px] w-full md:h-[320px]" role="img" aria-label="Simulierte Eigenkapitalkurve mit Startkapital als Referenzlinie" />;
}
