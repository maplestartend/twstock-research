"use client";

import { useMemo } from "react";
import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend } from "recharts";
import type { RollingICRow } from "@/lib/api";

type SeriesKey = "short" | "mid" | "long" | "composite" | "vrMacd";
const LABEL: Record<SeriesKey, string> = {
  short: "短期",
  mid: "中期",
  long: "長期",
  composite: "綜合",
  vrMacd: "VR",
};
const COLOR: Record<SeriesKey, string> = {
  short:     "var(--chart-series-short)",
  mid:       "var(--chart-series-mid)",
  long:      "var(--chart-series-long)",
  composite: "var(--chart-series-composite)",
  vrMacd:    "var(--warning-fg)",
};

/** Rolling cross-sectional IC 折線。X = date、Y = N-day rolling mean IC。
 *
 * 用來偵測 regime shift：例如 mid trend 在 2024 才開始 work、long valuation 在 2025 反轉等。
 * 如果單一 mean IC 是 +0.04，但折線顯示在 2022-23 是 -0.05、2024-26 是 +0.10，那 mean 是
 * 跨 regime 平均的假象，不是穩定 alpha。
 */
export function RollingICChart({ data, height = 300 }: { data: RollingICRow[]; height?: number }) {
  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center text-sm text-[var(--text-tertiary)]" style={{ height }}>
        rolling IC 資料不足，需要先跑 backfill_signal_history
      </div>
    );
  }
  const yDomain = useMemo(() => {
    const vals: number[] = [];
    for (const row of data) {
      for (const k of ["short", "mid", "long", "composite", "vrMacd"] as const) {
        const v = row[k];
        if (typeof v === "number" && Number.isFinite(v)) vals.push(Math.abs(v));
      }
    }
    const maxAbs = vals.length ? Math.max(...vals) : 0.1;
    // 對稱刻度避免視覺誇大，並限制上界不超過 0.40（IC 在實務上極少超出）。
    const bound = Math.max(0.1, Math.min(0.4, Math.ceil(maxAbs * 20) / 20));
    return [-bound, bound] as [number, number];
  }, [data]);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
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
          tickFormatter={(v: number) => v.toFixed(2)}
          domain={yDomain}
        />
        {/* IC = 0 是噪音線；IC > 0.05 通常算有訊號 */}
        <ReferenceLine y={0} stroke="var(--chart-grid)" />
        <ReferenceLine y={0.05} stroke="var(--up-400)" strokeDasharray="4 4" opacity={0.4} />
        <ReferenceLine y={-0.05} stroke="var(--down-400)" strokeDasharray="4 4" opacity={0.4} />
        <Tooltip
          contentStyle={{
            background: "var(--chart-tooltip-bg)",
            border: "none",
            borderRadius: 6,
            color: "var(--chart-tooltip-fg)",
            fontSize: 12,
          }}
          labelStyle={{ color: "var(--chart-tooltip-fg)", opacity: 0.7 }}
          formatter={(v: number) => v.toFixed(4)}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {(["short", "mid", "long", "composite", "vrMacd"] as SeriesKey[]).map((k) => (
          <Line
            key={k}
            type="monotone"
            dataKey={k}
            stroke={COLOR[k]}
            strokeWidth={k === "composite" ? 2.5 : 1.6}
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
