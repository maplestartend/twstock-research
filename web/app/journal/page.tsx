import { apiGet, type JournalStatRow, type TradeRow } from "@/lib/api";
import { PageHeader } from "@/components/primitives/PageHeader";
import { EmptyState } from "@/components/primitives/EmptyState";
import { Th, Td } from "@/components/primitives/Table";
import { TableContainer } from "@/components/primitives/TableContainer";
import { StockIdCell } from "@/components/primitives/StockIdCell";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { Icon } from "@/components/primitives/Icon";
import { fmtPct, fmtMoney, fmtPrice, toneClass } from "@/lib/format";
import { JournalEditor } from "./JournalEditor";

export const dynamic = "force-dynamic";

export default async function JournalPage() {
  let trades: TradeRow[];
  let stats: JournalStatRow[];
  try {
    [trades, stats] = await Promise.all([
      apiGet<TradeRow[]>("/api/portfolio/trades?limit=200"),
      apiGet<JournalStatRow[]>("/api/portfolio/journal-stats"),
    ]);
  } catch (e) {
    return <BackendDownError error={e} pageTitle="交易日誌" />;
  }

  return (
    <div className="p-4 lg:p-8 flex flex-col gap-6 max-w-[1400px] mx-auto">
      <PageHeader
        title="交易日誌"
        icon="edit_note"
        description="幫每筆交易補上「為什麼買」+ 標籤（例：短線強勢、法人連買）。下面的勝率表會依 tag 統計已實現損益，看自己跟哪種策略最合得來。"
        extra="未實現的 BUY 不算進勝率（避免被當下價格 noise 拉偏）。"
      />

      {/* tag 勝率彙總 */}
      <section className="flex flex-col gap-3">
        <h2 className="text-base font-semibold inline-flex items-center gap-2">
          <Icon name="leaderboard" size={20} className="text-[var(--brand-500)]" />
          標籤勝率
          <span className="numeric text-xs text-[var(--text-tertiary)] font-normal ml-2">
            {stats.length} 個 tag · 依累計損益排序
          </span>
        </h2>
        {stats.length === 0 ? (
          <EmptyState size="sm">
            還沒有「已配對到 SELL」的 trade 帶 tag。先在下面表格幫過去的買單補 tag，
            等 FIFO 配對後就會顯示。
          </EmptyState>
        ) : (
          <TableContainer>
            <table className="w-full text-[15px] min-w-[600px]">
              <thead className="bg-subtle">
                <tr>
                  <Th>標籤</Th>
                  <Th align="right" className="w-[100px]">配對數</Th>
                  <Th align="right" className="w-[110px]">勝率</Th>
                  <Th align="right" className="w-[140px]">平均報酬%</Th>
                  <Th align="right" className="w-[140px]">累計損益</Th>
                </tr>
              </thead>
              <tbody>
                {stats.map((s) => (
                  <tr key={s.tag} className="border-t border-[var(--border-default)] hover:bg-subtle">
                    <Td>
                      <span className="inline-block px-2 py-0.5 rounded bg-[var(--brand-tint)] text-[var(--brand-700)] dark:text-[var(--brand-300)] text-xs font-medium">
                        {s.tag}
                      </span>
                    </Td>
                    <Td align="right" numeric>{s.count}</Td>
                    <Td align="right" numeric>
                      <span
                        className={
                          s.winRate == null ? "" : s.winRate >= 0.5 ? "text-[var(--color-up)]" : "text-[var(--color-down)]"
                        }
                      >
                        {s.winRate == null ? "—" : fmtPct(s.winRate, 0)}
                      </span>
                    </Td>
                    <Td align="right" numeric>
                      <span className={toneClass(s.avgPnlPct)}>
                        {s.avgPnlPct == null ? "—" : fmtPct(s.avgPnlPct, 2)}
                      </span>
                    </Td>
                    <Td align="right" numeric>
                      <span className={cn("font-semibold", toneClass(s.totalPnl))}>
                        {fmtMoney(s.totalPnl, 0)}
                      </span>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableContainer>
        )}
      </section>

      {/* 交易明細 + retroactive 編輯 tag/reason */}
      <section className="flex flex-col gap-3">
        <h2 className="text-base font-semibold inline-flex items-center gap-2">
          <Icon name="receipt_long" size={20} className="text-[var(--brand-500)]" />
          交易明細
          <span className="numeric text-xs text-[var(--text-tertiary)] font-normal ml-2">
            最新 {trades.length} 筆
          </span>
        </h2>
        {trades.length === 0 ? (
          <EmptyState size="sm">尚無交易紀錄。到「我的持股」新增第一筆。</EmptyState>
        ) : (
          <TableContainer>
            <table className="w-full text-[15px] min-w-[1100px]">
              <thead className="bg-subtle">
                <tr>
                  <Th className="w-[100px]">日期</Th>
                  <Th sticky className="w-[160px]">代號</Th>
                  <Th align="center" className="w-[60px]">動作</Th>
                  <Th align="right" className="w-[80px]">張數</Th>
                  <Th align="right" className="w-[88px]">價格</Th>
                  <Th>進場理由 / 標籤 / 備註</Th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => (
                  <tr key={t.id} className="group border-t border-[var(--border-default)] hover:bg-subtle align-top">
                    <Td className="text-xs text-[var(--text-secondary)]">{t.tradeDate}</Td>
                    <Td sticky>
                      <StockIdCell stockId={t.stockId} stockName={t.stockName} />
                    </Td>
                    <Td align="center">
                      <span
                        className={cn(
                          "inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold",
                          t.action === "BUY"
                            ? "bg-[var(--color-up-bg)] text-[var(--color-up)]"
                            : "bg-[var(--color-down-bg)] text-[var(--color-down)]",
                        )}
                      >
                        {t.action === "BUY" ? "買" : "賣"}
                      </span>
                    </Td>
                    <Td align="right" numeric>{(t.shares / 1000).toFixed(1)}</Td>
                    <Td align="right" numeric>{fmtPrice(t.price)}</Td>
                    <Td>
                      <JournalEditor trade={t} />
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableContainer>
        )}
      </section>
    </div>
  );
}

function cn(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}
