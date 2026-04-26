import Link from "next/link";
import { Icon } from "./Icon";
import { cn } from "@/lib/utils";

/**
 * 跑完一個分析後的「下一步建議」卡片群。
 * 用在四個進階頁底部，串接「策略回測 → 投組回測 → 參數掃描 → 權重調優」流程。
 */
export type NextStep = {
  href: string;
  icon: string;
  title: string;
  description: string;
};

export function NextStepCards({ items, heading = "下一步試試" }: { items: NextStep[]; heading?: string }) {
  if (items.length === 0) return null;
  return (
    <section className="flex flex-col gap-3 mt-2">
      <h2 className="text-sm font-semibold text-[var(--text-secondary)] inline-flex items-center gap-2">
        <Icon name="arrow_forward" size={18} className="text-[var(--brand-500)]" />
        {heading}
      </h2>
      <div className={cn(
        "grid gap-3",
        items.length === 1 ? "grid-cols-1" :
        items.length === 2 ? "grid-cols-1 md:grid-cols-2" :
        "grid-cols-1 md:grid-cols-2 lg:grid-cols-3",
      )}>
        {items.map((s) => (
          <Link
            key={s.href}
            href={s.href}
            className="group rounded-xl border border-[var(--border-default)] bg-surface p-4 hover:border-[var(--brand-500)] hover:bg-[var(--brand-tint-soft)] transition-colors flex items-start gap-3"
          >
            <span className="inline-flex items-center justify-center w-9 h-9 rounded-lg bg-[var(--brand-tint)] text-[var(--brand-600)] dark:text-[var(--brand-400)] shrink-0">
              <Icon name={s.icon} size={20} filled />
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold text-[var(--text-primary)] inline-flex items-center gap-1.5">
                {s.title}
                <Icon name="chevron_right" size={16} className="text-[var(--text-tertiary)] group-hover:text-[var(--brand-500)] group-hover:translate-x-0.5 transition-transform" />
              </div>
              <p className="text-xs text-[var(--text-tertiary)] mt-0.5 leading-relaxed">{s.description}</p>
            </div>
          </Link>
        ))}
      </div>
    </section>
  );
}
