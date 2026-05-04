"use client";

/**
 * ATR 動態出場（停損 / 動態停利）+ 即時股價覆蓋。
 *
 * 收盤模式下「現價」用的是昨日 close — 但停損距離 / 已破狀態對使用者最有用的是「現在」是不是危險。
 * 客戶端拿 mis 即時價後重算所有距離 + below_stop / triggered 旗標；
 * 即時取不到（興櫃 / 休市 / 422）就 fallback 收盤。
 */
import { useIntradayQuote } from "@/lib/hooks/useIntraday";
import { Icon } from "@/components/primitives/Icon";
import { fmtPct, fmtPrice } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AtrStopView, IntradayQuoteView } from "@/lib/api";
import { LiveQuoteBadge } from "./LivePriceHeader";

export function AtrStopSection({
  atr,
  stockId,
  fallbackClose,
  initialIntraday,
}: {
  atr: AtrStopView;
  stockId: string;
  fallbackClose: number | null;
  /** Server prefetch 過的盤中報價；client 第一次渲染就用即時價算停損距離，不會閃爍。 */
  initialIntraday?: IntradayQuoteView | null;
}) {
  const live = useIntradayQuote(stockId, initialIntraday ?? null);
  const latestClose = live?.price ?? fallbackClose;

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
    const dist = latestClose != null && latestClose > 0
      ? (latestClose - atr.trailing.stopPrice) / latestClose
      : null;
    stopBlocks.push({
      kind: "trailing",
      label: "追蹤式停損",
      desc: `進場後高點 ${fmtPrice(atr.trailing.peakSinceEntry)} − 2×ATR(${fmtPrice(atr.trailing.atr)})`,
      stop: atr.trailing.stopPrice,
      distancePct: dist,
      below: latestClose != null && latestClose < atr.trailing.stopPrice,
    });
  }

  return (
    <>
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
      <div className="flex items-center justify-end pt-1">
        <LiveQuoteBadge quote={live} fallbackClose={fallbackClose} />
      </div>
    </>
  );
}

function TakeProfitBlock({
  tp,
  latestClose,
}: {
  tp: NonNullable<AtrStopView["takeProfit"]>;
  latestClose: number | null;
}) {
  const dist = latestClose != null && latestClose > 0
    ? (latestClose - tp.takeProfitPrice) / latestClose
    : null;
  // 即時價 < takeProfitPrice 時視為觸發（覆蓋後端用收盤算的 triggered，避免「即時跌破但 UI 還顯示安全」）
  const triggered = tp.armed && latestClose != null && latestClose <= tp.takeProfitPrice;
  const tone: "neutral" | "up" | "warning" | "down" =
    !tp.armed ? "neutral" :
    triggered ? "down" :
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
           triggered ? "建議出場" :
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
