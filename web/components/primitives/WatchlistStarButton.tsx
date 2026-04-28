"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Icon } from "./Icon";
import { toggleWatchlistAction } from "@/lib/watchlist-actions";
import { cn } from "@/lib/utils";

/**
 * 持股列「加入/移除自選」星號 toggle。
 * 樂觀更新：點下立刻翻轉 UI，失敗才還原並 console error。
 * 走 server action 而非直接 fetch FastAPI，原因是 server action 才能呼叫
 * `revalidatePath('/', 'layout')` 把其他頁面的 Next.js Data Cache 也清掉
 * （否則切到 /watchlist 還會吃到 60 秒前的 cached 自選清單）。
 */
export function WatchlistStarButton({
  stockId,
  initialInWatchlist,
}: {
  stockId: string;
  initialInWatchlist: boolean;
}) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [inWatchlist, setInWatchlist] = useState(initialInWatchlist);
  const [busy, setBusy] = useState(false);

  const toggle = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (busy) return;
    const next = !inWatchlist;
    setInWatchlist(next);
    setBusy(true);
    try {
      const res = await toggleWatchlistAction(stockId, next);
      if (!res.ok) throw new Error(res.error);
      // server action 已 revalidatePath，再 refresh 把當前頁面 RSC 拉一次最新
      startTransition(() => router.refresh());
    } catch (err) {
      setInWatchlist(!next);
      console.error("WatchlistStarButton toggle failed", err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={busy}
      title={inWatchlist ? "從自選移除" : "加入自選"}
      aria-label={inWatchlist ? `從自選移除 ${stockId}` : `加入自選 ${stockId}`}
      aria-pressed={inWatchlist}
      className={cn(
        // 40x40 tap target（WCAG AAA / iOS HIG / Material Design 推薦）
        "inline-flex items-center justify-center rounded min-w-[40px] min-h-[40px] -m-1 transition-colors",
        "hover:bg-subtle disabled:opacity-50 disabled:cursor-not-allowed",
        inWatchlist
          ? "text-[var(--warning-500)]"
          : "text-[var(--text-tertiary)] hover:text-[var(--warning-500)]",
      )}
    >
      <Icon name="star" size={18} filled={inWatchlist} />
    </button>
  );
}
