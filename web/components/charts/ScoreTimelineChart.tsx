"use client";

import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ScoreHistoryPoint } from "@/lib/api";

type SeriesKey = "short" | "mid" | "long" | "composite";
const LABEL: Record<SeriesKey, string> = {
  short: "短期",
  mid: "中期",
  long: "長期",
  composite: "綜合",
};
const COLOR: Record<SeriesKey, string> = {
  short:     "var(--chart-series-short)",
  mid:       "var(--chart-series-mid)",
  long:      "var(--chart-series-long)",
  composite: "var(--chart-series-composite)",
};

export function ScoreTimelineChart({ data, height = 260 }: { data: ScoreHistoryPoint[]; height?: number }) {
  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center text-sm text-[var(--text-tertiary)]" style={{ height }}>
        暫無分數歷史資料
      </div>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
        <XAxis
          dataKey="date"
          tick={{ fill: "var(--chart-axis)", fontSize: 11 }}
          stroke="var(--chart-axis)"
          tickLine={false}
          minTickGap={40}
        />
        <YAxis
          domain={[0, 100]}
          tick={{ fill: "var(--chart-axis)", fontSize: 11 }}
          stroke="var(--chart-axis)"
          tickLine={false}
          width={32}
        />
        <ReferenceLine y={70} stroke="var(--down-400)" strokeDasharray="4 4" opacity={0.4} />
        <ReferenceLine y={50} stroke="var(--chart-grid)" strokeDasharray="4 4" />
        <ReferenceLine y={30} stroke="var(--up-400)" strokeDasharray="4 4" opacity={0.4} />
        <Tooltip
          contentStyle={{
            background: "var(--chart-tooltip-bg)",
            border: "none",
            borderRadius: 6,
            color: "var(--chart-tooltip-fg)",
            fontSize: 12,
          }}
          labelStyle={{ color: "var(--chart-tooltip-fg)", opacity: 0.7 }}
        />
        {(["short", "mid", "long", "composite"] as SeriesKey[]).map((k) => (
          <Line
            key={k}
            type="monotone"
            dataKey={k}
            stroke={COLOR[k]}
            strokeWidth={k === "composite" ? 2.5 : 1.8}
            dot={false}
            isAnimationActive={false}
            name={LABEL[k]}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
