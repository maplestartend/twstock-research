"use client";

/**
 * 持股明細表的即時報價覆蓋層（給「我的持股」與「今日戰情室」共用）。
 *
 * 拆出來的目的：
 * - HoldingsLiveSection 是「KPI + 表格」整包；戰情室上面已經有頂部 KPI 列，再渲一遍會冗
 * - 所以 component 只負責「表格 + 標頭即時徽章」這一層，KPI 由 caller 視情況另接
 *
 * 收盤值 fallback：
 * - 整批 fetch 失敗 → useHoldingsIntraday 回 {}，所有 row 用初始 snapshot 的 price
 * - 個別 row 失敗（興櫃 / mis 撈不到）→ 該 row 退回 snapshot；其餘 row 用即時
 */
import { useMemo } from "react";
import type { HoldingRow } from "@/lib/api";
import { useHoldingsIntraday } from "@/lib/hooks/useIntraday";
import { HoldingsTable } from "./HoldingsTable";
import { Icon } from "./Icon";
import { cn } from "@/lib/utils";

export type LiveHoldingsResult = {
  rows: HoldingRow[];
  liveCount: number;
  totalCount: number;
};

/** Pure hook：拿到原始 rows + 即時 quotes 後回傳 merged rows + 計數，不負責渲染。
 *  HoldingsLiveSection 用這支 derive KPIs；dashboard / holdings 表格也是同一份合併結果。
 *  enabled=false：父層已在輪詢，這支不啟動第二份 timer，僅做 merge 與計數。 */
export function useLiveHoldings(initialRows: HoldingRow[], enabled = true): LiveHoldingsResult {
  const quotes = useHoldingsIntraday(enabled);
  return useMemo(() => {
    const rows = mergeLiveQuotes(initialRows, quotes);
    return { rows, liveCount: Object.keys(quotes).length, totalCount: initialRows.length };
  }, [initialRows, quotes]);
}

export function LiveHoldingsTable({
  initialRows,
  title = "持股明細",
  titleIcon = "list_alt",
  shared,
}: {
  initialRows: HoldingRow[];
  title?: string;
  titleIcon?: string;
  /** 父層已 useLiveHoldings 時把結果傳進來；本元件就不會再啟動第二份輪詢 timer。 */
  shared?: LiveHoldingsResult;
}) {
  const own = useLiveHoldings(initialRows, !shared);
  const { rows, liveCount, totalCount } = shared ?? own;
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div className="inline-flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 text-base font-semibold">
            <Icon name={titleIcon} size={20} className="text-[var(--brand-500)]" />
            {title}
          </span>
          <LiveStatus liveCount={liveCount} totalCount={totalCount} />
        </div>
      </div>
      <HoldingsTable rows={rows} />
    </section>
  );
}

export function LiveStatus({ liveCount, totalCount }: { liveCount: number; totalCount: number }) {
  if (totalCount === 0) return null;
  if (liveCount === 0) {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] border bg-subtle text-[var(--text-tertiary)] border-[var(--border-default)]">
        <Icon name="schedule" size={11} />
        昨日收盤
      </span>
    );
  }
  const cls =
    liveCount === totalCount
      ? "bg-[var(--color-up-bg)] text-[var(--color-up)] border-[var(--color-up-border)]"
      : "bg-[var(--warning-bg)] text-[var(--warning-fg)] border-[var(--warning-border)]";
  return (
    <span
      className={cn("inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] border", cls)}
      title="即時報價自 TWSE mis；30 秒更新一次（盤後降頻為 2 分鐘）"
    >
      <Icon name="bolt" size={11} filled />
      即時 {liveCount}/{totalCount}
    </span>
  );
}

function mergeLiveQuotes(
  rows: HoldingRow[],
  quotes: Record<string, { price: number; prevClose: number | null }>,
): HoldingRow[] {
  if (Object.keys(quotes).length === 0) return rows;
  return rows.map((r) => {
    const q = quotes[r.stockId];
    if (!q) return r;
    const price = q.price;
    const prev = q.prevClose ?? r.prevClose;
    const todayPct =
      price != null && prev != null && prev !== 0 ? (price - prev) / prev : r.todayPct;
    const marketValue = price * r.shares;
    const unrealizedPnl = (price - r.avgCost) * r.shares;
    const unrealizedPnlPct = r.avgCost > 0 ? (price - r.avgCost) / r.avgCost : r.unrealizedPnlPct;
    const sellCosts = r.estimatedSellCosts ?? null;
    const netUnrealizedPnl = sellCosts != null ? unrealizedPnl - sellCosts : unrealizedPnl;
    const netUnrealizedPnlPct =
      r.avgCost > 0 && r.shares > 0
        ? netUnrealizedPnl / (r.avgCost * r.shares)
        : r.netUnrealizedPnlPct ?? null;
    const atrDistancePct =
      r.atrStop != null && price > 0 ? (price - r.atrStop) / price : r.atrDistancePct;
    const atrBelowStop = r.atrStop != null ? price < r.atrStop : r.atrBelowStop;
    // 停利距離 / 觸發改用即時價重判：armed 仍沿用後端（依進場日 + 浮盈門檻），
    // 但 8% 門檻不會因即時價小幅波動而跳變，所以不重新評估 armed
    const atrTakeProfitDistancePct =
      r.atrTakeProfit != null && price > 0
        ? (price - r.atrTakeProfit) / price
        : r.atrTakeProfitDistancePct;
    const atrTakeProfitTriggered =
      r.atrTakeProfitArmed && r.atrTakeProfit != null
        ? price <= r.atrTakeProfit
        : r.atrTakeProfitTriggered;
    return {
      ...r,
      price,
      prevClose: prev ?? null,
      todayPct,
      marketValue,
      unrealizedPnl,
      unrealizedPnlPct,
      netUnrealizedPnl,
      netUnrealizedPnlPct,
      atrDistancePct,
      atrBelowStop,
      atrTakeProfitDistancePct,
      atrTakeProfitTriggered,
    };
  });
}
