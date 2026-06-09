import Link from "next/link";
import { apiGet, apiGetOptional, type TagCount, type WatchlistOverviewRow } from "@/lib/api";
import { Icon } from "@/components/primitives/Icon";
import { PageHeader } from "@/components/primitives/PageHeader";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { FilterChip } from "@/components/primitives/FilterChip";
import { WatchlistResults } from "./WatchlistResults";

export const revalidate = 60;

const TABS = {
  stock: { label: "個股", match: (m: string | null | undefined) => m === "上市" || m === "上櫃" || m === "其他" || m == null },
  etf: { label: "ETF", match: (m: string | null | undefined) => m === "ETF" },
} as const;
type Tab = keyof typeof TABS;

export default async function WatchlistOverviewPage({
  searchParams,
}: {
  searchParams: Promise<{ type?: string; tag?: string }>;
}) {
  const sp = await searchParams;
  const tab: Tab = sp.type === "etf" ? "etf" : "stock";
  const activeTag = sp.tag || "";

  let allRows: WatchlistOverviewRow[];
  try {
    allRows = await apiGet<WatchlistOverviewRow[]>("/api/watchlist/overview", {
      tags: ["watchlist", "snapshot"],
    });
  } catch (e) {
    return <BackendDownError error={e} pageTitle="自選股總覽" />;
  }
  // 完整 tag 清單給 chip 列；沒設過任何 tag 時整段不渲染
  const tagCounts = await apiGetOptional<TagCount[]>("/api/watchlist/tags", {
    tags: ["watchlist"],
  }) ?? [];

  const stockRows = allRows.filter((r) => TABS.stock.match(r.market));
  const etfRows = allRows.filter((r) => TABS.etf.match(r.market));
  // tab / tag 過濾 + 排行卡 + 主表都移進 WatchlistResults（client island），讓收盤/即時兩種
  // 資料走同一套篩選與排序。此處只留 header 用的計數與最後快照日。
  const latestAsOf = allRows.map((r) => r.asOf).filter(Boolean).sort().slice(-1)[0];

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
            // 切 tab 時保留當前 tag filter（只要 tag 在新 tab 下還有命中）
            const tabHref =
              t === "etf"
                ? `/watchlist?type=etf${activeTag ? `&tag=${encodeURIComponent(activeTag)}` : ""}`
                : `/watchlist${activeTag ? `?tag=${encodeURIComponent(activeTag)}` : ""}`;
            return (
              <FilterChip
                key={t}
                href={tabHref}
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

      {tagCounts.length > 0 && (
        <section className="flex flex-wrap items-center gap-2 -mt-2">
          <span className="text-xs text-[var(--text-tertiary)] inline-flex items-center gap-1">
            <Icon name="sell" size={12} />
            標籤
          </span>
          <FilterChip
            href={`/watchlist${tab === "etf" ? "?type=etf" : ""}`}
            active={!activeTag}
            size="sm"
            tone="neutral"
            prefetch={false}
          >
            全部
          </FilterChip>
          {tagCounts.map((tc) => {
            const active = tc.tag === activeTag;
            // 構造 query：保留 type tab，覆寫 tag
            const params = new URLSearchParams();
            if (tab === "etf") params.set("type", "etf");
            params.set("tag", tc.tag);
            return (
              <FilterChip
                key={tc.tag}
                href={`/watchlist?${params.toString()}`}
                active={active}
                size="sm"
                count={tc.count}
                prefetch={false}
              >
                {tc.tag}
              </FilterChip>
            );
          })}
        </section>
      )}

      <WatchlistResults
        allRows={allRows}
        tab={tab}
        activeTag={activeTag}
        tabLabel={TABS[tab].label}
        hasAnyStock={allRows.length > 0}
      />
    </div>
  );
}


