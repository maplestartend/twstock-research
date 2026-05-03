"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { OHLCV, IndicatorPoint } from "@/lib/api";

type Props = {
  ohlcv: OHLCV[];
  indicators?: IndicatorPoint[];
  height?: number;
};

function toTime(date: string): UTCTimestamp {
  return (Math.floor(new Date(date).getTime() / 1000)) as UTCTimestamp;
}

function readTheme(): string {
  if (typeof document === "undefined") return "light";
  return document.documentElement.dataset.theme ?? "light";
}

type ThemeTokens = {
  up: string;
  down: string;
  grid: string;
  fg: string;
  bg: string;
  ma20Color: string;
  ma60Color: string;
};

function readThemeTokens(): ThemeTokens {
  if (typeof document === "undefined") {
    return {
      up: "#D9342B",
      down: "#16A34A",
      grid: "#E3E8EF",
      fg: "#121926",
      bg: "#FFFFFF",
      ma20Color: "#2F4A80",
      ma60Color: "#7048E8",
    };
  }
  const cs = getComputedStyle(document.documentElement);
  return {
    up: cs.getPropertyValue("--up-500").trim() || "#D9342B",
    down: cs.getPropertyValue("--down-500").trim() || "#16A34A",
    grid: cs.getPropertyValue("--chart-grid").trim() || "#E3E8EF",
    fg: cs.getPropertyValue("--text-primary").trim() || "#121926",
    bg: cs.getPropertyValue("--bg-surface").trim() || "#FFFFFF",
    ma20Color: cs.getPropertyValue("--chart-ma20").trim() || "#2F4A80",
    ma60Color: cs.getPropertyValue("--chart-ma60").trim() || "#7048E8",
  };
}

export function CandlestickChart({ ohlcv, indicators = [], height = 360 }: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const ma20Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ma60Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const volRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const [theme, setTheme] = useState<string>("light");
  const candleData = useMemo(
    () =>
      ohlcv.map((p) => ({
        time: toTime(p.date),
        open: p.open,
        high: p.high,
        low: p.low,
        close: p.close,
      })),
    [ohlcv],
  );
  const ma20Data = useMemo(
    () =>
      indicators
        .filter((i) => i.ma20 != null)
        .map((i) => ({ time: toTime(i.date), value: i.ma20 as number })),
    [indicators],
  );
  const ma60Data = useMemo(
    () =>
      indicators
        .filter((i) => i.ma60 != null)
        .map((i) => ({ time: toTime(i.date), value: i.ma60 as number })),
    [indicators],
  );

  useEffect(() => {
    setTheme(readTheme());
    const mo = new MutationObserver(() => setTheme(readTheme()));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
  }, []);

  useEffect(() => {
    if (!hostRef.current) return;
    if (chartRef.current) return;
    const el = hostRef.current;
    const t = readThemeTokens();

    const chart: IChartApi = createChart(el, {
      width: el.clientWidth,
      height,
      layout: { background: { color: t.bg }, textColor: t.fg, fontFamily: "var(--font-sans)" },
      rightPriceScale: { borderColor: t.grid },
      timeScale: { borderColor: t.grid, timeVisible: false },
      grid: { horzLines: { color: t.grid }, vertLines: { color: t.grid } },
      crosshair: { mode: 1 },
    });
    chartRef.current = chart;

    // 台股慣例：上漲實心紅、下跌實心綠
    const candle: ISeriesApi<"Candlestick"> = chart.addSeries(CandlestickSeries, {
      upColor: t.up,
      borderUpColor: t.up,
      wickUpColor: t.up,
      downColor: t.down,
      borderDownColor: t.down,
      wickDownColor: t.down,
    });
    candleRef.current = candle;

    // 均線
    const ma20 = chart.addSeries(LineSeries, {
      color: t.ma20Color,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const ma60 = chart.addSeries(LineSeries, {
      color: t.ma60Color,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    ma20Ref.current = ma20;
    ma60Ref.current = ma60;

    // 成交量 sub chart
    // DB 存的是 TWSE/TPEX「成交股數」（股），但台股慣例以「張」報量（1 張 = 1000 股）。
    // 直接顯示原始股數會讓 2330 之類的權值股出現 ~80M 的讀數，跟看盤軟體差 1000 倍 → /1000 轉張。
    const vol: ISeriesApi<"Histogram"> = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });
    volRef.current = vol;
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

    const onResize = () => chart.resize(el.clientWidth, height);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      ma20Ref.current = null;
      ma60Ref.current = null;
      volRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    const chart = chartRef.current;
    const el = hostRef.current;
    if (!chart || !el) return;
    chart.applyOptions({ height });
    chart.resize(el.clientWidth, height);
  }, [height]);

  useEffect(() => {
    const chart = chartRef.current;
    const candle = candleRef.current;
    const ma20 = ma20Ref.current;
    const ma60 = ma60Ref.current;
    const vol = volRef.current;
    if (!chart || !candle || !ma20 || !ma60 || !vol) return;
    candle.setData(candleData);
    ma20.setData(ma20Data);
    ma60.setData(ma60Data);
    const t = readThemeTokens();
    vol.setData(
      ohlcv.map((p) => ({
        time: toTime(p.date),
        value: (p.volume ?? 0) / 1000,
        color: p.close >= p.open ? t.up : t.down,
      })),
    );
    chart.timeScale().fitContent();
  }, [ohlcv, candleData, ma20Data, ma60Data]);

  useEffect(() => {
    const chart = chartRef.current;
    const candle = candleRef.current;
    const ma20 = ma20Ref.current;
    const ma60 = ma60Ref.current;
    const vol = volRef.current;
    if (!chart || !candle || !ma20 || !ma60 || !vol) return;
    const t = readThemeTokens();
    chart.applyOptions({
      layout: { background: { color: t.bg }, textColor: t.fg, fontFamily: "var(--font-sans)" },
      rightPriceScale: { borderColor: t.grid },
      timeScale: { borderColor: t.grid, timeVisible: false },
      grid: { horzLines: { color: t.grid }, vertLines: { color: t.grid } },
    });
    candle.applyOptions({
      upColor: t.up,
      borderUpColor: t.up,
      wickUpColor: t.up,
      downColor: t.down,
      borderDownColor: t.down,
      wickDownColor: t.down,
    });
    ma20.applyOptions({ color: t.ma20Color });
    ma60.applyOptions({ color: t.ma60Color });
    vol.setData(
      ohlcv.map((p) => ({
        time: toTime(p.date),
        value: (p.volume ?? 0) / 1000,
        color: p.close >= p.open ? t.up : t.down,
      })),
    );
  }, [theme, ohlcv]);

  return <div ref={hostRef} className="w-full" style={{ height }} />;
}
