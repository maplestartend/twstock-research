"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useRef } from "react";

/**
 * 表格內共用的「代號 / 名稱」cell。
 * - 代號：tabular-nums + 主要文字色 + semibold
 * - 名稱：12px secondary（比 tertiary 對比強，小字仍易讀），truncate 防溢位
 *
 * Hover prefetch：`<Link prefetch>` 預設只 prefetch RSC layout payload，個股詳情頁的
 * 6 支 API 仍要點下去才開始抓。在 hover 時呼叫 `router.prefetch(href)` 把整條 RSC
 * tree（含 await 的 server component data）拉到 client cache，點擊時瞬間 navigate。
 *
 * 用 timeout 50ms debounce — 滑鼠掃過表格不該觸發每一列的 prefetch。
 */
export function StockIdCell({
  stockId,
  stockName,
}: {
  stockId: string;
  stockName?: string | null;
}) {
  const router = useRouter();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const href = `/stocks/${stockId}`;

  const armPrefetch = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      router.prefetch(href);
    }, 50);
  }, [router, href]);

  const cancelPrefetch = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  return (
    <Link
      href={href}
      className="flex flex-col hover:underline"
      onMouseEnter={armPrefetch}
      onMouseLeave={cancelPrefetch}
      onFocus={armPrefetch}
      onBlur={cancelPrefetch}
    >
      <span className="numeric font-semibold text-[var(--text-primary)]">{stockId}</span>
      {stockName ? (
        <span className="text-[var(--text-secondary)] text-[12px] truncate">{stockName}</span>
      ) : null}
    </Link>
  );
}
