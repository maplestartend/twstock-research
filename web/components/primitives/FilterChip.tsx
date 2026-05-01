import Link from "next/link";
import { Icon } from "./Icon";
import { cn } from "@/lib/utils";

/**
 * 統一的「篩選 chip」primitive — 過去 8+ 處頁面（radar / dq / history / dividend-calendar /
 * watchlist / weight-tuner ...）各自寫過大致相同的 active/inactive className：
 *   active=brand-500 + 白字 + brand 邊框；inactive=surface + tertiary 字 + 預設邊框 hover brand-300
 *
 * 統一抽出後：(a) 維護單一外觀來源；(b) 後續調主題色只改一處；(c) 配合 InfoTip count badge
 * 等附屬資訊統一渲染。
 *
 * 變體：
 * - size="sm" 給篩選列窄高用（h 28px），"md" 給 tab 用（h 36px）
 * - tone="neutral" / "brand"：brand 是預設、neutral 用於非主要篩選
 * - active 切換 visual style，icon `filled` 同步聯動
 *
 * 用法：
 *   <FilterChip href={`/radar?strategy=${s.name}`} icon="bolt" active={current === s.name} count={s.hits}>
 *     {s.label}
 *   </FilterChip>
 */
export type FilterChipProps = {
  /** 用 href 走 Next Link（保留 SSR navigate）；不提供則 render <button>。 */
  href?: string;
  onClick?: () => void;
  active?: boolean;
  size?: "sm" | "md";
  /** brand: 主色（active 用 brand-500）；neutral: subtle 灰底（用於不太強調的開關） */
  tone?: "brand" | "neutral";
  icon?: string;
  /** 右側小數字 badge — 用於顯示命中數 / 篩出量 */
  count?: number | string | null;
  /** prefetch=false 給「不希望背景預載入的」chip 用（例如 ETF/個股切換要跑 backend） */
  prefetch?: boolean;
  className?: string;
  scroll?: boolean;
  ariaLabel?: string;
  children: React.ReactNode;
};

const sizeClasses = {
  sm: "px-2 py-0.5 text-xs gap-1",
  md: "px-4 py-2 text-sm gap-2",
} as const;

export function FilterChip({
  href,
  onClick,
  active = false,
  size = "md",
  tone = "brand",
  icon,
  count,
  prefetch,
  className,
  scroll = false,
  ariaLabel,
  children,
}: FilterChipProps) {
  const activeClass =
    tone === "brand"
      ? "bg-[var(--brand-500)] text-white border-[var(--brand-500)] font-medium"
      : "bg-subtle text-[var(--text-primary)] border-[var(--border-default)] font-medium";
  const inactiveClass =
    "bg-surface text-[var(--text-secondary)] border-[var(--border-default)] hover:border-[var(--brand-300)]";

  const cls = cn(
    "inline-flex items-center rounded-md border transition-colors",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand-500)]/40 focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--surface)]",
    sizeClasses[size],
    active ? activeClass : inactiveClass,
    className,
  );

  const inner = (
    <>
      {icon && <Icon name={icon} size={size === "sm" ? 12 : 16} filled={active} />}
      <span>{children}</span>
      {count != null && (
        <span
          className={cn(
            "inline-flex items-center justify-center min-w-[1.25rem] px-1 rounded text-[10px] tabular-nums font-semibold",
            active
              ? "bg-white/25 text-white"
              : "bg-subtle text-[var(--text-tertiary)]",
          )}
        >
          {count}
        </span>
      )}
    </>
  );

  if (href) {
    return (
      <Link
        href={href}
        scroll={scroll}
        prefetch={prefetch}
        className={cls}
        aria-label={ariaLabel}
        // aria-current 對 link-based filter 比 aria-pressed 更語意正確：告訴讀屏軟體
        // 「目前頁面套用的就是這個 chip 對應的篩選條件」。
        aria-current={active ? "page" : undefined}
      >
        {inner}
      </Link>
    );
  }
  return (
    <button type="button" onClick={onClick} className={cls} aria-label={ariaLabel} aria-pressed={active}>
      {inner}
    </button>
  );
}
