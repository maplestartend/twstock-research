import { notFound } from "next/navigation";
import {
  apiGetOptional,
  type AtrStopView,
  type HoldingRow,
  type StockMeta,
  type StockPriceBundle,
  type StockScoreView,
  type ScoreHistoryPoint,
} from "@/lib/api";
import { PriceCell } from "@/components/primitives/PriceCell";
import { Icon } from "@/components/primitives/Icon";
import { SectionTitle } from "@/components/primitives/SectionTitle";
import { CandlestickChart } from "@/components/charts/CandlestickChartLazy";
import { ScoreTimelineChart } from "@/components/charts/ScoreTimelineChartLazy";
import { fmtPct, fmtPrice } from "@/lib/format";
import { cn } from "@/lib/utils";
import { StockScorePanel } from "./StockScorePanel";

export const revalidate = 60;

export default async function StockDetailPage({ params }: { params: Promise<{ stockId: string }> }) {
  const { stockId } = await params;

  // 先平行抓不依賴 entry context 的東西
  const [meta, score, price, history, holdings] = await Promise.all([
    apiGetOptional<StockMeta>(`/api/stocks/${stockId}/meta`),
    apiGetOptional<StockScoreView>(`/api/stocks/${stockId}/score`),
    apiGetOptional<StockPriceBundle>(`/api/stocks/${stockId}/price?days=180`),
    apiGetOptional<ScoreHistoryPoint[]>(`/api/stocks/${stockId}/score-history?days=90`).then((v) => v ?? []),
    apiGetOptional<HoldingRow[]>(`/api/portfolio/holdings`),
  ]);

  // 若使用者持有此檔，把 entry_date / entry_price 拿來算 trailing 停損 + Chandelier 動態停利
  const myHolding = holdings?.find((h) => h.stockId === stockId) ?? null;
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

      {/* K-line */}
      <section className="flex flex-col gap-3">
        <SectionTitle icon="candlestick_chart">K 線與技術指標（近 180 日）</SectionTitle>
        <div className="rounded-xl border border-[var(--border-default)] bg-surface p-3">
          <CandlestickChart ohlcv={price.ohlcv} indicators={price.indicators} height={380} />
          <div className="flex gap-4 mt-2 px-2 text-xs text-[var(--text-tertiary)]">
            <LegendDot color="var(--chart-ma20)" label="MA20" />
            <LegendDot color="var(--chart-ma60)" label="MA60" />
            <LegendDot color="var(--color-up)" label="陽線 (收 &gt; 開)" />
            <LegendDot color="var(--color-down)" label="陰線 (收 &lt; 開)" />
          </div>
        </div>
      </section>

      {/* ATR exits（停損 + 動態停利） */}
      {atr && (atr.fixed || atr.trailing || atr.takeProfit) && (
        <section className="flex flex-col gap-3">
          <SectionTitle icon="shield">
            ATR 動態出場（停損 2×、停利 {atr.takeProfit ? atr.takeProfit.multiplier.toFixed(1) : "3.0"}× ATR-{atr.period}）
          </SectionTitle>
          <AtrStopBlock atr={atr} latestClose={price.ohlcv[price.ohlcv.length - 1]?.close ?? null} />
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
        <PriceCell price={last.close} prevClose={prev?.close} variant="expanded" />
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

function AtrStopBlock({ atr, latestClose }: { atr: AtrStopView; latestClose: number | null }) {
  type StopBlock = {
    kind: "fixed" | "trailing";
    label: string;
    desc: string;
    stop: number;
    distancePct: number | null;
    below: boolean;
  };
  const stopBlocks: StopBlock[] = [];
  if (atr.fixed) {
    const dist = latestClose != null && latestClose > 0
      ? (latestClose - atr.fixed.stopPrice) / latestClose
      : null;
    stopBlocks.push({
      kind: "fixed",
      label: "固定式停損",
      desc: `進場參考 ${fmtPrice(atr.fixed.entryRef)} − ${(2.0).toFixed(1)}×ATR(${fmtPrice(atr.fixed.atr)})`,
      stop: atr.fixed.stopPrice,
      distancePct: dist,
      below: latestClose != null && latestClose < atr.fixed.stopPrice,
    });
  }
  if (atr.trailing) {
    stopBlocks.push({
      kind: "trailing",
      label: "追蹤式停損",
      desc: `進場後高點 ${fmtPrice(atr.trailing.peakSinceEntry)} − 2×ATR(${fmtPrice(atr.trailing.atr)})`,
      stop: atr.trailing.stopPrice,
      distancePct: latestClose != null && latestClose > 0
        ? (latestClose - atr.trailing.stopPrice) / latestClose
        : null,
      below: atr.trailing.belowStop,
    });
  }
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
      {stopBlocks.map((b) => {
        const dist = b.distancePct;
        const tone =
          b.below ? "down" :
          dist != null && dist < 0.03 ? "down" :
          dist != null && dist < 0.08 ? "warning" :
          "up";
        const cls =
          tone === "down" ? "text-[var(--color-down)] bg-[var(--color-down-bg)] border-[var(--color-down-border)]" :
          tone === "warning" ? "text-[var(--warning-fg)] bg-[var(--warning-bg)] border-[var(--warning-border)]" :
          "text-[var(--color-up)] bg-[var(--color-up-bg)] border-[var(--color-up-border)]";
        return (
          <div
            key={b.kind}
            className={cn(
              "rounded-xl border bg-surface p-4 flex flex-col gap-3",
              "border-[var(--border-default)]",
            )}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-sm font-semibold text-[var(--text-primary)]">{b.label}</span>
              <span className={cn("inline-flex items-center px-2 py-0.5 rounded border text-xs font-semibold", cls)}>
                {b.below ? "已破" : dist != null ? `距 ${fmtPct(dist, 1)}` : "—"}
              </span>
            </div>
            <div className="flex items-baseline gap-3">
              <span className="numeric text-[26px] font-bold leading-none text-[var(--text-primary)]">
                {fmtPrice(b.stop)}
              </span>
              {latestClose != null && (
                <span className="text-xs text-[var(--text-tertiary)]">
                  現價 {fmtPrice(latestClose)}
                </span>
              )}
            </div>
            <span className="text-xs text-[var(--text-tertiary)] leading-relaxed">{b.desc}</span>
          </div>
        );
      })}
      {atr.takeProfit && (
        <TakeProfitBlock tp={atr.takeProfit} latestClose={latestClose} />
      )}
    </div>
  );
}

function TakeProfitBlock({
  tp,
  latestClose,
}: {
  tp: NonNullable<AtrStopView["takeProfit"]>;
  latestClose: number | null;
}) {
  // 距停利線：正值=還有獲利空間、負值=已跌穿（觸發）
  const dist = latestClose != null && latestClose > 0
    ? (latestClose - tp.takeProfitPrice) / latestClose
    : null;
  // 三段配色：未啟動 = neutral；已啟動但安全 = up；已觸發 = down（建議出場）
  const tone: "neutral" | "up" | "warning" | "down" =
    !tp.armed ? "neutral" :
    tp.triggered ? "down" :
    dist != null && dist < 0.03 ? "warning" :
    "up";
  const cls =
    tone === "down" ? "text-[var(--color-down)] bg-[var(--color-down-bg)] border-[var(--color-down-border)]" :
    tone === "warning" ? "text-[var(--warning-fg)] bg-[var(--warning-bg)] border-[var(--warning-border)]" :
    tone === "up" ? "text-[var(--color-up)] bg-[var(--color-up-bg)] border-[var(--color-up-border)]" :
    "text-[var(--text-secondary)] bg-subtle border-[var(--border-default)]";
  const armedPctTxt = `${(tp.armPnlThreshold * 100).toFixed(0)}%`;
  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-semibold text-[var(--text-primary)] inline-flex items-center gap-1.5">
          <Icon name="flag" size={14} filled />
          動態停利
        </span>
        <span className={cn("inline-flex items-center px-2 py-0.5 rounded border text-xs font-semibold", cls)}>
          {!tp.armed ? "尚未啟動" :
           tp.triggered ? "建議出場" :
           dist != null ? `距 ${fmtPct(dist, 1)}` : "—"}
        </span>
      </div>
      <div className="flex items-baseline gap-3">
        <span className="numeric text-[26px] font-bold leading-none text-[var(--text-primary)]">
          {fmtPrice(tp.takeProfitPrice)}
        </span>
        {latestClose != null && (
          <span className="text-xs text-[var(--text-tertiary)]">
            現價 {fmtPrice(latestClose)}
          </span>
        )}
      </div>
      <span className="text-xs text-[var(--text-tertiary)] leading-relaxed">
        進場後高點 {fmtPrice(tp.peakSinceEntry)} − {tp.multiplier.toFixed(1)}×ATR({fmtPrice(tp.atr)})
      </span>
      {/* armed 狀態：浮盈 / 持有日 */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[var(--text-tertiary)] pt-1 border-t border-[var(--border-default)]/60">
        <span>
          浮盈 <span className={cn("numeric font-medium", tp.unrealizedPnlPct >= 0 ? "text-[var(--color-up)]" : "text-[var(--color-down)]")}>
            {fmtPct(tp.unrealizedPnlPct, 1)}
          </span>
          {!tp.armed && tp.unrealizedPnlPct < tp.armPnlThreshold && (
            <span className="ml-1">/ 需 {armedPctTxt}</span>
          )}
        </span>
        <span>
          持有 <span className="numeric font-medium text-[var(--text-secondary)]">{tp.daysHeld} 日</span>
          {!tp.armed && tp.daysHeld < tp.armDaysThreshold && (
            <span className="ml-1">/ 需 {tp.armDaysThreshold} 日</span>
          )}
        </span>
      </div>
    </div>
  );
}

