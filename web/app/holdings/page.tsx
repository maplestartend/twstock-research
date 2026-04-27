import Link from "next/link";
import {
  apiGet,
  apiGetOptional,
  type HoldingRow,
  type PortfolioSummary,
  type RealizedPnlSummary,
  type RiskAlert,
  type TradeRow,
} from "@/lib/api";
import { Icon } from "@/components/primitives/Icon";
import { PageHeader } from "@/components/primitives/PageHeader";
import { EmptyState } from "@/components/primitives/EmptyState";
import { KPIStat } from "@/components/primitives/KPIStat";
import { HoldingsTable } from "@/components/primitives/HoldingsTable";
import { RiskAlertList } from "@/components/primitives/RiskAlertList";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { SectionTitle } from "@/components/primitives/SectionTitle";
import { Th, Td } from "@/components/primitives/Table";
import { TableContainer } from "@/components/primitives/TableContainer";
import { StockIdCell } from "@/components/primitives/StockIdCell";
import { fmtMoney, fmtPct, fmtPrice, tone, toneClass } from "@/lib/format";
import { cn } from "@/lib/utils";
import { TradesPanel } from "./TradesPanel";

// 持股頁是「頻繁編輯」場景：使用者新增 / 刪除交易後預期立刻看到結果。
// 用 ISR cache（revalidate=60）會讓 router.refresh() 在 60s 內讀到舊 fetch 結果，
// 出現「明明刪掉了卻還在」的錯覺。改成 dynamic 不快取，每次 request 都重 fetch。
export const dynamic = "force-dynamic";

export default async function HoldingsPage() {
  let summary: PortfolioSummary, holdings: HoldingRow[], risks: RiskAlert[], trades: TradeRow[], realized: RealizedPnlSummary;
  try {
    // 5 個 fetch 全部 noCache，避免 Data Cache 內部仍命中舊版（即便頁面 dynamic）。
    [summary, holdings, risks, trades, realized] = await Promise.all([
      apiGet<PortfolioSummary>("/api/portfolio/summary", { noCache: true }),
      apiGet<HoldingRow[]>("/api/portfolio/holdings", { noCache: true }),
      apiGet<RiskAlert[]>("/api/portfolio/risk-alerts", { noCache: true }),
      apiGet<TradeRow[]>("/api/portfolio/trades?limit=50", { noCache: true }),
      apiGet<RealizedPnlSummary>("/api/portfolio/realized-pnl", { noCache: true }),
    ]);
  } catch (e) {
    return <BackendDownError error={e} pageTitle="我的持股" />;
  }

  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1600px] mx-auto">
      <PageHeader
        title="我的持股"
        icon="account_balance_wallet"
        description="持股總覽、新增/刪除交易、已實現損益"
      />

      {/* KPI strip */}
      <section className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPIStat
          label="持股檔數"
          value={summary.holdingCount.toString()}
          tone="neutral"
        />
        <KPIStat
          label="成本總額"
          value={fmtMoney(summary.totalCost, 0)}
          tone="neutral"
        />
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

      {/* Holdings detail */}
      <section className="flex flex-col gap-3">
        <SectionTitle icon="list_alt">持股明細</SectionTitle>
        <HoldingsTable rows={holdings} />
      </section>

      {/* Risk alerts — 同 severity 合併成 list 卡，避免 4 張 2x2 噪音 */}
      {risks.length > 0 && (
        <section className="flex flex-col gap-3">
          <SectionTitle icon="warning">風險提醒</SectionTitle>
          <RiskAlertList alerts={risks} />
        </section>
      )}

      {/* Realized P&L */}
      <section className="flex flex-col gap-3">
        <SectionTitle icon="paid">已實現損益</SectionTitle>
        {realized.pairCount === 0 ? (
          <EmptyState size="sm">尚無已實現損益（所有持股都尚未出場）</EmptyState>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-4 max-w-xl">
              <MiniKPI
                label="已實現損益"
                value={fmtMoney(realized.totalPnl, 0)}
                tone={tone(realized.totalPnl)}
              />
              <MiniKPI
                label="配對筆數"
                value={realized.pairCount.toString()}
                tone="neutral"
              />
              <MiniKPI
                label="勝率"
                value={realized.winRate != null ? `${(realized.winRate * 100).toFixed(1)}%` : "—"}
                tone="neutral"
              />
            </div>
            <TableContainer>
              <table className="w-full text-[15px] min-w-[600px]">
                <thead className="bg-subtle">
                  <tr>
                    <Th>代號 / 名稱</Th>
                    <Th align="right">股數</Th>
                    <Th align="right">買 / 賣 價</Th>
                    <Th align="right">買日 → 賣日</Th>
                    <Th align="right">成本 / 收入</Th>
                    <Th align="right">損益</Th>
                  </tr>
                </thead>
                <tbody>
                  {realized.rows.map((r, i) => (
                    <tr key={i} className="border-t border-[var(--border-default)] hover:bg-subtle transition-colors">
                      <Td>
                        <StockIdCell stockId={r.stockId} stockName={r.stockName} />
                      </Td>
                      <Td align="right" numeric>{r.shares.toLocaleString("zh-TW")}</Td>
                      <Td align="right" numeric>
                        <div className="flex flex-col items-end">
                          <span>{fmtPrice(r.buyPrice)}</span>
                          <span className="text-xs text-[var(--text-tertiary)]">{fmtPrice(r.sellPrice)}</span>
                        </div>
                      </Td>
                      <Td align="right" numeric>
                        <div className="flex flex-col items-end text-xs text-[var(--text-tertiary)]">
                          <span>{r.buyDate}</span>
                          <span>→ {r.sellDate}</span>
                        </div>
                      </Td>
                      <Td align="right" numeric>
                        <div className="flex flex-col items-end">
                          <span>{fmtMoney(r.cost, 0)}</span>
                          <span className="text-xs text-[var(--text-tertiary)]">{fmtMoney(r.proceed, 0)}</span>
                        </div>
                      </Td>
                      <Td align="right" numeric>
                        <div className="flex flex-col items-end">
                          <span className={cn("font-semibold", toneClass(r.pnl))}>{fmtMoney(r.pnl, 0)}</span>
                          <span className={cn("text-xs", toneClass(r.pnlPct))}>{fmtPct(r.pnlPct, 2)}</span>
                        </div>
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableContainer>
          </>
        )}
      </section>

      {/* Trades — 含新增表單與刪除按鈕的 client component */}
      <TradesPanel initialTrades={trades} />
    </div>
  );
}

function MiniKPI({ label, value, tone }: { label: string; value: string; tone: "up" | "down" | "flat" | "neutral" }) {
  const toneCls =
    tone === "up" ? "text-[var(--color-up)]" :
    tone === "down" ? "text-[var(--color-down)]" :
    "text-[var(--text-primary)]";
  return (
    <div className="rounded-lg border border-[var(--border-default)] bg-surface p-3 flex flex-col gap-1">
      <span className="text-xs text-[var(--text-tertiary)]">{label}</span>
      <span className={cn("numeric text-lg font-bold", toneCls)}>{value}</span>
    </div>
  );
}


