"use client";

import dynamic from "next/dynamic";

/** Client-only lazy wrapper：lightweight-charts ~160 kB 的 bundle 只在實際渲染時載入。
 * 直接 import CandlestickChart 會把整包 lightweight-charts 拉進 First Load JS。 */
export const CandlestickChart = dynamic(
  () => import("./CandlestickChart").then((m) => m.CandlestickChart),
  {
    ssr: false,
    loading: () => (
      <div
        className="rounded-lg bg-subtle animate-pulse"
        style={{ height: 380 }}
        aria-label="K 線圖載入中"
      />
    ),
  },
);
