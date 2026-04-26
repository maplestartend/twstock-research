/**
 * 共用分頁器：取代雷達 + 歷史頁本地定義。
 *
 * 設計：
 * - 固定可見格數，避免不同頁碼下 Pagination 視覺寬度不一致
 *   - totalPages <= 7：全顯示
 *   - 否則：固定 7 格 [1, ?, mid×3, ?, N]
 * - 用獨立 wrapper（pt-1）取代 nav 上的 mt-1，避免與父層 gap-3 複合
 * - 上/下/數字鈕統一 min-w 2.25rem、h-8，視覺一致
 */
import Link from "next/link";

import { cn } from "@/lib/utils";

export function Pagination({
  page,
  totalPages,
  buildHref,
}: {
  page: number;
  totalPages: number;
  buildHref: (page: number) => string;
}) {
  const visible: (number | "...")[] = [];
  if (totalPages <= 7) {
    for (let i = 1; i <= totalPages; i++) visible.push(i);
  } else {
    // mid 三個元素確保長度 == 3，但 noUncheckedIndexedAccess 不會推論
    const mid: [number, number, number] =
      page <= 3
        ? [2, 3, 4]
        : page >= totalPages - 2
        ? [totalPages - 3, totalPages - 2, totalPages - 1]
        : [page - 1, page, page + 1];
    visible.push(1);
    visible.push(page > 3 ? "..." : 2);
    visible.push(mid[0]);
    visible.push(mid[1]);
    visible.push(mid[2]);
    visible.push(page < totalPages - 2 ? "..." : totalPages - 1);
    visible.push(totalPages);
  }
  return (
    <div className="pt-1">
      <nav className="flex items-center justify-center gap-1 flex-nowrap" aria-label="分頁">
        <PageBtn
          href={page > 1 ? buildHref(page - 1) : null}
          label="‹ 上一頁"
          disabled={page <= 1}
        />
        {visible.map((p, i) =>
          p === "..." ? (
            <span
              key={`gap-${i}`}
              className="inline-flex items-center justify-center min-w-[2.25rem] h-8 text-[var(--text-tertiary)] text-xs"
            >
              …
            </span>
          ) : (
            <PageBtn
              key={`p-${p}-${i}`}
              href={buildHref(p as number)}
              label={String(p)}
              active={(p as number) === page}
            />
          ),
        )}
        <PageBtn
          href={page < totalPages ? buildHref(page + 1) : null}
          label="下一頁 ›"
          disabled={page >= totalPages}
        />
      </nav>
    </div>
  );
}

function PageBtn({
  href,
  label,
  active = false,
  disabled = false,
}: {
  href: string | null;
  label: string;
  active?: boolean;
  disabled?: boolean;
}) {
  const base =
    "numeric inline-flex items-center justify-center min-w-[2.25rem] h-8 px-2 rounded border text-xs transition-colors shrink-0";
  if (disabled || !href) {
    return (
      <span
        className={cn(
          base,
          "border-[var(--border-default)] text-[var(--text-disabled)] cursor-not-allowed bg-surface",
        )}
      >
        {label}
      </span>
    );
  }
  return (
    <Link
      href={href}
      scroll={false}
      className={cn(
        base,
        active
          ? "bg-[var(--brand-500)] text-white border-[var(--brand-500)]"
          : "bg-surface text-[var(--text-secondary)] border-[var(--border-default)] hover:border-[var(--brand-300)]",
      )}
    >
      {label}
    </Link>
  );
}
