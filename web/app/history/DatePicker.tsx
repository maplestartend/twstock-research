"use client";

import { useRouter } from "next/navigation";

/** 歷史追蹤頁日期切換 dropdown。
 *
 * 設計：上方 chip 只露最近 10 天（常用），這個 select 補足「更早」場景，避免使用者
 * 看到 `…+N more` 文字以為被截斷而無法導航。auto-submit on change（不必再點按鈕）。
 */
export function DatePicker({
  dates,
  current,
  basePath = "/history",
  preservedParams,
}: {
  dates: string[];
  current: string;
  basePath?: string;
  preservedParams: Record<string, string | undefined>;
}) {
  const router = useRouter();

  const navigate = (d: string) => {
    if (d === current) return;
    const params = new URLSearchParams();
    params.set("as_of", d);
    for (const [k, v] of Object.entries(preservedParams)) {
      if (v) params.set(k, v);
    }
    // 切日期等同重新看那一天，page 重置回 1（不放進 preservedParams）
    router.push(`${basePath}?${params.toString()}`, { scroll: false });
  };

  return (
    <select
      value={current}
      onChange={(e) => navigate(e.target.value)}
      className="numeric h-7 px-2 rounded border border-[var(--border-default)] bg-surface text-xs text-[var(--text-secondary)] hover:border-[var(--brand-300)] focus:outline-none focus:border-[var(--brand-500)]"
      aria-label="選擇歷史快照日期"
    >
      {dates.map((d) => (
        <option key={d} value={d}>{d}</option>
      ))}
    </select>
  );
}
