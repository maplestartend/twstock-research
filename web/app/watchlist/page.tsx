import Link from "next/link";
import { apiGet, type WatchlistOverviewRow } from "@/lib/api";
import { Icon } from "@/components/primitives/Icon";
import { PageHeader } from "@/components/primitives/PageHeader";
import { EmptyState } from "@/components/primitives/EmptyState";
import { ScoreBadge } from "@/components/primitives/ScoreBadge";
import { RecommendationTag } from "@/components/primitives/RecommendationTag";
import { PriceCell } from "@/components/primitives/PriceCell";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { Th, Td } from "@/components/primitives/Table";
import { TableContainer } from "@/components/primitives/TableContainer";
import { StockIdCell } from "@/components/primitives/StockIdCell";
import { SectionTitle } from "@/components/primitives/SectionTitle";
import { FilterChip } from "@/components/primitives/FilterChip";
import { fmtPrice } from "@/lib/format";
import { cn } from "@/lib/utils";

export const revalidate = 60;

const TABS = {
  stock: { label: "個股", match: (m: string | null | undefined) => m === "上市" || m === "上櫃" || m === "其他" || m == null },
  etf: { label: "ETF", match: (m: string | null | undefined) => m === "ETF" },
} as const;
type Tab = keyof typeof TABS;

export default async function WatchlistOverviewPage({
  searchParams,
}: {
  searchParams: Promise<{ type?: string }>;
}) {
  const sp = await searchParams;
  const tab: Tab = sp.type === "etf" ? "etf" : "stock";

  let allRows: WatchlistOverviewRow[];
  try {
    allRows = await apiGet<WatchlistOverviewRow[]>("/api/watchlist/overview");
  } catch (e) {
    return <BackendDownError error={e} pageTitle="自選股總覽" />;
  }

  const stockRows = allRows.filter((r) => TABS.stock.match(r.market));
  const etfRows = allRows.filter((r) => TABS.etf.match(r.market));
  const rows = tab === "etf" ? etfRows : stockRows;

  const top3 = rows.slice(0, 3);
  const bottom3 = rows.slice().reverse().slice(0, 3);
  const latestAsOf = rows.map((r) => r.asOf).filter(Boolean).sort().slice(-1)[0];

  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1600px] mx-auto">
      <PageHeader
        title="自選股總覽"
        icon="dataset"
        description={
          allRows.length > 0
            ? `共 ${allRows.length} 檔（個股 ${stockRows.length}・ETF ${etfRows.length}）${latestAsOf ? `　·　最後快照 ${latestAsOf}` : ""}`
            : "尚無自選股"
        }
      />

      {/* sub-nav：把 /watchlist-manage 和 /dividend-calendar 從 sidebar 收進這裡，
          因為它們是「自選股」這個 domain 的子頁。CommandPalette 也可以直接跳。 */}
      <div className="flex flex-wrap items-center gap-2 -mt-2">
        <Link
          href="/watchlist-manage"
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-[var(--border-default)] bg-surface text-xs font-medium text-[var(--text-secondary)] hover:border-[var(--brand-300)] hover:text-[var(--text-primary)] transition-colors"
        >
          <Icon name="edit_note" size={14} />
          自選股管理
        </Link>
        <Link
          href="/dividend-calendar"
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-[var(--border-default)] bg-surface text-xs font-medium text-[var(--text-secondary)] hover:border-[var(--brand-300)] hover:text-[var(--text-primary)] transition-colors"
        >
          <Icon name="event" size={14} />
          除權息行事曆
        </Link>
      </div>

      {allRows.length === 0 ? null : (
        <section className="flex flex-wrap items-center gap-2 border-b border-[var(--border-default)] pb-3">
          {(Object.keys(TABS) as Tab[]).map((t) => {
            const active = t === tab;
            const count = t === "etf" ? etfRows.length : stockRows.length;
            return (
              <FilterChip
                key={t}
                href={`/watchlist${t === "etf" ? "?type=etf" : ""}`}
                active={active}
                icon={t === "etf" ? "currency_exchange" : "monitoring"}
                count={count}
                prefetch={false}
              >
                {TABS[t].label}
              </FilterChip>
            );
          })}
          <span className="text-[11px] text-[var(--text-tertiary)] ml-2">
            ETF 沒有 EPS / ROE / 月營收，與個股的長期分數不可比；分開看比較公平
          </span>
        </section>
      )}

      {rows.length === 0 ? (
        <EmptyState>
          {allRows.length === 0
            ? <>尚無自選股。先到「自選股管理」新增，再跑 <code className="font-mono">python -m scripts.market_update</code> 產生訊號快照。</>
            : `自選清單沒有${TABS[tab].label}。可切到另一個 tab。`}
        </EmptyState>
      ) : (
        <>
          {/* Top/Bottom 3 strip */}
          <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <RankingCard
              title="綜合評分前三"
              icon="emoji_events"
              tone="up"
              items={top3}
            />
            <RankingCard
              title="綜合評分後三"
              icon="thumb_down"
              tone="down"
              items={bottom3}
            />
          </section>

          {/* Main table */}
          <section className="flex flex-col gap-3">
            <SectionTitle icon="table_view">全部自選股</SectionTitle>
            <TableContainer>
              <table className="w-full text-[15px] min-w-[700px]">
                <thead className="bg-subtle">
                  <tr>
                    <Th>代號 / 名稱</Th>
                    <Th align="right">收盤</Th>
                    <Th align="right">今日%</Th>
                    <Th align="center">短期</Th>
                    <Th align="center">中期</Th>
                    {tab !== "etf" && <Th align="center">長期</Th>}
                    <Th align="center">綜合</Th>
                    <Th align="center">建議</Th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.stockId} className="tv-row border-t border-[var(--border-default)] hover:bg-subtle transition-colors">
                      <Td>
                        <StockIdCell stockId={r.stockId} stockName={r.stockName} />
                      </Td>
                      <Td align="right" numeric>{fmtPrice(r.close)}</Td>
                      <Td align="right">
                        <PriceCell price={r.close} deltaPct={r.changePct} variant="compact" />
                      </Td>
                      <Td align="center">
                        <div className="flex justify-center">
                          <ScoreBadge score={r.short} size="sm" horizon="short" />
                        </div>
                      </Td>
                      <Td align="center">
                        <div className="flex justify-center">
                          <ScoreBadge score={r.mid} size="sm" horizon="mid" />
                        </div>
                      </Td>
                      {tab !== "etf" && (
                        <Td align="center">
                          <div className="flex justify-center">
                            <ScoreBadge score={r.long} size="sm" horizon="long" />
                          </div>
                        </Td>
                      )}
                      <Td align="center">
                        <div className="flex justify-center">
                          <ScoreBadge score={r.composite} size="sm" horizon="composite" />
                        </div>
                      </Td>
                      <Td align="center">
                        {r.recommendation ? (
                          <div className="flex justify-center">
                            <RecommendationTag raw={r.recommendation} size="sm" />
                          </div>
                        ) : (
                          <span className="text-[var(--text-tertiary)]">—</span>
                        )}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableContainer>
          </section>
        </>
      )}
    </div>
  );
}

function RankingCard({
  title,
  icon,
  tone,
  items,
}: {
  title: string;
  icon: string;
  tone: "up" | "down";
  items: WatchlistOverviewRow[];
}) {
  const accent = tone === "up" ? "text-[var(--color-up)]" : "text-[var(--color-down)]";
  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-surface p-5 flex flex-col gap-3">
      <h3 className={cn("text-sm font-semibold inline-flex items-center gap-2", accent)}>
        <Icon name={icon} size={18} filled />
        {title}
      </h3>
      {items.length === 0 ? (
        <span className="text-sm text-[var(--text-tertiary)]">—</span>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((r) => (
            <li key={r.stockId}>
              <Link
                href={`/stocks/${r.stockId}`}
                className="flex items-center gap-3 p-2 rounded-lg hover:bg-subtle transition-colors"
              >
                <span className="numeric text-sm font-semibold text-[var(--text-primary)] w-14 shrink-0">
                  {r.stockId}
                </span>
                <span className="text-sm text-[var(--text-secondary)] flex-1 truncate">{r.stockName}</span>
                <ScoreBadge score={r.composite} size="md" horizon="composite" />
                {r.recommendation && <RecommendationTag raw={r.recommendation} size="sm" />}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


