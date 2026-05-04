import { Suspense } from "react";
import {
  apiGet,
  humanizeApiError,
  type HoldingRow,
  type RealizedPnlSummary,
  type RiskAlert,
  type TradeRow,
} from "@/lib/api";
import { Icon } from "@/components/primitives/Icon";
import { PageHeader } from "@/components/primitives/PageHeader";
import { EmptyState } from "@/components/primitives/EmptyState";
import { RiskAlertList } from "@/components/primitives/RiskAlertList";
import { SectionTitle } from "@/components/primitives/SectionTitle";
import { Th, Td } from "@/components/primitives/Table";
import { TableContainer } from "@/components/primitives/TableContainer";
import { StockIdCell } from "@/components/primitives/StockIdCell";
import { DownloadXlsxButton } from "@/components/primitives/DownloadXlsxButton";
import {
  KpiRowSkeleton,
  TableSkeleton,
  CardSkeleton,
} from "@/components/primitives/Skeleton";
import { fmtMoney, fmtPct, fmtPrice, tone, toneClass } from "@/lib/format";
import { cn } from "@/lib/utils";
import { TradesPanel } from "./TradesPanel";
import { HoldingsLiveSection } from "./HoldingsLiveSection";

// 持股頁是「頻繁編輯」場景：使用者新增 / 刪除交易後預期立刻看到結果。
// 用 ISR cache（revalidate=60）會讓 router.refresh() 在 60s 內讀到舊 fetch 結果，
// 出現「明明刪掉了卻還在」的錯覺。改成 dynamic 不快取，每次 request 都重 fetch。
export const dynamic = "force-dynamic";

const NOCACHE = { noCache: true } as const;

export default function HoldingsPage() {
  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1600px] mx-auto">
      <PageHeader
        title="我的持股"
        icon="account_balance_wallet"
        description="持股總覽、新增/刪除交易、已實現損益"
      />

      <div className="flex justify-end">
        <DownloadXlsxButton href="/api/portfolio/holdings/export.xlsx" size="sm" />
      </div>

      <Suspense fallback={<>
        <KpiRowSkeleton count={4} />
        <TableSkeleton rows={6} cols={9} />
      </>}>
        <LiveSection />
      </Suspense>

      <Suspense fallback={null}>
        <RisksSection />
      </Suspense>

      <section className="flex flex-col gap-3">
        <SectionTitle icon="paid">已實現損益</SectionTitle>
        <Suspense fallback={<CardSkeleton className="h-32" />}>
          <RealizedPnlSection />
        </Suspense>
      </section>

      <Suspense fallback={<TableSkeleton rows={4} cols={6} />}>
        <TradesSection />
      </Suspense>
    </div>
  );
}

function SectionError({ error }: { error: unknown }) {
  return (
    <div className="rounded-xl border border-[var(--error-border)] bg-[var(--error-bg)] p-4 flex gap-3 items-start">
      <Icon name="cloud_off" size={20} filled className="text-[var(--error-fg)] shrink-0 mt-0.5" />
      <div className="text-sm text-[var(--error-fg)]">{humanizeApiError(error)}</div>
    </div>
  );
}

async function LiveSection() {
  let holdings: HoldingRow[];
  try {
    holdings = await apiGet<HoldingRow[]>("/api/portfolio/holdings", NOCACHE);
  } catch (e) {
    return <SectionError error={e} />;
  }
  return <HoldingsLiveSection initialRows={holdings} />;
}

async function RisksSection() {
  let risks: RiskAlert[];
  try {
    risks = await apiGet<RiskAlert[]>("/api/portfolio/risk-alerts", NOCACHE);
  } catch {
    // 風險提醒失敗不顯示比顯示錯誤訊息更不噪音
    return null;
  }
  if (risks.length === 0) return null;
  return (
    <section className="flex flex-col gap-3">
      <SectionTitle icon="warning">風險提醒</SectionTitle>
      <RiskAlertList alerts={risks} />
    </section>
  );
}

async function RealizedPnlSection() {
  let realized: RealizedPnlSummary;
  try {
    realized = await apiGet<RealizedPnlSummary>("/api/portfolio/realized-pnl", NOCACHE);
  } catch (e) {
    return <SectionError error={e} />;
  }
  if (realized.pairCount === 0) {
    return <EmptyState size="sm">尚無已實現損益（所有持股都尚未出場）</EmptyState>;
  }
  return (
    <>
      <div className="grid grid-cols-3 gap-4 max-w-xl">
        <MiniKPI label="已實現損益" value={fmtMoney(realized.totalPnl, 0)} tone={tone(realized.totalPnl)} />
        <MiniKPI label="配對筆數" value={realized.pairCount.toString()} tone="neutral" />
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
  );
}

async function TradesSection() {
  let trades: TradeRow[] = [];
  try {
    trades = await apiGet<TradeRow[]>("/api/portfolio/trades?limit=50", NOCACHE);
  } catch {
    // TradesPanel 容忍空陣列（會顯示 EmptyState）
  }
  return <TradesPanel initialTrades={trades} />;
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
