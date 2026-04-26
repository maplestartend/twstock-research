import { cn } from "@/lib/utils";
import { Icon } from "./Icon";

const TONE = {
  ok:      { icon: "check_circle",  cls: "bg-[var(--color-down-bg)] text-[var(--color-down)]" },
  warning: { icon: "error",         cls: "bg-[var(--warning-bg)] text-[var(--warning-fg)]" },
  error:   { icon: "cancel",        cls: "bg-[var(--color-up-bg)] text-[var(--color-up)]" },
  neutral: { icon: "remove",        cls: "bg-subtle text-[var(--text-secondary)]" },
} as const;

export function DataFreshnessBadge({
  tone,
  latestDate,
  lagDays,
}: {
  tone: "ok" | "warning" | "error" | "neutral";
  latestDate?: string | null;
  lagDays?: number | null;
}) {
  const t = TONE[tone] ?? TONE.neutral;
  // 沒有日期時顯示 hourglass + 「無資料」，比「—」清楚（UIUX 審查）
  if (!latestDate) {
    return (
      <span className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium", TONE.neutral.cls)}>
        <Icon name="hourglass_empty" size={14} filled />
        <span>無資料</span>
      </span>
    );
  }
  return (
    <span className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium", t.cls)}>
      <Icon name={t.icon} size={14} filled />
      <span className="numeric">{latestDate}</span>
      {lagDays != null && <span className="opacity-70 numeric">(−{lagDays}d)</span>}
    </span>
  );
}
