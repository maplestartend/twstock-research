"use client";

import dynamic from "next/dynamic";

/** Client-only lazy wrapper：recharts 的 LineChart 用到才載，
 *  讓 /diagnostics 的 IC 表格（純 server HTML）不必把 recharts 打進初始 bundle。 */
export const RollingICChart = dynamic(
  () => import("./RollingICChart").then((m) => m.RollingICChart),
  {
    ssr: false,
    loading: () => (
      <div
        className="rounded-lg bg-subtle animate-pulse"
        style={{ height: 320 }}
        aria-label="Rolling IC 圖載入中"
      />
    ),
  },
);
