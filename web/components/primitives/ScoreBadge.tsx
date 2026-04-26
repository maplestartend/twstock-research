import { cn } from "@/lib/utils";
import { fmtScore, scoreTier } from "@/lib/format";

// font-weight 統一拉到 600+ 加強 scannability（UIUX 審查）
const TIER_CLASS = {
  "strong-pos": "bg-[var(--score-strong-pos-bg)] text-[var(--score-strong-pos-fg)] font-bold",
  "pos":        "bg-[var(--score-pos-bg)] text-[var(--score-pos-fg)] font-bold",
  "neutral":    "bg-[var(--score-neutral-bg)] text-[var(--score-neutral-fg)] font-bold",
  "caution":    "bg-[var(--score-caution-bg)] text-[var(--score-caution-fg)] font-bold",
  "danger":     "bg-[var(--score-danger-bg)] text-[var(--score-danger-fg)] font-bold",
  "unknown":    "bg-subtle text-[var(--text-tertiary)] font-semibold",
};

const SIZE_CLASS = {
  sm: "text-[11px] h-5 min-w-[36px] px-1.5",
  md: "text-[13px] h-6 min-w-[44px] px-2.5",
  lg: "text-base h-8 min-w-[52px] px-3",
};

export type ScoreBadgeProps = {
  score: number | null | undefined;
  size?: "sm" | "md" | "lg";
  horizon?: "short" | "mid" | "long" | "composite";
  ariaLabel?: string;
};

export function ScoreBadge({ score, size = "md", horizon, ariaLabel }: ScoreBadgeProps) {
  const tier = scoreTier(score);
  const label = horizon
    ? { short: "短期", mid: "中期", long: "長期", composite: "綜合" }[horizon]
    : "分數";
  return (
    <span
      aria-label={ariaLabel ?? `${label} ${fmtScore(score)} 分`}
      className={cn(
        "numeric inline-flex items-center justify-center rounded-md",
        TIER_CLASS[tier],
        SIZE_CLASS[size],
      )}
    >
      {fmtScore(score)}
    </span>
  );
}
