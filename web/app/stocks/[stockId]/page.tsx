import { notFound } from "next/navigation";
import {
  apiGet,
  apiGetOptional,
  type AtrStopView,
  type StockMeta,
  type StockPriceBundle,
  type StockScoreView,
  type ScoreHistoryPoint,
} from "@/lib/api";
import { PriceCell } from "@/components/primitives/PriceCell";
import { ScoreBadge } from "@/components/primitives/ScoreBadge";
import { RecommendationTag } from "@/components/primitives/RecommendationTag";
import { ScoreBreakdownBars } from "@/components/primitives/ScoreBreakdownBars";
import { Icon } from "@/components/primitives/Icon";
import { SectionTitle } from "@/components/primitives/SectionTitle";
import { CandlestickChart } from "@/components/charts/CandlestickChartLazy";
import { ScoreTimelineChart } from "@/components/charts/ScoreTimelineChartLazy";
import { fmtPct, fmtPrice, fmtScore } from "@/lib/format";
import { cn } from "@/lib/utils";

export const revalidate = 60;

export default async function StockDetailPage({ params }: { params: Promise<{ stockId: string }> }) {
  const { stockId } = await params;

  const [meta, score, price, history, atr] = await Promise.all([
    apiGetOptional<StockMeta>(`/api/stocks/${stockId}/meta`),
    apiGetOptional<StockScoreView>(`/api/stocks/${stockId}/score`),
    apiGetOptional<StockPriceBundle>(`/api/stocks/${stockId}/price?days=180`),
    apiGetOptional<ScoreHistoryPoint[]>(`/api/stocks/${stockId}/score-history?days=90`).then((v) => v ?? []),
    apiGetOptional<AtrStopView>(`/api/stocks/${stockId}/atr-stop?multiplier=2.0`),
  ]);

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

      {/* 5-KPI row */}
      <section className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <ScoreCard label="短期" score={score.short.total} horizon="short" completeness={score.short.completeness} />
        <ScoreCard label="中期" score={score.mid.total} horizon="mid" completeness={score.mid.completeness} />
        <ScoreCard label="長期" score={score.long.total} horizon="long" completeness={score.long.completeness} />
        <ScoreCard label="綜合" score={score.compositeScore} horizon="composite" completeness={score.dataCompleteness} />
        <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-2">
          <span className="text-xs text-[var(--text-tertiary)] flex items-center gap-1.5">建議</span>
          <div className="flex items-center h-8">
            <RecommendationTag raw={score.recommendation} />
          </div>
          <span className="text-xs text-[var(--text-tertiary)] mt-auto">
            資料日期 {score.asOf}
            {score.isStale && (
              <span className="ml-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-[var(--warning-bg)] text-[var(--warning-fg)] border border-[var(--warning-border)]">
                <Icon name="warning" size={11} filled />
                過期 {score.staleDays} 天
              </span>
            )}
            {score.isPending && (
              <span className="ml-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-[var(--warning-bg)] text-[var(--warning-fg)] border border-[var(--warning-border)]" title="盤中暫態：14:00 後才視為當日確定收盤">
                <Icon name="hourglass_empty" size={11} filled />
                盤中暫態
              </span>
            )}
          </span>
        </div>
      </section>

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

      {/* Breakdown + signals */}
      <section className="grid grid-cols-1 xl:grid-cols-[1fr_1.1fr] gap-6">
        <div className="flex flex-col gap-4">
          <SectionTitle icon="analytics">評分拆解</SectionTitle>
          <div className="rounded-xl border border-[var(--border-default)] bg-surface p-5 flex flex-col gap-5">
            <BreakdownBlock title="短期" total={score.short.total} parts={score.short.parts} />
            <BreakdownBlock title="中期" total={score.mid.total} parts={score.mid.parts} />
            <BreakdownBlock title="長期" total={score.long.total} parts={score.long.parts} />
          </div>
        </div>
        <div className="flex flex-col gap-4">
          <SectionTitle icon="lightbulb">進出場建議</SectionTitle>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <SignalCard title="進場訊號" items={score.entry} tone="up" icon="login" />
            <SignalCard title="停損參考" items={score.stopLoss} tone="neutral" icon="shield" />
            <SignalCard title="停利參考" items={score.takeProfit} tone="down" icon="flag" />
            <SignalCard
              title="風險提示"
              items={score.warnings.length ? score.warnings : ["無明顯風險訊號"]}
              tone="warning"
              icon="warning"
            />
          </div>
        </div>
      </section>

      {/* ATR stop loss */}
      {atr && (atr.fixed || atr.trailing) && (
        <section className="flex flex-col gap-3">
          <SectionTitle icon="shield">ATR 動態停損（2× ATR-{atr.period}）</SectionTitle>
          <AtrStopBlock atr={atr} latestClose={price.ohlcv[price.ohlcv.length - 1]?.close ?? null} />
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

function ScoreCard({ label, score, horizon, completeness }: {
  label: string;
  score: number | null;
  horizon: "short" | "mid" | "long" | "composite";
  completeness?: number;
}) {
  const isLowTrust = completeness != null && completeness < 0.6;
  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-2">
      <span className="text-xs text-[var(--text-tertiary)] flex items-center gap-1.5">
        {label}分數
        {isLowTrust && (
          // 用中性灰色，避免和漲跌警示色（紅/綠）衝突；只是「可信度低」不是「危險」
          <span
            className="inline-flex items-center px-1 rounded text-[10px] bg-subtle text-[var(--text-tertiary)] border border-[var(--border-default)]"
            title={`僅 ${Math.round((completeness ?? 0) * 100)}% 子指標有資料，分數可信度較低`}
          >
            資料 {Math.round((completeness ?? 0) * 100)}%
          </span>
        )}
      </span>
      <div className="flex items-baseline gap-2">
        <span className="numeric text-[32px] font-bold leading-none text-[var(--text-primary)]">
          {fmtScore(score)}
        </span>
        <ScoreBadge score={score} size="sm" horizon={horizon} />
      </div>
    </div>
  );
}

function BreakdownBlock({ title, total, parts }: {
  title: string;
  total: number | null;
  parts: Record<string, number | null>;
}) {
  return (
    <div>
      <div className="flex items-baseline gap-2 mb-3">
        <span className="text-sm font-semibold text-[var(--text-secondary)]">{title}</span>
        <ScoreBadge score={total} size="sm" />
      </div>
      <ScoreBreakdownBars parts={parts} />
    </div>
  );
}

function SignalCard({ title, items, tone, icon }: { title: string; items: string[]; tone: "up" | "down" | "neutral" | "warning"; icon: string }) {
  const cls =
    tone === "up" ? "text-[var(--color-up)] bg-[var(--color-up-bg)] border-[var(--color-up-border)]" :
    tone === "down" ? "text-[var(--color-down)] bg-[var(--color-down-bg)] border-[var(--color-down-border)]" :
    tone === "warning" ? "text-[var(--warning-fg)] bg-[var(--warning-bg)] border-[var(--warning-border)]" :
    "text-[var(--text-secondary)] bg-subtle border-[var(--border-default)]";
  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-2.5">
      <div className={`inline-flex items-center gap-1.5 self-start px-2 py-1 rounded border ${cls}`}>
        <Icon name={icon} size={16} filled />
        <span className="text-xs font-semibold">{title}</span>
      </div>
      <ul className="flex flex-col gap-1.5 text-sm text-[var(--text-primary)] pl-1">
        {items.map((x, i) => (
          <li key={i} className="flex gap-2 leading-snug">
            <span className="text-[var(--text-tertiary)] shrink-0">·</span>
            <span>{x}</span>
          </li>
        ))}
      </ul>
    </div>
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
  const blocks: Array<{
    kind: "fixed" | "trailing";
    label: string;
    desc: string;
    stop: number;
    distancePct: number | null;
    extra: string;
    below: boolean;
  }> = [];
  if (atr.fixed) {
    const dist = latestClose != null && latestClose > 0
      ? (latestClose - atr.fixed.stopPrice) / latestClose
      : null;
    blocks.push({
      kind: "fixed",
      label: "固定式停損",
      desc: `進場參考 ${fmtPrice(atr.fixed.entryRef)} − ${(2.0).toFixed(1)}×ATR(${fmtPrice(atr.fixed.atr)})`,
      stop: atr.fixed.stopPrice,
      distancePct: dist,
      extra: `ATR ${fmtPrice(atr.fixed.atr)}`,
      below: latestClose != null && latestClose < atr.fixed.stopPrice,
    });
  }
  if (atr.trailing) {
    blocks.push({
      kind: "trailing",
      label: "追蹤式停損",
      desc: `進場後高點 ${fmtPrice(atr.trailing.peakSinceEntry)} − 2×ATR(${fmtPrice(atr.trailing.atr)})`,
      stop: atr.trailing.stopPrice,
      distancePct: latestClose != null && latestClose > 0
        ? (latestClose - atr.trailing.stopPrice) / latestClose
        : null,
      extra: `高點 ${fmtPrice(atr.trailing.peakSinceEntry)}`,
      below: atr.trailing.belowStop,
    });
  }
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {blocks.map((b) => {
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
    </div>
  );
}

