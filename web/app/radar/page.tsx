import Link from "next/link";
import { apiGet, type RadarHit, type RadarHitsPage, type RadarStrategy } from "@/lib/api";
import { Icon } from "@/components/primitives/Icon";
import { PageHeader } from "@/components/primitives/PageHeader";
import { EmptyState } from "@/components/primitives/EmptyState";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { Pagination } from "@/components/primitives/Pagination";
import { DownloadXlsxButton } from "@/components/primitives/DownloadXlsxButton";
import { SectionTitle } from "@/components/primitives/SectionTitle";
import { PrefetchLink } from "@/components/primitives/PrefetchLink";
import { FilterChip } from "@/components/primitives/FilterChip";
import { fmtNum } from "@/lib/format";
import { cn } from "@/lib/utils";
import { RadarHitsLive } from "./RadarHitsLive";

export const revalidate = 60;

const MARKETS = ["上市", "上櫃", "ETF"] as const;
const TYPE_TABS = {
  stock: { label: "個股", markets: ["上市", "上櫃"] },
  etf: { label: "ETF", markets: ["ETF"] },
} as const;
type TypeTab = keyof typeof TYPE_TABS;

const PAGE_SIZE = 50;

// "all" = 全部不限筆數；其他為 top-N
type TopChoice = 30 | 50 | 100 | "all";
const TOP_CHOICES: TopChoice[] = [30, 50, 100, "all"];

function parseTop(v: string | undefined): TopChoice {
  if (v === "all") return "all";
  const n = Number(v);
  if (n === 50) return 50;
  if (n === 100) return 100;
  return 30;
}

export default async function RadarPage({
  searchParams,
}: {
  searchParams: Promise<{ strategy?: string; market?: string | string[]; top?: string; type?: string; page?: string }>;
}) {
  const sp = await searchParams;
  const topChoice = parseTop(sp.top);
  // top=0 表示全部
  const topApi = topChoice === "all" ? 0 : topChoice;
  const page = Math.max(1, Number(sp.page) || 1);

  // type tab: 個股 (default) / ETF — 兩者評分機制不同，分開看才有可比性
  const typeTab: TypeTab = sp.type === "etf" ? "etf" : "stock";

  // market 細選（個股 tab 下可再勾選 上市 / 上櫃）；ETF tab 強制 ETF only
  const allowedMarkets = [...TYPE_TABS[typeTab].markets];
  const marketsPicked = typeTab === "etf"
    ? ["ETF"]
    : ((Array.isArray(sp.market) ? sp.market : sp.market ? [sp.market] : allowedMarkets) as string[]).filter(
        (m) => allowedMarkets.includes(m as (typeof allowedMarkets)[number]),
      );
  const effectiveMarkets = marketsPicked.length ? marketsPicked : allowedMarkets;

  let allStrategies: RadarStrategy[];
  try {
    allStrategies = await apiGet<RadarStrategy[]>("/api/radar/strategies", { tags: ["snapshot"] });
  } catch (e) {
    return <BackendDownError error={e} pageTitle="雷達掃描" />;
  }

  // ETF tab 過濾掉「個股限定」策略（ETF 沒有 EPS/ROE/月營收）
  const strategies = typeTab === "etf"
    ? allStrategies.filter((s) => !s.stocksOnly)
    : allStrategies;

  // 預設第一個策略（短線強勢）；URL 帶的策略若該 tab 不適用，降回預設
  const activeStrategy =
    (sp.strategy && strategies.some((s) => s.name === sp.strategy))
      ? sp.strategy
      : (strategies[0]?.name ?? "");
  const activeStrategyInfo = strategies.find((s) => s.name === activeStrategy);

  // Server-side 分頁：只取當前頁的 rows + 過濾後 total（不再撈全部回前端 client slice）
  let pagedHits: RadarHit[];
  let totalHits: number;
  try {
    const res = await apiGet<RadarHitsPage>(
      buildHitsUrl(activeStrategy, effectiveMarkets, topApi, page),
      { tags: ["snapshot"] },
    );
    pagedHits = res.rows;
    totalHits = res.total;
  } catch (e) {
    return <BackendDownError error={e} pageTitle="雷達掃描" />;
  }

  // 「量能動能」策略：依 vr_macd 排序，UI 多顯示一個 VR 欄
  const showVrMacdCol = activeStrategy === "量能動能";

  const totalPages = Math.max(1, Math.ceil(totalHits / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);

  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1600px] mx-auto">
      <PageHeader
        title="雷達掃描"
        icon="radar"
        extra="ETF 與個股的評分維度不同（ETF 沒有 EPS/ROE/月營收），預設只顯示個股；ETF 請切到右側 tab 單獨看。"
      />

      {/* Type tabs: 個股 / ETF — 分開呈現避免混淆 */}
      <section className="flex flex-wrap items-center gap-2 border-b border-[var(--border-default)] pb-3">
        {(Object.keys(TYPE_TABS) as TypeTab[]).map((t) => {
          const active = t === typeTab;
          return (
            <FilterChip
              key={t}
              href={`/radar?${buildQuery({ strategy: activeStrategy, top: topChoice, type: t })}`}
              scroll={false}
              active={active}
              icon={t === "etf" ? "currency_exchange" : "monitoring"}
            >
              {TYPE_TABS[t].label}
            </FilterChip>
          );
        })}
        <span className="text-[11px] text-[var(--text-tertiary)] ml-2">
          {typeTab === "etf"
            ? "ETF：依技術 + 籌碼指標排序，長期分數仍受 ETF 無基本面所限"
            : "個股：上市 / 上櫃，含完整短中長分數"}
        </span>
      </section>

      {/* Strategy chips */}
      <section className="flex flex-col gap-3">
        <SectionTitle icon="ads_click">策略</SectionTitle>
        <div className="flex flex-wrap gap-2">
          {strategies.map((s) => {
            const active = s.name === activeStrategy;
            return (
              <PrefetchLink
                key={s.name}
                href={`/radar?${buildQuery({ strategy: s.name, market: marketsPicked, top: topChoice, type: typeTab })}`}
                scroll={false}
                title={s.stocksOnly ? `${s.description}（個股限定，ETF 無此資料）` : s.description}
                className={cn(
                  "inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors border",
                  active
                    ? "bg-[var(--brand-500)] text-white border-[var(--brand-500)] shadow-sm"
                    : "bg-surface text-[var(--text-secondary)] border-[var(--border-default)] hover:border-[var(--brand-300)] hover:text-[var(--text-primary)]",
                )}
              >
                <span className="font-medium">{s.name}</span>
                {s.stocksOnly && typeTab === "stock" && (
                  <span
                    className={cn(
                      "text-[9px] px-1 rounded",
                      active ? "bg-white/30" : "bg-[var(--info-bg)] text-[var(--info-fg)]",
                    )}
                    aria-label="個股限定"
                  >
                    個股限定
                  </span>
                )}
                <span className={cn(
                  "numeric text-xs px-1.5 py-0.5 rounded",
                  active ? "bg-white/20" : "bg-subtle text-[var(--text-tertiary)]",
                )}>
                  {fmtNum(s.hitCount)}
                </span>
              </PrefetchLink>
            );
          })}
        </div>
        {activeStrategyInfo && (
          <div className="rounded-lg bg-[var(--info-bg)] border border-[var(--info-border)] px-4 py-2 text-sm text-[var(--info-fg)] inline-flex items-start gap-2">
            <Icon name="info" size={16} filled className="mt-0.5" />
            <span>{activeStrategyInfo.description}</span>
          </div>
        )}
      </section>

      {/* Filters */}
      <section className="flex flex-wrap items-center gap-4 text-sm">
        {typeTab === "stock" && (
          <div className="flex items-center gap-2">
            <span className="text-[var(--text-tertiary)]">板別</span>
            {(["上市", "上櫃"] as const).map((m) => {
              const picked = marketsPicked.includes(m);
              const nextMarkets = picked ? marketsPicked.filter((x) => x !== m) : [...marketsPicked, m];
              return (
                <FilterChip
                  key={m}
                  href={`/radar?${buildQuery({ strategy: activeStrategy, market: nextMarkets.length ? nextMarkets : ["上市", "上櫃"], top: topChoice, type: typeTab })}`}
                  scroll={false}
                  active={picked}
                  size="sm"
                  icon={picked ? "check" : "add"}
                >
                  {m}
                </FilterChip>
              );
            })}
          </div>
        )}
        <div className="flex items-center gap-2">
          <span className="text-[var(--text-tertiary)]">顯示</span>
          {TOP_CHOICES.map((n) => {
            const active = n === topChoice;
            const label = n === "all" ? "全部" : `前 ${n} 名`;
            return (
              <FilterChip
                key={String(n)}
                href={`/radar?${buildQuery({ strategy: activeStrategy, market: marketsPicked, top: n, type: typeTab })}`}
                scroll={false}
                active={active}
                size="sm"
                className="numeric"
              >
                {label}
              </FilterChip>
            );
          })}
        </div>
      </section>

      {/* Hits table */}
      <section className="flex flex-col gap-3">
        <div className="flex items-end justify-between gap-3">
          <SectionTitle icon="format_list_numbered">
            {activeStrategy || "命中列表"}
            <span className="numeric text-xs text-[var(--text-tertiary)] font-normal ml-2">
              共 {totalHits} 檔
              {totalPages > 1 && ` · 第 ${safePage} / ${totalPages} 頁（每頁 ${PAGE_SIZE}）`}
            </span>
          </SectionTitle>
          <div className="flex items-center gap-2">
            {/* CSV / XLSX 都走後端 href 下載（top 預設 0 = 全部），命中表不再內嵌全部列進 client props */}
            <DownloadXlsxButton
              href={buildExportUrl("csv", activeStrategy, effectiveMarkets)}
              label="下載 CSV"
              size="sm"
              disabled={totalHits === 0}
            />
            <DownloadXlsxButton
              href={buildExportUrl("xlsx", activeStrategy, effectiveMarkets)}
              size="sm"
              disabled={totalHits === 0}
            />
          </div>
        </div>
        {totalHits === 0 ? (
          <EmptyState size="sm">
            當日無符合 {activeStrategy} 的標的。可切策略或放寬市場篩選。
          </EmptyState>
        ) : (
          <RadarHitsLive
            initialRows={pagedHits}
            total={totalHits}
            liveUrl={buildHitsUrl(activeStrategy, effectiveMarkets, topApi, page).replace("/hits?", "/hits/live?")}
            showLongCol={typeTab !== "etf"}
            showVrMacdCol={showVrMacdCol}
            activeStrategy={activeStrategy}
          />
        )}

        {totalPages > 1 && (
          <Pagination
            page={safePage}
            totalPages={totalPages}
            buildHref={(p) => `/radar?${buildQuery({ strategy: activeStrategy, market: marketsPicked, top: topChoice, type: typeTab, page: p })}`}
          />
        )}
      </section>
    </div>
  );
}

function buildHitsUrl(strategy: string | undefined, markets: string[], top: number, page: number): string {
  const q = new URLSearchParams();
  if (strategy) q.set("strategy", strategy);
  markets.forEach((m) => q.append("market", m));
  q.set("top", String(top));
  q.set("page", String(page));
  q.set("page_size", String(PAGE_SIZE));
  return `/api/radar/hits?${q.toString()}`;
}

// 匯出 URL（csv / xlsx 共用）：保留當前策略 + 市場過濾，top 由後端預設 0（全部）。
function buildExportUrl(fmt: "csv" | "xlsx", strategy: string | undefined, markets: string[]): string {
  const q = new URLSearchParams();
  if (strategy) q.set("strategy", strategy);
  markets.forEach((m) => q.append("market", m));
  return `/api/radar/export.${fmt}?${q.toString()}`;
}

function buildQuery({ strategy, market, top, type, page }: {
  strategy?: string; market?: string[]; top?: TopChoice | number; type?: string; page?: number;
}): string {
  const q = new URLSearchParams();
  if (strategy) q.set("strategy", strategy);
  (market ?? []).forEach((m) => q.append("market", m));
  if (top !== undefined && top !== null) q.set("top", String(top));
  if (type) q.set("type", type);
  if (page && page > 1) q.set("page", String(page));
  return q.toString();
}


