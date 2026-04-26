"use client";

import dynamic from "next/dynamic";

/** Client-only lazy wrapper：recharts 的 LineChart 體積大，個股詳情頁滾到下方才載入即可。 */
export const ScoreTimelineChart = dynamic(
  () => import("./ScoreTimelineChart").then((m) => m.ScoreTimelineChart),
  {
    ssr: false,
    loading: () => (
      <div
        className="rounded-lg bg-subtle animate-pulse"
        style={{ height: 280 }}
        aria-label="分數走勢圖載入中"
      />
    ),
  },
);
