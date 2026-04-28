import { Suspense } from "react";
import Link from "next/link";
import {
  apiGet,
  apiGetOptional,
  humanizeApiError,
  type PortfolioSummary,
  type HoldingRow,
  type RadarHit,
  type ExDividendEvent,
  type DataFreshness,
  type WatchlistMover,
  type RiskAlert,
  type SnapshotDelta,
} from "@/lib/api";
import { KPIStat } from "@/components/primitives/KPIStat";
import { HoldingsTable } from "@/components/primitives/HoldingsTable";
import { RadarHitChip } from "@/components/primitives/RadarHitChip";
import { RiskAlertList } from "@/components/primitives/RiskAlertList";
import { SnapshotDeltaPanel } from "@/components/primitives/SnapshotDeltaPanel";
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
  Skeleton,
} from "@/components/primitives/Skeleton";
import { fmtDateShort, fmtMoney, fmtPrice, tone } from "@/lib/format";

export const revalidate = 60;

export default function DashboardPage() {
  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1600px] mx-auto">
      <PageHeader title="今日戰情室" icon="dashboard" description="開工具第一眼看的整合資訊" />

      <Suspense fallback={<KpiRowSkeleton count={4} hero />}>
        <KpiSection />
      </Suspense>

      <section className="grid grid-cols-1 xl:grid-cols-[1.6fr_1fr] gap-6">
        <div className="flex flex-col gap-4">
          <SectionTitle icon="account_balance_wallet">持股快照</SectionTitle>
          <Suspense fallback={<TableSkeleton rows={4} cols={6} />}>
            <HoldingsSnapshotSection />
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
  let summary: PortfolioSummary;
  let radarHits: RadarHit[];
  let freshness: DataFreshness[];
  try {
    [summary, radarHits, freshness] = await Promise.all([
      apiGet<PortfolioSummary>("/api/portfolio/summary"),
      apiGet<RadarHit[]>("/api/dashboard/radar-hits?limit=6"),
      apiGet<DataFreshness[]>("/api/dashboard/data-freshness"),
    ]);
  } catch (e) {
    return <SectionError error={e} />;
  }
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

async function HoldingsSnapshotSection() {
  let holdings: HoldingRow[];
  let risks: RiskAlert[];
  let delta: SnapshotDelta | null;
  try {
    [holdings, risks, delta] = await Promise.all([
      // holdings 含 in_watchlist 旗標，加 'watchlist' tag 讓 toggleWatchlistAction 能精準清快取
      apiGet<HoldingRow[]>("/api/portfolio/holdings", { tags: ["watchlist"] }),
      apiGet<RiskAlert[]>("/api/portfolio/risk-alerts"),
      apiGetOptional<SnapshotDelta>("/api/dashboard/snapshot-delta?top=8"),
    ]);
  } catch (e) {
    return <SectionError error={e} />;
  }
  return (
    <>
      <HoldingsTable rows={holdings} />
      <RiskAlertList alerts={risks} />
      {delta && (
        <>
          <SectionTitle icon="compare_arrows">今日 vs 昨日</SectionTitle>
          <SnapshotDeltaPanel delta={delta} />
        </>
      )}
    </>
  );
}

async function RadarHitsSection() {
  let radarHits: RadarHit[];
  try {
    radarHits = await apiGet<RadarHit[]>("/api/dashboard/radar-hits?limit=6");
  } catch (e) {
    return <SectionError error={e} />;
  }
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
  const movers = (await apiGetOptional<WatchlistMover[]>(
    `/api/watchlist/movers?top=5&direction=${direction}`,
    { tags: ["watchlist"] },
  )) ?? [];
  return <MoversCard movers={movers} />;
}

async function ExDividendSection() {
  let events: ExDividendEvent[];
  try {
    events = await apiGet<ExDividendEvent[]>("/api/dashboard/ex-dividend?days_ahead=7");
  } catch (e) {
    return <SectionError error={e} />;
  }
  return <ExDividendCard events={events} />;
}

async function FreshnessFooterSection() {
  let freshness: DataFreshness[];
  try {
    freshness = await apiGet<DataFreshness[]>("/api/dashboard/data-freshness");
  } catch (e) {
    return <SectionError error={e} />;
  }
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
