"use client";

import dynamic from "next/dynamic";

/** Client-only lazy wrapper：recharts 的 ComposedChart + Scatter 用到才載。 */
export const BacktestEquityChart = dynamic(
  () => import("./BacktestEquityChart").then((m) => m.BacktestEquityChart),
  {
    ssr: false,
    loading: () => (
      <div
        className="rounded-lg bg-subtle animate-pulse"
        style={{ height: 340 }}
        aria-label="回測權益圖載入中"
      />
    ),
  },
);
