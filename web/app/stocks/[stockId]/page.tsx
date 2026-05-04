import { notFound } from "next/navigation";
import {
  apiGetOptional,
  type AtrStopView,
  type HoldingContext,
  type StockMeta,
  type StockPriceBundle,
  type StockScoreView,
  type ScoreHistoryPoint,
} from "@/lib/api";
import { Icon } from "@/components/primitives/Icon";
import { SectionTitle } from "@/components/primitives/SectionTitle";
import { CandlestickChart } from "@/components/charts/CandlestickChartLazy";
import { ScoreTimelineChart } from "@/components/charts/ScoreTimelineChartLazy";
import { StockScorePanel } from "./StockScorePanel";
import { NarrativeSection } from "./NarrativeSection";
import { PeerComparisonSection } from "./PeerComparisonSection";
import { PositionSuggestCard } from "./PositionSuggestCard";
import { LivePriceHeader } from "./LivePriceHeader";
import { AtrStopSection } from "./AtrStopSection";

// 個股頁的分數是使用者最在意的即時資訊；若沿用 ISR/RSC cache，
// 軟導頁可能暫時看到舊分數（需 Ctrl+F5 才刷新）。改成 dynamic + noCache，
// 確保每次進頁都拿到最新 score_stock 結果。
export const dynamic = "force-dynamic";

const SCORE_NOCACHE = { noCache: true } as const;

export default async function StockDetailPage({ params }: { params: Promise<{ stockId: string }> }) {
  const { stockId } = await params;

  // 先平行抓不依賴 entry context 的東西
  const [meta, score, price, history, myHolding] = await Promise.all([
    apiGetOptional<StockMeta>(`/api/stocks/${stockId}/meta`),
    apiGetOptional<StockScoreView>(`/api/stocks/${stockId}/score`, SCORE_NOCACHE),
    apiGetOptional<StockPriceBundle>(`/api/stocks/${stockId}/price?days=180`),
    apiGetOptional<ScoreHistoryPoint[]>(`/api/stocks/${stockId}/score-history?days=90`, {
      tags: ["snapshot"],
    }).then((v) => v ?? []),
    apiGetOptional<HoldingContext>(`/api/portfolio/holding-context/${stockId}`, {
      tags: ["watchlist", "snapshot"],
    }),
  ]);

  // 若使用者持有此檔，把 entry_date / entry_price 拿來算 trailing 停損 + Chandelier 動態停利
  const atrParams = new URLSearchParams({ multiplier: "2.0" });
  if (myHolding?.entryDate && myHolding.avgCost > 0) {
    atrParams.set("entry_date", myHolding.entryDate);
    atrParams.set("entry_price", String(myHolding.avgCost));
  }
  const atr = await apiGetOptional<AtrStopView>(
    `/api/stocks/${stockId}/atr-stop?${atrParams.toString()}`,
  );

  // 完全找不到資料 → 404；有 meta（後端對任何代號都吐 fallback meta）但無 price 也視為不存在
  if (!meta || (!price && !score)) {
    notFound();
  }

  if (!price) {
    return (
      <div className="p-4 lg:p-8 max-w-[1200px] mx-auto">
        <h1 className="text-[22px] font-bold inline-flex items-center gap-2.5">
          <Icon name="monitoring" size={26} filled className="text-[var(--brand-500)]" />
          {meta.stockName || stockId}
        </h1>
        <div className="mt-6 rounded-xl border border-[var(--color-up-border)] bg-[var(--color-up-bg)] p-6 text-[var(--color-up)]">
          <strong className="block mb-1 inline-flex items-center gap-2">
            <Icon name="error" size={20} filled />
            無法載入資料
          </strong>
          <span className="text-sm">
            {stockId} 在資料庫可能完全沒有資料，或不足 60 天。執行：
            <code className="ml-1 font-mono text-xs px-1.5 py-0.5 rounded bg-surface">python -m scripts.market_update --days 260</code>
          </span>
        </div>
      </div>
    );
  }

  if (!score) {
    // 有價格但無評分：可能是資料剛灌完還沒跑快照
    return (
      <div className="p-4 lg:p-8 max-w-[1600px] mx-auto flex flex-col gap-6">
        <StockHeader meta={meta} price={price} />
        <div className="rounded-xl border border-dashed p-6 text-center text-sm text-[var(--text-tertiary)]">
          尚未產生訊號快照。可跑 <code>python -m scripts.market_update</code> 產出。
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 lg:p-8 max-w-[1600px] mx-auto flex flex-col gap-8">
      <StockHeader meta={meta} price={price} score={score} />

      {/* Mode toggle + 5-KPI row + breakdown + signals（client：可切收盤/即時/假設） */}
      <StockScorePanel stockId={stockId} initialScore={score} />

      {/* AI 解讀（client：on-demand fetch、後端永久快取，未設 API key 時自動隱藏） */}
      <NarrativeSection stockId={stockId} />

      {/* 同業比較（RSC：產業樣本不足 / ETF / 興櫃 → 整段隱藏） */}
      <PeerComparisonSection stockId={stockId} />

      {/* K-line */}
      <section className="flex flex-col gap-3">
        <SectionTitle icon="candlestick_chart">K 線與技術指標（近 180 日）</SectionTitle>
        <div className="rounded-xl border border-[var(--border-default)] bg-surface p-3">
          <CandlestickChart ohlcv={price.ohlcv} indicators={price.indicators} height={380} />
          <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 px-2 text-xs text-[var(--text-tertiary)]">
            <LegendDot color="var(--chart-ma20)" label="MA20" />
            <LegendDot color="var(--chart-ma60)" label="MA60" />
            <LegendDot color="var(--color-up)" label="陽線 (收 &gt; 開)" />
            <LegendDot color="var(--color-down)" label="陰線 (收 &lt; 開)" />
            <span className="ml-auto">成交量單位：張 (1 張 = 1,000 股)</span>
          </div>
        </div>
      </section>

      {/* ATR exits（停損 + 動態停利）+ 進場買進試算 */}
      {atr && (atr.fixed || atr.trailing || atr.takeProfit) && (
        <section className="flex flex-col gap-3">
          <SectionTitle icon="shield">
            ATR 動態出場（停損 2×、停利 {atr.takeProfit ? atr.takeProfit.multiplier.toFixed(1) : "3.0"}× ATR-{atr.period}）
          </SectionTitle>
          <AtrStopSection
            atr={atr}
            stockId={stockId}
            fallbackClose={price.ohlcv[price.ohlcv.length - 1]?.close ?? null}
          />
          {/* 把停損自動帶入「現在買的話該買幾張」決策卡：使用者不必跳到新增交易表單試算 */}
          {!myHolding && atr.fixed && (
            <PositionSuggestCard
              stockId={stockId}
              entryPrice={price.ohlcv[price.ohlcv.length - 1]?.close ?? null}
              stopPrice={atr.fixed.stopPrice}
            />
          )}
          {!myHolding && (
            <p className="text-xs text-[var(--text-tertiary)] pl-1">
              <Icon name="info" size={12} className="inline-block mr-1 align-text-bottom" />
              動態停利需要持倉的進場日 + 成本；買入此檔後此區會自動顯示「進場後高點 − 3×ATR」的停利線。
            </p>
          )}
        </section>
      )}

      {/* Score history */}
      <section className="flex flex-col gap-3">
        <SectionTitle icon="show_chart">分數走勢（近 90 日）</SectionTitle>
        <div className="rounded-xl border border-[var(--border-default)] bg-surface p-5">
          <ScoreTimelineChart data={history} height={280} />
          <div className="flex gap-4 mt-3 text-xs text-[var(--text-tertiary)]">
            <LegendDot color="var(--chart-series-short)" label="短期" />
            <LegendDot color="var(--chart-series-mid)" label="中期" />
            <LegendDot color="var(--chart-series-long)" label="長期" />
            <LegendDot color="var(--chart-series-composite)" label="綜合" />
          </div>
        </div>
      </section>
    </div>
  );
}

function StockHeader({ meta, price, score }: { meta: StockMeta; price: StockPriceBundle; score?: StockScoreView }) {
  const last = price.ohlcv[price.ohlcv.length - 1];
  const prev = price.ohlcv[price.ohlcv.length - 2];
  return (
    <section className="flex flex-col md:flex-row md:items-end gap-6">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-3">
          <span className="numeric text-[32px] font-bold text-[var(--text-primary)]">{meta.stockId}</span>
          <span className="text-xl text-[var(--text-secondary)]">{meta.stockName}</span>
        </div>
        <div className="flex gap-3 mt-2 text-xs text-[var(--text-tertiary)]">
          {meta.industry && <span>{meta.industry}</span>}
          {meta.marketType && <span>· {meta.marketType}</span>}
          {score?.asOf && <span>· 資料日期 {score.asOf}</span>}
        </div>
      </div>
      {last && (
        <LivePriceHeader
          stockId={meta.stockId}
          initialClose={last.close}
          initialPrevClose={prev?.close ?? null}
        />
      )}
    </section>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block w-3 h-3 rounded" style={{ backgroundColor: color }} />
      {label}
    </span>
  );
}

// AtrStopBlock / TakeProfitBlock 已抽到 ./AtrStopSection.tsx 客戶端覆蓋層

