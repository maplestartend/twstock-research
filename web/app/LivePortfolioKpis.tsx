"use client";

/**
 * 戰情室頂部 3 張 KPI 卡（持股總市值 / 今日損益 / 累積未實現損益）的即時覆蓋層。
 *
 * 為什麼要拆 client component：
 * - 原本 KpiSection 用 `data.summary` 渲染（snapshot close），盤中報價變動時 KPI 不會動
 * - 持股表格底下已用 `<LiveHoldingsTable>` 跟 mis 即時報價同步；上面的 KPI 不同步看起來會精分
 * - 這支 component 跟 LiveHoldingsTable 共用 `useLiveHoldings`，從 server-prefetch 的 initialQuotes
 *   開始 render，第一個 frame 就是即時值，沒有「先昨收後跳即時」的肉眼閃爍
 *
 * 為什麼 estimatedSellCosts / totalCost 仍從 initialSummary 取：
 * - sellCosts = tax_rate × price × shares + fee；嚴格講 price 變動會微影響但 30s 間 < 1%
 * - totalCost = avg_cost × shares 不會因即時價變動（只在新增/刪除交易時才會變）
 * - 重新由 client 算這兩個沒額外好處，沿用 server snapshot 即可
 */
import { useMemo } from "react";
import type { HoldingRow, IntradayQuoteView, PortfolioSummary } from "@/lib/api";
import { useLiveHoldings } from "@/components/primitives/LiveHoldingsTable";
import { KPIStat } from "@/components/primitives/KPIStat";
import { fmtMoney, tone } from "@/lib/format";

export function LivePortfolioKpis({
  initialRows,
  initialQuotes,
  initialSummary,
}: {
  initialRows: HoldingRow[];
  /** Server prefetch 過的批次盤中報價；client 第一個 render 就直接用即時值 */
  initialQuotes?: Record<string, IntradayQuoteView>;
  /** 從 dashboard payload 的 summary 來，提供 estimatedSellCosts / totalCost / 標題 footnote 字樣 */
  initialSummary: PortfolioSummary;
}) {
  const live = useLiveHoldings(initialRows, true, initialQuotes);
  const d = useMemo(
    () => deriveLiveSummary(live.rows, initialSummary),
    [live.rows, initialSummary],
  );
  return (
    <>
      <div className="col-span-2">
        <KPIStat
          label="持股總市值"
          value={fmtMoney(d.totalMarketValue, 0)}
          deltaPct={d.todayPnlPct}
          tone={tone(d.todayPnlPct)}
          footnote={`${d.holdingCount} 檔`}
          size="lg"
        />
      </div>
      <KPIStat
        label="今日損益"
        value={fmtMoney(d.todayPnl, 0)}
        deltaPct={d.todayPnlPct}
        tone={tone(d.todayPnl)}
      />
      <KPIStat
        label="累積未實現損益"
        value={fmtMoney(d.netUnrealizedPnl, 0)}
        deltaPct={d.netUnrealizedPnlPct}
        tone={tone(d.netUnrealizedPnl)}
        footnote={
          initialSummary.estimatedSellCosts
            ? `毛 ${fmtMoney(d.unrealizedPnl, 0)}・賣出稅費 ${fmtMoney(initialSummary.estimatedSellCosts, 0)}`
            : `成本 ${fmtMoney(d.totalCost, 0)}`
        }
      />
    </>
  );
}

/** 從即時 merged rows 重算 summary。estimatedSellCosts 沿用 server snapshot（30s 內影響 < 1%）。 */
function deriveLiveSummary(rows: HoldingRow[], initialSummary: PortfolioSummary) {
  let totalMarketValue = 0;
  let totalCost = 0;
  let unrealizedPnl = 0;
  let todayPnl = 0;
  let hasTodayPnl = false;
  for (const r of rows) {
    totalCost += r.shares * r.avgCost;
    if (r.marketValue != null) totalMarketValue += r.marketValue;
    if (r.unrealizedPnl != null) unrealizedPnl += r.unrealizedPnl;
    if (r.price != null && r.prevClose != null) {
      todayPnl += r.shares * (r.price - r.prevClose);
      hasTodayPnl = true;
    }
  }
  const sellCosts = initialSummary.estimatedSellCosts ?? 0;
  const netUnrealizedPnl = unrealizedPnl - sellCosts;
  const unrealizedPnlPct = totalCost > 0 ? unrealizedPnl / totalCost : null;
  const netUnrealizedPnlPct = totalCost > 0 ? netUnrealizedPnl / totalCost : null;
  const todayPnlPct = totalMarketValue > 0 ? todayPnl / totalMarketValue : null;
  return {
    totalMarketValue,
    totalCost,
    unrealizedPnl,
    unrealizedPnlPct,
    netUnrealizedPnl,
    netUnrealizedPnlPct,
    todayPnl: hasTodayPnl ? todayPnl : 0,
    todayPnlPct: hasTodayPnl ? todayPnlPct : null,
    holdingCount: rows.length,
  };
}
