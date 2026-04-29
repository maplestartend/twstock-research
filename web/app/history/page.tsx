import Link from "next/link";
import {
  apiGet,
  apiGetOptional,
  type HistoryPerfSummary,
  type RadarStrategy,
} from "@/lib/api";
import { Icon } from "@/components/primitives/Icon";
import { PageHeader } from "@/components/primitives/PageHeader";
import { EmptyState } from "@/components/primitives/EmptyState";
import { ScoreBadge } from "@/components/primitives/ScoreBadge";
import { RecommendationTag } from "@/components/primitives/RecommendationTag";
import { PriceCell } from "@/components/primitives/PriceCell";
import { KPIStat } from "@/components/primitives/KPIStat";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { Th, Td } from "@/components/primitives/Table";
import { TableContainer } from "@/components/primitives/TableContainer";
import { StockIdCell } from "@/components/primitives/StockIdCell";
import { Pagination } from "@/components/primitives/Pagination";
import { fmtPct, tone, toneClass } from "@/lib/format";
import { cn } from "@/lib/utils";
import { DatePicker } from "./DatePicker";

export const revalidate = 300;

const PAGE_SIZE = 50;
const TYPE_TABS = {
  stock: { label: "個股", markets: ["上市", "上櫃"] },
  etf: { label: "ETF", markets: ["ETF"] },
} as const;
type TypeTab = keyof typeof TYPE_TABS;

export default async function HistoryPage({
  searchParams,
}: {
  searchParams: Promise<{ as_of?: string; strategy?: string; type?: string; page?: string }>;
}) {
  const sp = await searchParams;
  const typeTab: TypeTab = sp.type === "etf" ? "etf" : "stock";
  const page = Math.max(1, Number(sp.page) || 1);

  let dates: string[];
  try {
    dates = await apiGet<string[]>("/api/history/dates");
  } catch (e) {
    return <BackendDownError error={e} pageTitle="歷史追蹤" />;
  }

  if (dates.length === 0) {
    return (
      <div className="p-4 lg:p-8 max-w-[1200px] mx-auto">
        <Header />
        <EmptyState className="mt-6">
          還沒有歷史快照。跑一次 <code className="font-mono">python -m scripts.market_update</code> 就會自動存檔。
        </EmptyState>
      </div>
    );
  }

  // dates.length === 0 已在上方 early return，此處 dates[0] 一定存在
  const asOf: string = sp.as_of && dates.includes(sp.as_of) ? sp.as_of : (dates[0] as string);

  // market 參數依 typeTab
  const marketParams = TYPE_TABS[typeTab].markets.map((m) => `market=${encodeURIComponent(m)}`).join("&");

  // 拉「該 as_of 在當前 type 下各策略命中數」 + 該策略 performance
  // 注意：performance 需要先知道 validStrategy 才能算 URL，但「拿 strategies 清單」與
  // 「拿 sp.strategy 對應的 performance」其實只都依賴 asOf — 我們先樂觀用 sp.strategy
  // 發 perf，並行拉 strategies；拉完再決定 validStrategy 是否就是樂觀那個。若不是
  // （sp.strategy 無效或空），補一次 perf；命中率高時省 1 個 RTT。
  const optimisticStrategy = sp.strategy ?? null;
  const optimisticPerfUrl = optimisticStrategy
    ? `/api/history/performance?as_of=${encodeURIComponent(asOf)}&strategy=${encodeURIComponent(optimisticStrategy)}&top=0&${marketParams}`
    : null;

  const [strategiesResult, optimisticPerf] = await Promise.all([
    apiGet<RadarStrategy[]>(
      `/api/history/strategies?as_of=${encodeURIComponent(asOf)}&${marketParams}`,
    ).catch(() => [] as RadarStrategy[]),
    optimisticPerfUrl ? apiGetOptional<HistoryPerfSummary>(optimisticPerfUrl) : Promise.resolve(null),
  ]);

  // ETF tab 額外過濾「個股限定」策略
  const tabFiltered = typeTab === "etf" ? strategiesResult.filter((s) => !s.stocksOnly) : strategiesResult;
  const strategies = tabFiltered.filter((s) => s.hitCount > 0);

  const validStrategy = sp.strategy && strategies.some((s) => s.name === sp.strategy)
    ? sp.strategy
    : (strategies[0]?.name);

  const qs = (patch: { as_of?: string; strategy?: string; type?: TypeTab; page?: number }): string => {
    const params = new URLSearchParams();
    const finalAsOf = patch.as_of ?? asOf;
    const finalStrat = patch.strategy ?? validStrategy;
    const finalType = patch.type ?? typeTab;
    if (finalAsOf) params.set("as_of", finalAsOf);
    if (finalStrat) params.set("strategy", finalStrat);
    if (finalType !== "stock") params.set("type", finalType);
    if (patch.page && patch.page > 1) params.set("page", String(patch.page));
    return params.toString();
  };

  // 樂觀預取命中：optimisticStrategy 就是 validStrategy → 直接用
  // 否則 fallback 到 validStrategy（通常是 strategies[0]）再拉一次
  let perf: HistoryPerfSummary | null;
  if (validStrategy && validStrategy === optimisticStrategy) {
    perf = optimisticPerf;
  } else if (validStrategy) {
    perf = await apiGetOptional<HistoryPerfSummary>(
      `/api/history/performance?as_of=${encodeURIComponent(asOf)}&strategy=${encodeURIComponent(validStrategy)}&top=0&${marketParams}`,
    );
  } else {
    perf = null;
  }

  // Client-side 50/頁分頁
  const allRows = perf?.rows ?? [];
  const totalRows = allRows.length;
  const totalPages = Math.max(1, Math.ceil(totalRows / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pageStart = (safePage - 1) * PAGE_SIZE;
  const pagedRows = allRows.slice(pageStart, pageStart + PAGE_SIZE);

  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1600px] mx-auto">
      <Header />

      {/* Filters */}
      <section className="flex flex-wrap items-center gap-6 text-sm">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[var(--text-tertiary)]">快照日期</span>
          <div className="flex items-center gap-1 flex-wrap">
            {dates.slice(0, 10).map((d) => {
              const active = d === asOf;
              return (
                <Link
                  key={d}
                  href={`/history?${qs({ as_of: d })}`}
                  scroll={false}
                  title={d}
                  className={cn(
                    "numeric px-2.5 py-1 rounded border text-xs transition-colors",
                    active
                      ? "bg-[var(--brand-500)] text-white border-[var(--brand-500)]"
                      : "bg-surface text-[var(--text-tertiary)] border-[var(--border-default)] hover:border-[var(--brand-300)]",
                  )}
                >
                  <span className="numeric">{d.slice(5)}</span>
                </Link>
              );
            })}
            {dates.length > 10 && (
              <>
                <span className="text-xs text-[var(--text-tertiary)] ml-1">更早</span>
                <DatePicker
                  dates={dates}
                  current={asOf}
                  preservedParams={{
                    strategy: validStrategy,
                    type: typeTab !== "stock" ? typeTab : undefined,
                  }}
                />
              </>
            )}
          </div>
        </div>
      </section>

      {/* 個股 / ETF tab */}
      <section className="flex flex-wrap items-center gap-2 border-b border-[var(--border-default)] pb-3">
        {(Object.keys(TYPE_TABS) as TypeTab[]).map((t) => {
          const active = t === typeTab;
          return (
            <Link
              key={t}
              href={`/history?${qs({ type: t, strategy: undefined, page: 1 })}`}
              scroll={false}
              className={cn(
                "inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors border",
                active
                  ? "bg-[var(--brand-500)] text-white border-[var(--brand-500)]"
                  : "bg-surface text-[var(--text-secondary)] border-[var(--border-default)] hover:border-[var(--brand-300)]",
              )}
            >
              <Icon name={t === "etf" ? "currency_exchange" : "monitoring"} size={16} filled={active} />
              {TYPE_TABS[t].label}
            </Link>
          );
        })}
        <span className="text-[11px] text-[var(--text-tertiary)] ml-2">
          {typeTab === "etf" ? "ETF：技術 + 籌碼策略命中" : "個股：上市 / 上櫃，含完整策略"}
        </span>
      </section>

      <section className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-[var(--text-tertiary)] mr-2">策略</span>
        {strategies.length === 0 ? (
          <span className="text-xs text-[var(--text-tertiary)]">當日 {TYPE_TABS[typeTab].label} 無任何命中</span>
        ) : (
          strategies.map((s) => (
            <StrategyChip
              key={s.name}
              label={s.name}
              count={s.hitCount}
              active={validStrategy === s.name}
              href={`/history?${qs({ strategy: s.name, page: 1 })}`}
            />
          ))
        )}
      </section>

      {/* Summary + table */}
      {!perf ? (
        <EmptyState>
          {validStrategy ? `當日「${validStrategy}」無命中` : "當日無任何命中"}
        </EmptyState>
      ) : perf.daysElapsed < 1 ? (
        // 當日命中尚未經過任何交易日 → 0%/0 天 KPI 看起來像 bug，改用「累積中」骨架
        // （UIUX/PM 審查 P0-5：歷史追蹤頁第一眼 0% / 0 天讓人誤以為功能壞掉）
        <section className="rounded-xl border border-dashed border-[var(--border-default)] bg-surface/60 p-6 flex flex-col items-center gap-2 text-center">
          <Icon name="hourglass_empty" size={28} className="text-[var(--text-tertiary)]" />
          <h2 className="text-base font-semibold text-[var(--text-primary)]">資料累積中</h2>
          <p className="text-sm text-[var(--text-secondary)] max-w-md">
            {asOf} 那天命中 <span className="numeric font-semibold">{perf.hitCount}</span> 檔
            {validStrategy && ` 「${validStrategy}」`}，但尚未經過任何交易日 →
            勝率 / 平均漲幅還無法計算。明天盤後再來看。
          </p>
        </section>
      ) : (
        <>
          <section className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <KPIStat label="命中檔數" value={perf.hitCount.toString()} tone="neutral" />
            <KPIStat
              label="勝率"
              value={perf.winRate != null ? `${(perf.winRate * 100).toFixed(1)}%` : "—"}
              tone={perf.winRate != null && perf.winRate >= 0.5 ? "up" : perf.winRate != null && perf.winRate < 0.5 ? "down" : "neutral"}
              footnote={`${perf.winCount} 勝 / ${perf.lossCount} 敗`}
            />
            <KPIStat
              label="平均漲幅"
              value={perf.avgChangePct != null ? fmtPct(perf.avgChangePct, 2) : "—"}
              tone={tone(perf.avgChangePct)}
            />
            <KPIStat
              label="經過天數"
              value={`${perf.daysElapsed} 天`}
              tone="neutral"
              footnote={perf.latestDate ? `比到 ${perf.latestDate}` : undefined}
            />
          </section>

          <section className="flex flex-col gap-3">
            <h2 className="text-base font-semibold inline-flex items-center gap-2">
              <Icon name="history" size={20} className="text-[var(--brand-500)]" />
              {asOf} 命中表現
              <span className="numeric text-xs text-[var(--text-tertiary)] font-normal ml-2">
                共 {totalRows} 檔
                {totalPages > 1 && ` · 第 ${safePage} / ${totalPages} 頁（每頁 ${PAGE_SIZE}）`}
              </span>
              <span className="text-[11px] text-[var(--text-tertiary)] font-normal ml-2">依綜合分數降序</span>
            </h2>
            <TableContainer>
              <table className="w-full text-[15px] min-w-[1200px] table-fixed">
                <thead className="bg-subtle">
                  <tr>
                    <Th className="w-[170px]">代號 / 名稱</Th>
                    <Th align="right" className="w-[100px]">{asOf} 收盤</Th>
                    <Th align="right" className="w-[100px]">目前收盤</Th>
                    <Th align="right" className="w-[88px]">漲跌幅</Th>
                    <Th align="center" className="w-[88px]">短期</Th>
                    <Th align="center" className="w-[88px]">中期</Th>
                    <Th align="center" className="w-[88px]">長期</Th>
                    <Th align="center" className="w-[88px]">綜合</Th>
                    <Th align="center" className="w-[108px]">當時建議</Th>
                    <Th>命中策略</Th>
                  </tr>
                </thead>
                <tbody>
                  {pagedRows.map((r) => (
                    <tr key={r.stockId} className="border-t border-[var(--border-default)] hover:bg-subtle transition-colors">
                      <Td>
                        <StockIdCell stockId={r.stockId} stockName={r.stockName} />
                      </Td>
                      <Td align="right">
                        <PriceCell price={r.snapshotClose} variant="compact" />
                      </Td>
                      <Td align="right">
                        <PriceCell price={r.latestClose} variant="compact" />
                      </Td>
                      <Td align="right">
                        <span className={cn("numeric font-semibold", toneClass(r.changePct))}>
                          {fmtPct(r.changePct, 2)}
                        </span>
                      </Td>
                      <Td align="center"><div className="flex justify-center"><ScoreBadge score={r.short} size="sm" horizon="short" /></div></Td>
                      <Td align="center"><div className="flex justify-center"><ScoreBadge score={r.mid} size="sm" horizon="mid" /></div></Td>
                      <Td align="center"><div className="flex justify-center"><ScoreBadge score={r.long} size="sm" horizon="long" /></div></Td>
                      <Td align="center"><div className="flex justify-center"><ScoreBadge score={r.composite} size="sm" horizon="composite" /></div></Td>
                      <Td align="center">
                        {r.recommendation ? <div className="flex justify-center"><RecommendationTag raw={r.recommendation} size="sm" /></div> : <span className="text-[var(--text-tertiary)]">—</span>}
                      </Td>
                      <Td>
                        <div className="flex flex-wrap gap-1">
                          {(r.strategies ?? "").split(",").filter(Boolean).map((s) => (
                            <span
                              key={s}
                              className={cn(
                                "text-[11px] font-medium px-1.5 py-0.5 rounded border",
                                s.trim() === validStrategy
                                  ? "bg-[var(--brand-tint-strong)] text-[var(--brand-700)] dark:text-[var(--brand-300)] border-[var(--brand-tint-border)]"
                                  : "bg-subtle text-[var(--text-secondary)] border-[var(--border-default)]",
                              )}
                            >
                              {s.trim()}
                            </span>
                          ))}
                        </div>
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableContainer>
            <p className="text-xs text-[var(--text-tertiary)]">
              漲跌幅 = （目前收盤 − 快照日收盤）/ 快照日收盤，未考慮除權息還原
            </p>
            {totalPages > 1 && (
              <Pagination
                page={safePage}
                totalPages={totalPages}
                buildHref={(p) => `/history?${qs({ page: p })}`}
              />
            )}
          </section>
        </>
      )}
    </div>
  );
}

function Header() {
  return (
    <PageHeader
      title="歷史追蹤"
      icon="history"
      description="回看某天雷達選出來的股票，看到今天為止的表現如何"
    />
  );
}

function StrategyChip({ label, active, href, count }: { label: string; active: boolean; href: string; count?: number }) {
  // 與雷達掃描的策略 chip 視覺一致：px-3 py-1.5 rounded-lg + active 帶 shadow-sm
  return (
    <Link
      href={href}
      scroll={false}
      className={cn(
        "inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors border",
        active
          ? "bg-[var(--brand-500)] text-white border-[var(--brand-500)] shadow-sm"
          : "bg-surface text-[var(--text-secondary)] border-[var(--border-default)] hover:border-[var(--brand-300)] hover:text-[var(--text-primary)]",
      )}
    >
      <span className="font-medium">{label}</span>
      {count != null && (
        <span className={cn(
          "numeric text-xs px-1.5 py-0.5 rounded",
          active ? "bg-white/20" : "bg-subtle text-[var(--text-tertiary)]",
        )}>{count}</span>
      )}
    </Link>
  );
}


