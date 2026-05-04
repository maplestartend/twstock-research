"use client";

/**
 * 「我的持股」頁的即時價格覆蓋層（KPI 摘要 + 持股明細共用一份 polling）。
 *
 * 為什麼 KPI + 表格放在同一個 client component：
 * - 兩邊都需要同一份 holdings/intraday 批次回應，分開實作會 polling 兩次（雖然後端 30s
 *   per-stock cache 讓重複呼叫廉價，但前端浪費 1 次 round-trip 沒必要）
 * - 即時價變動 → KPI 的「目前市值 / 今日損益 / 未實現損益」都會跟著動，集中在一處 derive
 *   比較好理解
 *
 * 表格本體抽到 components/primitives/LiveHoldingsTable，今日戰情室共用同一支。
 */
import { useMemo } from "react";
import type { HoldingRow, IntradayQuoteView } from "@/lib/api";
import { KPIStat } from "@/components/primitives/KPIStat";
import { LiveHoldingsTable, useLiveHoldings } from "@/components/primitives/LiveHoldingsTable";
import { fmtMoney, tone } from "@/lib/format";

export function HoldingsLiveSection({
  initialRows,
  initialQuotes,
}: {
  initialRows: HoldingRow[];
  /** Server 端 prefetch 的批次報價；client 第一次渲染就用即時值，不會出現昨收→即時的閃爍。 */
  initialQuotes?: Record<string, IntradayQuoteView>;
}) {
  const live = useLiveHoldings(initialRows, true, initialQuotes);
  const summary = useMemo(() => deriveSummary(live.rows), [live.rows]);

  return (
    <div className="flex flex-col gap-8">
      <section className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPIStat label="持股檔數" value={summary.holdingCount.toString()} tone="neutral" />
        <KPIStat label="成本總額" value={fmtMoney(summary.totalCost, 0)} tone="neutral" />
        <KPIStat
          label="目前市值"
          value={fmtMoney(summary.totalMarketValue, 0)}
          deltaPct={summary.todayPnlPct}
          tone={tone(summary.todayPnl)}
          footnote={`今日 ${fmtMoney(summary.todayPnl, 0)}`}
        />
        <KPIStat
          label="未實現損益"
          value={fmtMoney(summary.unrealizedPnl, 0)}
          deltaPct={summary.unrealizedPnlPct}
          tone={tone(summary.unrealizedPnl)}
        />
      </section>

      {/* 把已輪詢過的結果傳下去，避免子元件重啟第二份 30s 計時器 */}
      <LiveHoldingsTable initialRows={initialRows} shared={live} />
    </div>
  );
}

function deriveSummary(rows: HoldingRow[]) {
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
  const unrealizedPnlPct = totalCost > 0 ? unrealizedPnl / totalCost : null;
  const todayPnlPct = totalMarketValue > 0 ? todayPnl / totalMarketValue : null;
  return {
    totalMarketValue,
    totalCost,
    unrealizedPnl,
    unrealizedPnlPct,
    todayPnl: hasTodayPnl ? todayPnl : 0,
    todayPnlPct: hasTodayPnl ? todayPnlPct : null,
    holdingCount: rows.length,
  };
}
