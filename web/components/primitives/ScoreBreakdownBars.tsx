import { PART_LABEL } from "@/lib/labels";
import { scoreTier } from "@/lib/format";
import { cn } from "@/lib/utils";

export function ScoreBreakdownBars({ parts }: { parts: Record<string, number | null> }) {
  const entries = Object.entries(parts)
    .filter((pair): pair is [string, number] => pair[1] != null && Number.isFinite(pair[1]))
    .sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) {
    return <p className="text-sm text-[var(--text-tertiary)]">無子項資料</p>;
  }
  return (
    <ul className="flex flex-col gap-2">
      {entries.map(([k, v]) => {
        // entries 已過濾成有限數字，scoreTier 不會回 "unknown"；門檻與舊內聯版一致（70/55/45/30）
        const tier = scoreTier(v);
        return (
          <li key={k} className="flex items-center gap-3">
            <span className="w-20 text-xs text-[var(--text-secondary)] shrink-0 truncate">{PART_LABEL[k] ?? k}</span>
            <div className="flex-1 h-2 rounded bg-subtle overflow-hidden">
              <div
                className={cn(
                  "h-full transition-all",
                  tier === "strong-pos" && "bg-[var(--score-strong-pos-fg)]",
                  tier === "pos"        && "bg-[var(--score-pos-fg)]",
                  tier === "neutral"    && "bg-[var(--score-neutral-fg)]",
                  tier === "caution"    && "bg-[var(--score-caution-fg)]",
                  tier === "danger"     && "bg-[var(--score-danger-fg)]",
                )}
                style={{ width: `${Math.max(2, Math.min(100, v))}%` }}
              />
            </div>
            <span className="numeric w-10 text-right text-xs font-medium text-[var(--text-primary)]">
              {v.toFixed(0)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
