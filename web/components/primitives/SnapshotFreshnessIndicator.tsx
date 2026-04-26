"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { apiPost, type SnapshotStatus, type RefreshSnapshotResponse } from "@/lib/api";
import { Icon } from "./Icon";
import { cn } from "@/lib/utils";

/** Topbar 用的 snapshot 新鮮度指示器：
 * - is_stale=false → 綠勾「資料新鮮」
 * - is_stale=true → 黃驚嘆「需重算」+ 點擊觸發 /api/system/refresh-snapshot
 * - 重算中：spinner + 禁用點擊
 *
 * 對應 PM 審查 P0-3：之前完全沒地方看 snapshot 是否最新；CLAUDE.md 地雷 #5 說
 * 改 app/scoring/* 後不重算就會看到舊資料。
 */
export function SnapshotFreshnessIndicator({ initial }: { initial: SnapshotStatus | null }) {
  const router = useRouter();
  const [status, setStatus] = useState<SnapshotStatus | null>(initial);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  if (!status) return null;

  const stale = status.isStale;
  const handleRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    setError(null);
    try {
      const res = await apiPost<RefreshSnapshotResponse>("/api/system/refresh-snapshot", {});
      // refresh OK → 更新 local + 重新整理整頁的 RSC
      setStatus({
        ...status,
        snapshotAsOf: status.dailyPriceAsOf,
        isStale: false,
      });
      startTransition(() => router.refresh());
      console.info(`snapshot refreshed: ${res.rowsWritten} rows`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  };

  if (refreshing) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-subtle text-[var(--text-secondary)]">
        <Icon name="autorenew" size={14} className="animate-spin" />
        重算 snapshot 中…（1-2 分鐘）
      </span>
    );
  }

  if (!stale) {
    return (
      <span
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-[var(--color-down-bg)] text-[var(--color-down)]"
        title={`signal_history 截至 ${status.snapshotAsOf}`}
      >
        <Icon name="check_circle" size={14} filled />
        <span className="hidden sm:inline">快照最新</span>
      </span>
    );
  }

  return (
    <button
      type="button"
      onClick={handleRefresh}
      className={cn(
        "inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium",
        "bg-[var(--warning-bg)] text-[var(--warning-fg)] hover:opacity-90 cursor-pointer transition-opacity",
        error && "ring-1 ring-[var(--color-up)]",
      )}
      title={
        error
          ? `重算失敗：${error}`
          : `signal_history (${status.snapshotAsOf}) 落後 daily_price (${status.dailyPriceAsOf}) — 點擊重算`
      }
    >
      <Icon name="error" size={14} filled />
      <span className="hidden sm:inline">快照需重算</span>
      <Icon name="refresh" size={12} className="ml-0.5" />
    </button>
  );
}
