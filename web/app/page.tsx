import { Suspense, cache } from "react";
import Link from "next/link";
import {
  apiGet,
  apiGetOptional,
  humanizeApiError,
  type DashboardHomePayload,
  type PortfolioSummary,
  type HoldingRow,
  type IntradayQuoteView,
  type RadarHit,
  type ExDividendEvent,
  type DataFreshness,
  type WatchlistMover,
  type ScoreChange,
} from "@/lib/api";
import { KPIStat } from "@/components/primitives/KPIStat";
import { LiveHoldingsTable } from "@/components/primitives/LiveHoldingsTable";
import { RadarHitChip } from "@/components/primitives/RadarHitChip";
import { DataFreshnessBadge } from "@/components/primitives/DataFreshnessBadge";
import { PriceCell } from "@/components/primitives/PriceCell";
import { Icon } from "@/components/primitives/Icon";
import { PageHeader } from "@/components/primitives/PageHeader";
import { EmptyState } from "@/components/primitives/EmptyState";
import { SectionTitle } from "@/components/primitives/SectionTitle";
import {
  KpiRowSkeleton,
  TableSkeleton,
  ListSkeleton,
  CardSkeleton,
} from "@/components/primitives/Skeleton";
import { fmtDateShort, fmtMoney, fmtPrice, tone } from "@/lib/format";

export const revalidate = 60;

const getDashboardHome = cache(() =>
  apiGet<DashboardHomePayload>("/api/dashboard/home", { tags: ["snapshot", "watchlist"] }),
);

export default function DashboardPage() {
  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1600px] mx-auto">
      <PageHeader title="今日戰情室" icon="dashboard" description="開工具第一眼看的整合資訊" />

      <Suspense fallback={<KpiRowSkeleton count={4} hero />}>
        <KpiSection />
      </Suspense>

      <section className="grid grid-cols-1 xl:grid-cols-[1.6fr_1fr] gap-6">
        <div className="flex flex-col gap-4">
          <Suspense fallback={<TableSkeleton rows={4} cols={6} />}>
            <HoldingsDetailSection />
          </Suspense>
        </div>
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <SectionTitle icon="radar">今日雷達命中</SectionTitle>
            <Link
              href="/radar"
              className="text-xs text-[var(--brand-500)] hover:underline inline-flex items-center gap-1"
            >
              查看全部
              <Icon name="arrow_forward" size={14} />
            </Link>
          </div>
          <Suspense fallback={<ListSkeleton rows={4} />}>
            <RadarHitsSection />
          </Suspense>
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <SectionTitle icon="insights">我的關注 7 日分數變化</SectionTitle>
          <span className="text-xs text-[var(--text-tertiary)]">綜合分變化最大的 8 檔（自選股 + 持股）</span>
        </div>
        <Suspense fallback={<ListSkeleton rows={4} />}>
          <MyScoreChangesSection />
        </Suspense>
      </section>

      <section className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="flex flex-col gap-3">
          <SectionTitle icon="trending_up">自選股漲幅榜</SectionTitle>
          <Suspense fallback={<ListSkeleton rows={5} />}>
            <MoversSection direction="up" />
          </Suspense>
        </div>
        <div className="flex flex-col gap-3">
          <SectionTitle icon="trending_down">自選股跌幅榜</SectionTitle>
          <Suspense fallback={<ListSkeleton rows={5} />}>
            <MoversSection direction="down" />
          </Suspense>
        </div>
        <div className="flex flex-col gap-3">
          <SectionTitle icon="event">近 7 日除權息</SectionTitle>
          <Suspense fallback={<ListSkeleton rows={5} />}>
            <ExDividendSection />
          </Suspense>
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <SectionTitle icon="manage_history">資料更新狀態</SectionTitle>
        <Suspense fallback={<CardSkeleton className="h-20" />}>
          <FreshnessFooterSection />
        </Suspense>
      </section>
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

async function KpiSection() {
  let data: DashboardHomePayload;
  try {
    data = await getDashboardHome();
  } catch (e) {
    return <SectionError error={e} />;
  }
  const summary: PortfolioSummary = data.summary;
  const radarHits: RadarHit[] = data.radarHits;
  const freshness: DataFreshness[] = data.freshness;
  const freshest = freshness.find((f) => f.table === "daily_price");
  return (
    <section className="grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-6 gap-4">
      <div className="col-span-2">
        <KPIStat
          label="持股總市值"
          value={fmtMoney(summary.totalMarketValue, 0)}
          deltaPct={summary.todayPnlPct}
          tone={tone(summary.todayPnlPct)}
          footnote={`${summary.holdingCount} 檔`}
          size="lg"
        />
      </div>
      <KPIStat
        label="今日損益"
        value={fmtMoney(summary.todayPnl, 0)}
        deltaPct={summary.todayPnlPct}
        tone={tone(summary.todayPnl)}
      />
      <KPIStat
        label="累積未實現損益"
        value={fmtMoney(summary.netUnrealizedPnl ?? summary.unrealizedPnl, 0)}
        deltaPct={summary.netUnrealizedPnlPct ?? summary.unrealizedPnlPct}
        tone={tone(summary.netUnrealizedPnl ?? summary.unrealizedPnl)}
        footnote={
          summary.estimatedSellCosts
            ? `毛 ${fmtMoney(summary.unrealizedPnl, 0)}・賣出稅費 ${fmtMoney(summary.estimatedSellCosts, 0)}`
            : `成本 ${fmtMoney(summary.totalCost, 0)}`
        }
      />
      <KPIStat
        label="雷達命中"
        value={radarHits.length.toString()}
        footnote="今日"
        tone="neutral"
      />
      <KPIStat
        label="資料狀態"
        value={freshest?.latestDate ?? "—"}
        deltaText={
          freshest?.tone === "ok" ? `新鮮 (−${freshest.lagDays ?? 0}d)` :
          freshest?.tone === "warning" ? `稍舊 (−${freshest.lagDays ?? 0}d)` :
          "過舊"
        }
        tone={freshest?.tone === "ok" ? "down" : freshest?.tone === "warning" ? "neutral" : "up"}
      />
    </section>
  );
}

async function HoldingsDetailSection() {
  let data: DashboardHomePayload;
  let quotesList: IntradayQuoteView[] | null = null;
  try {
    // 並行：snapshot 持股（走 dashboard/home cache）+ 批次盤中報價（noCache，每次 SSR 都新鮮）。
    // intraday 端點失敗（後端掛 / 興櫃整批沒有 mis）就吞掉回 null，client 仍會自己輪詢。
    [data, quotesList] = await Promise.all([
      getDashboardHome(),
      apiGetOptional<IntradayQuoteView[]>("/api/portfolio/holdings/intraday", { noCache: true }),
    ]);
  } catch (e) {
    return <SectionError error={e} />;
  }
  const holdings: HoldingRow[] = data.holdings;
  const initialQuotes: Record<string, IntradayQuoteView> = {};
  for (const q of quotesList ?? []) initialQuotes[q.stockId] = q;
  // 整個區塊 = LiveHoldingsTable（標頭 + 即時徽章 + 表格），與「我的持股」頁的「持股明細」一致
  return (
    <LiveHoldingsTable
      initialRows={holdings}
      initialQuotes={initialQuotes}
      titleIcon="account_balance_wallet"
    />
  );
}

async function RadarHitsSection() {
  let data: DashboardHomePayload;
  try {
    data = await getDashboardHome();
  } catch (e) {
    return <SectionError error={e} />;
  }
  const radarHits: RadarHit[] = data.radarHits;
  if (radarHits.length === 0) {
    return (
      <EmptyState size="sm">
        尚無訊號快照。可跑 <code className="font-mono">python -m scripts.market_update</code>
      </EmptyState>
    );
  }
  return (
    <>
      <p className="text-[11px] text-[var(--text-tertiary)] -mt-1">
        只顯示個股（ETF 評分機制不同，<Link href="/radar?type=etf" className="text-[var(--brand-500)] hover:underline">單獨看 ETF</Link>）
      </p>
      <ul className="grid gap-2">
        {radarHits.map((h) => (
          <li key={h.stockId}>
            <RadarHitChip hit={h} />
          </li>
        ))}
      </ul>
    </>
  );
}

async function MoversSection({ direction }: { direction: "up" | "down" }) {
  const data = await getDashboardHome();
  const movers: WatchlistMover[] = direction === "up" ? data.moversUp : data.moversDown;
  return <MoversCard movers={movers} />;
}

async function MyScoreChangesSection() {
  let data: DashboardHomePayload;
  try {
    data = await getDashboardHome();
  } catch (e) {
    return <SectionError error={e} />;
  }
  const changes: ScoreChange[] = data.myScoreChanges;
  if (changes.length === 0) {
    return (
      <EmptyState size="sm">
        snapshot 歷史不足 7 天、或自選+持股還沒命中。等資料累積後就會出現。
      </EmptyState>
    );
  }
  // 取絕對值 top 8
  const top = changes.slice(0, 8);
  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-surface overflow-hidden">
      <ul className="divide-y divide-[var(--border-default)]">
        {top.map((c) => {
          const t = tone(c.delta);
          return (
            <li key={c.stockId} className="px-4 py-3 flex items-center gap-3 hover:bg-subtle">
              <Link
                href={`/stocks/${c.stockId}`}
                className="flex-1 flex items-center gap-2 min-w-0"
              >
                <span className="numeric font-semibold w-16 shrink-0">{c.stockId}</span>
                <span className="truncate text-[var(--text-secondary)]">{c.stockName}</span>
                {c.inHoldings && (
                  <span className="text-[10px] px-1 py-0.5 rounded bg-[var(--info-bg)] text-[var(--info-fg)] shrink-0">
                    持股
                  </span>
                )}
                {c.inWatchlist && !c.inHoldings && (
                  <span className="text-[10px] px-1 py-0.5 rounded bg-subtle text-[var(--text-tertiary)] shrink-0">
                    自選
                  </span>
                )}
              </Link>
              <div className="flex items-center gap-3 numeric text-sm shrink-0">
                <span className="text-[var(--text-tertiary)]">
                  {c.prevScore?.toFixed(0)} → {c.latestScore?.toFixed(0)}
                </span>
                <span
                  className={
                    "inline-flex items-center gap-0.5 font-semibold " +
                    (t === "up"
                      ? "text-[var(--color-up)]"
                      : t === "down"
                        ? "text-[var(--color-down)]"
                        : "text-[var(--text-secondary)]")
                  }
                >
                  <Icon
                    name={t === "up" ? "arrow_drop_up" : t === "down" ? "arrow_drop_down" : "remove"}
                    size={18}
                  />
                  {c.delta != null ? (c.delta > 0 ? "+" : "") + c.delta.toFixed(1) : "—"}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

async function ExDividendSection() {
  let data: DashboardHomePayload;
  try {
    data = await getDashboardHome();
  } catch (e) {
    return <SectionError error={e} />;
  }
  const events: ExDividendEvent[] = data.exDividend;
  return <ExDividendCard events={events} />;
}

async function FreshnessFooterSection() {
  let data: DashboardHomePayload;
  try {
    data = await getDashboardHome();
  } catch (e) {
    return <SectionError error={e} />;
  }
  const freshness: DataFreshness[] = data.freshness;
  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-wrap gap-3">
      {freshness.map((f) => (
        <div key={f.table} className="flex flex-col gap-1">
          <span className="text-xs text-[var(--text-tertiary)]">{f.label}</span>
          <DataFreshnessBadge tone={f.tone} latestDate={f.latestDate} lagDays={f.lagDays} />
        </div>
      ))}
    </div>
  );
}

function MoversCard({ movers }: { movers: WatchlistMover[] }) {
  if (!movers || movers.length === 0) {
    return <EmptyState size="sm">尚無自選股資料</EmptyState>;
  }
  return (
    <ul className="rounded-xl border border-[var(--border-default)] bg-surface overflow-hidden">
      {movers.map((m) => (
        <li
          key={m.stockId}
          className="flex items-center gap-3 h-12 px-4 border-b border-[var(--border-default)] last:border-0 hover:bg-subtle transition-colors"
        >
          <span className="numeric font-semibold text-sm w-12 shrink-0">{m.stockId}</span>
          <span className="text-sm text-[var(--text-secondary)] flex-1 truncate">{m.stockName}</span>
          <PriceCell price={m.close} deltaPct={m.changePct} variant="compact" />
        </li>
      ))}
    </ul>
  );
}

function ExDividendCard({ events }: { events: ExDividendEvent[] }) {
  if (events.length === 0) {
    return <EmptyState size="sm">近 7 日無除權息事件</EmptyState>;
  }
  return (
    <ul className="rounded-xl border border-[var(--border-default)] bg-surface overflow-hidden">
      {events.map((e, i) => (
        <li key={`${e.stockId}-${i}`} className="flex items-center gap-3 h-12 px-4 border-b border-[var(--border-default)] last:border-0">
          <span className="numeric text-xs font-semibold px-2 py-1 rounded bg-subtle text-[var(--text-secondary)]">
            {fmtDateShort(e.exDate)}
          </span>
          <span className="numeric text-sm font-semibold w-12 shrink-0">{e.stockId}</span>
          <span className="text-sm text-[var(--text-secondary)] flex-1 truncate">{e.stockName}</span>
          {e.cashDividend != null && (
            <span className="numeric text-xs text-[var(--color-up)]">
              配息 {fmtPrice(e.cashDividend)}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
