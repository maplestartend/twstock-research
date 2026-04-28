"use client";

import { useEffect, useRef } from "react";
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

export function CandlestickChart({ ohlcv, indicators = [], height = 360 }: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!hostRef.current) return;
    const el = hostRef.current;
    // 讀取目前主題的 token 色
    const cs = getComputedStyle(document.documentElement);
    const up = cs.getPropertyValue("--up-500").trim() || "#D9342B";
    const down = cs.getPropertyValue("--down-500").trim() || "#16A34A";
    const grid = cs.getPropertyValue("--chart-grid").trim() || "#E3E8EF";
    const axis = cs.getPropertyValue("--chart-axis").trim() || "#697586";
    const fg = cs.getPropertyValue("--text-primary").trim() || "#121926";
    const bg = cs.getPropertyValue("--bg-surface").trim() || "#FFFFFF";
    const ma20Color = cs.getPropertyValue("--chart-ma20").trim() || "#2F4A80";
    const ma60Color = cs.getPropertyValue("--chart-ma60").trim() || "#7048E8";

    const chart: IChartApi = createChart(el, {
      width: el.clientWidth,
      height,
      layout: { background: { color: bg }, textColor: fg, fontFamily: "var(--font-sans)" },
      rightPriceScale: { borderColor: grid },
      timeScale: { borderColor: grid, timeVisible: false },
      grid: { horzLines: { color: grid }, vertLines: { color: grid } },
      crosshair: { mode: 1 },
    });

    // 台股慣例：上漲實心紅、下跌實心綠
    const candle: ISeriesApi<"Candlestick"> = chart.addSeries(CandlestickSeries, {
      upColor: up,
      borderUpColor: up,
      wickUpColor: up,
      downColor: down,
      borderDownColor: down,
      wickDownColor: down,
    });
    candle.setData(
      ohlcv.map((p) => ({
        time: toTime(p.date),
        open: p.open,
        high: p.high,
        low: p.low,
        close: p.close,
      })),
    );

    // 均線
    if (indicators.length > 0) {
      const ma20 = chart.addSeries(LineSeries, {
        color: ma20Color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      ma20.setData(
        indicators.filter((i) => i.ma20 != null).map((i) => ({
          time: toTime(i.date),
          value: i.ma20 as number,
        })),
      );
      const ma60 = chart.addSeries(LineSeries, {
        color: ma60Color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      ma60.setData(
        indicators.filter((i) => i.ma60 != null).map((i) => ({
          time: toTime(i.date),
          value: i.ma60 as number,
        })),
      );
    }

    // 成交量 sub chart
    // DB 存的是 TWSE/TPEX「成交股數」（股），但台股慣例以「張」報量（1 張 = 1000 股）。
    // 直接顯示原始股數會讓 2330 之類的權值股出現 ~80M 的讀數，跟看盤軟體差 1000 倍 → /1000 轉張。
    const vol: ISeriesApi<"Histogram"> = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(
      ohlcv.map((p) => ({
        time: toTime(p.date),
        value: (p.volume ?? 0) / 1000,
        color: p.close >= p.open ? up : down,
      })),
    );

    chart.timeScale().fitContent();

    const onResize = () => chart.resize(el.clientWidth, height);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
    };
  }, [ohlcv, indicators, height]);

  return <div ref={hostRef} className="w-full" style={{ height }} />;
}
