"use client";

import { useMemo } from "react";
import {
  ComposedChart,
  Line,
  Scatter,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ResponsiveContainer,
} from "recharts";
import type { BacktestDailyPoint, BacktestTrade } from "@/lib/api";

type Props = {
  daily: BacktestDailyPoint[];
  trades: BacktestTrade[];
  height?: number;
};

export function BacktestEquityChart({ daily, trades, height = 340 }: Props) {
  // 以 date 為 x，close 為主線。交易進場/出場點作為 Scatter 覆蓋。
  // priceSeries / dateIndex / entryPoints / exitPoints 都只跟 (daily, trades) 相關，
  // 用 useMemo 避免每次 re-render 重做；dateIndex 把 daily.find O(N) 換成 Map.get O(1)，
  // 大型回測（1000+ 筆 trade × 1000+ daily point）省 N×M。
  const priceSeries = useMemo(
    () => daily.map((d) => ({ date: d.date, close: d.close, short_score: d.shortScore })),
    [daily],
  );

  const dateIndex = useMemo(() => {
    const m = new Map<string, BacktestDailyPoint>();
    for (const d of daily) m.set(d.date, d);
    return m;
  }, [daily]);

  const entryPoints = useMemo(
    () =>
      trades.map((t) => {
        const point = dateIndex.get(t.entryDate);
        return {
          date: t.entryDate,
          close: point?.close ?? t.entryPrice,
          net: t.netReturn,
        };
      }),
    [trades, dateIndex],
  );
  const exitPoints = useMemo(
    () =>
      trades.map((t) => {
        const point = dateIndex.get(t.exitDate);
        return {
          date: t.exitDate,
          close: point?.close ?? t.exitPrice,
          net: t.netReturn,
        };
      }),
    [trades, dateIndex],
  );

  // early return 必須在 hook 之後（避免 hook 順序變動）
  if (daily.length === 0) {
    return (
      <div className="flex items-center justify-center text-sm text-[var(--text-tertiary)]" style={{ height }}>
        無時序資料
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={priceSeries} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
        <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
        <XAxis
          dataKey="date"
          tick={{ fill: "var(--chart-axis)", fontSize: 11 }}
          stroke="var(--chart-axis)"
          tickLine={false}
          minTickGap={60}
        />
        <YAxis
          tick={{ fill: "var(--chart-axis)", fontSize: 11 }}
          stroke="var(--chart-axis)"
          tickLine={false}
          width={48}
          domain={["auto", "auto"]}
        />
        <Tooltip
          contentStyle={{
            background: "var(--chart-tooltip-bg)",
            border: "none",
            borderRadius: 6,
            color: "var(--chart-tooltip-fg)",
            fontSize: 12,
          }}
          labelStyle={{ color: "var(--chart-tooltip-fg)", opacity: 0.7 }}
          formatter={(v: number) => v?.toFixed?.(2) ?? v}
        />
        <Line
          type="monotone"
          dataKey="close"
          stroke="var(--brand-500)"
          strokeWidth={1.8}
          dot={false}
          isAnimationActive={false}
          name="收盤"
        />
        <Scatter
          data={entryPoints}
          dataKey="close"
          fill="var(--up-500)"
          shape={({ cx, cy }: { cx?: number; cy?: number }) => (
            <g>
              <circle cx={cx} cy={cy} r={5} fill="var(--up-500)" stroke="#fff" strokeWidth={1.5} />
            </g>
          )}
          name="進場"
        />
        <Scatter
          data={exitPoints}
          dataKey="close"
          shape={({ cx, cy, payload }: { cx?: number; cy?: number; payload?: { net: number } }) => {
            const color = (payload?.net ?? 0) >= 0 ? "var(--up-600)" : "var(--down-600)";
            return (
              <g>
                <circle cx={cx} cy={cy} r={5} fill={color} stroke="#fff" strokeWidth={1.5} />
              </g>
            );
          }}
          name="出場"
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
