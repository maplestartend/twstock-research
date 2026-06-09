"use client";

/**
 * 自選股總覽結果區（排行卡 + 主表）+「收盤 / 即時」切換（client island）。
 *
 * - 收盤（預設）：顯示 SSR 帶進來的 signal_history 收盤快照。
 * - 即時：輪詢 /api/watchlist/overview/live（trading-hour 30s / off-hour 120s / 背景暫停），
 *   後端對自選股抓盤中即時價、用同一份 score_all 重算短/中/綜合並依即時綜合分排序。
 *
 * 過濾（tab / tag）在 client 端套用，讓收盤與即時兩種資料走同一套篩選；長期分數結構性、
 * 不隨盤中價變動（與個股詳情頁一致）。
 */
import { useMemo, useState } from "react";
import Link from "next/link";
import { ScoreBadge } from "@/components/primitives/ScoreBadge";
import { RecommendationTag } from "@/components/primitives/RecommendationTag";
import { PriceCell } from "@/components/primitives/PriceCell";
import { Th, Td } from "@/components/primitives/Table";
import { TableContainer } from "@/components/primitives/TableContainer";
import { StockIdCell } from "@/components/primitives/StockIdCell";
import { SectionTitle } from "@/components/primitives/SectionTitle";
import { EmptyState } from "@/components/primitives/EmptyState";
import { Icon } from "@/components/primitives/Icon";
import { useLiveListData } from "@/lib/hooks/useIntraday";
import type { WatchlistOverviewRow } from "@/lib/api";
import { fmtPrice } from "@/lib/format";
import { cn } from "@/lib/utils";

type Tab = "stock" | "etf";

function matchTab(tab: Tab, m: string | null | undefined): boolean {
  if (tab === "etf") return m === "ETF";
  return m === "上市" || m === "上櫃" || m === "其他" || m == null;
}

type Props = {
  /** 全部自選股的收盤快照（未過濾）；client 端自行套 tab / tag 過濾，與即時資料一致。 */
  allRows: WatchlistOverviewRow[];
  tab: Tab;
  activeTag: string;
  tabLabel: string;
  hasAnyStock: boolean; // allRows.length > 0（給空狀態文案）
};

export function WatchlistResults({ allRows, tab, activeTag, tabLabel, hasAnyStock }: Props) {
  const [liveMode, setLiveMode] = useState(false);
  const { data } = useLiveListData<WatchlistOverviewRow[]>(
    "/api/watchlist/overview/live",
    allRows,
    liveMode,
  );

  const rows = useMemo(() => {
    const tabRows = data.filter((r) => matchTab(tab, r.market));
    const filtered = activeTag ? tabRows.filter((r) => r.tags?.includes(activeTag)) : tabRows;
    // 即時模式後端已依即時綜合分排序；這裡保險再排一次（過濾後順序穩定）
    return filtered
      .slice()
      .sort((a, b) => (b.composite ?? -Infinity) - (a.composite ?? -Infinity));
  }, [data, tab, activeTag]);

  const anyLive = liveMode && rows.some((r) => r.isLive);
  const top3 = rows.slice(0, 3);
  const bottom3 = rows.slice().reverse().slice(0, 3);

  if (rows.length === 0) {
    return (
      <EmptyState>
        {!hasAnyStock ? (
          <>尚無自選股。先到「自選股管理」新增，再跑 <code className="font-mono">python -m scripts.market_update</code> 產生訊號快照。</>
        ) : activeTag ? (
          `「${activeTag}」標籤底下沒有${tabLabel}。可切標籤或回到「全部」。`
        ) : (
          `自選清單沒有${tabLabel}。可切到另一個 tab。`
        )}
      </EmptyState>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {/* 收盤 / 即時 切換 */}
      <div className="flex items-center gap-3 flex-wrap">
        <div
          className="inline-flex rounded-lg border border-[var(--border-default)] overflow-hidden text-sm"
          role="group"
          aria-label="分數模式"
        >
          <button
            type="button"
            onClick={() => setLiveMode(false)}
            aria-pressed={!liveMode}
            className={cn(
              "px-3 py-1.5 inline-flex items-center gap-1.5 transition-colors",
              !liveMode
                ? "bg-[var(--brand-500)] text-white"
                : "bg-surface text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
            )}
          >
            <Icon name="event_available" size={16} />收盤
          </button>
          <button
            type="button"
            onClick={() => setLiveMode(true)}
            aria-pressed={liveMode}
            className={cn(
              "px-3 py-1.5 inline-flex items-center gap-1.5 transition-colors border-l border-[var(--border-default)]",
              liveMode
                ? "bg-[var(--brand-500)] text-white"
                : "bg-surface text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
            )}
          >
            <Icon name="bolt" size={16} />即時
          </button>
        </div>
        {liveMode && (
          <span className="text-[11px] text-[var(--text-tertiary)] inline-flex items-center gap-1.5">
            <span
              className={cn(
                "inline-block w-1.5 h-1.5 rounded-full",
                anyLive ? "bg-[var(--color-up)] animate-pulse" : "bg-[var(--text-tertiary)]",
              )}
            />
            盤中即時重算（每 30 秒）· 來源 TWSE mis · 僅短/中/綜合隨價變動，長期結構不變
            {liveMode && !anyLive && "·目前非盤中／抓不到即時價，暫顯示收盤"}
          </span>
        )}
      </div>

      {/* Top/Bottom 3 strip */}
      <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <RankingCard title="綜合評分前三" icon="emoji_events" tone="up" items={top3} />
        <RankingCard title="綜合評分後三" icon="thumb_down" tone="down" items={bottom3} />
      </section>

      {/* Main table */}
      <section className="flex flex-col gap-3">
        <SectionTitle icon="table_view">全部自選股</SectionTitle>
        <TableContainer>
          <table className="w-full text-[15px] min-w-[700px]">
            <thead className="bg-subtle">
              <tr>
                <Th>代號 / 名稱</Th>
                <Th align="right">{liveMode ? "即時" : "收盤"}</Th>
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
                    <div className="flex items-center gap-2 min-w-0">
                      <StockIdCell stockId={r.stockId} stockName={r.stockName} />
                      {liveMode && r.isLive && (
                        <span
                          className="inline-flex items-center text-[9px] font-medium px-1 rounded bg-[var(--color-up)]/10 text-[var(--color-up)] shrink-0"
                          title="此列分數以盤中即時價重算"
                        >
                          即時
                        </span>
                      )}
                      {r.tags && r.tags.length > 0 && (
                        <span className="flex flex-wrap gap-1 shrink-0">
                          {r.tags.map((t) => (
                            <span
                              key={t}
                              className="px-1.5 py-px rounded text-[10px] bg-[var(--brand-tint)] text-[var(--brand-700)] border border-[var(--brand-300)]/40"
                            >
                              {t}
                            </span>
                          ))}
                        </span>
                      )}
                    </div>
                  </Td>
                  <Td align="right" numeric>{fmtPrice(r.close)}</Td>
                  <Td align="right">
                    <PriceCell price={r.close} deltaPct={r.changePct} variant="compact" />
                  </Td>
                  <Td align="center"><div className="flex justify-center"><ScoreBadge score={r.short} size="sm" horizon="short" /></div></Td>
                  <Td align="center"><div className="flex justify-center"><ScoreBadge score={r.mid} size="sm" horizon="mid" /></div></Td>
                  {tab !== "etf" && (
                    <Td align="center"><div className="flex justify-center"><ScoreBadge score={r.long} size="sm" horizon="long" /></div></Td>
                  )}
                  <Td align="center"><div className="flex justify-center"><ScoreBadge score={r.composite} size="sm" horizon="composite" /></div></Td>
                  <Td align="center">
                    {r.recommendation ? (
                      <div className="flex justify-center"><RecommendationTag raw={r.recommendation} size="sm" /></div>
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
