"use client";

/**
 * 個股詳情頁的「即時股價標頭」客戶端覆蓋層。
 *
 * 為什麼客戶端覆蓋而不是 server fetch：
 * - 個股頁是 dynamic 但仍是 server-side 一次性 render，沒有自動 refresh
 * - 使用者打開頁面後價格本來就會走、需要每 30 秒重抓
 * - 同一份 polling cadence 與 StockScorePanel 的「即時」mode 共用 useIntradayQuote hook
 * - 失敗/興櫃/休市時 fallback 顯示昨日收盤（initialClose），不會空白
 */
import { useIntradayQuote } from "@/lib/hooks/useIntraday";
import { PriceCell } from "@/components/primitives/PriceCell";
import { Icon } from "@/components/primitives/Icon";
import { fmtPrice } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { IntradayQuoteView } from "@/lib/api";

export function LivePriceHeader({
  stockId,
  initialClose,
  initialPrevClose,
  initialIntraday,
}: {
  stockId: string;
  initialClose: number;
  initialPrevClose: number | null;
  /** Server prefetch 過的盤中報價；提供時 client 第一次 render 就用即時值，不會「先收盤再跳即時」閃爍。 */
  initialIntraday?: IntradayQuoteView | null;
}) {
  const live = useIntradayQuote(stockId, initialIntraday ?? null);
  const price = live?.price ?? initialClose;
  const prev = live?.prevClose ?? initialPrevClose ?? undefined;
  return (
    <div className="flex flex-col items-end gap-1.5">
      <PriceCell price={price} prevClose={prev} variant="expanded" />
      <LiveQuoteBadge quote={live} fallbackClose={initialClose} />
    </div>
  );
}

/** 顯示 quote_source / quote_time / 「非盤中」標籤；live==null（API 失敗或 422）時顯示「昨日收盤」。 */
export function LiveQuoteBadge({
  quote,
  fallbackClose,
}: {
  quote: IntradayQuoteView | null;
  fallbackClose: number | null;
}) {
  if (!quote) {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-[var(--text-tertiary)]">
        <Icon name="schedule" size={11} />
        昨日收盤 {fallbackClose != null && fmtPrice(fallbackClose)}
      </span>
    );
  }
  const isLive = quote.isLive && quote.quoteSource !== "prev_close";
  const label = isLive ? "即時" : "非盤中（昨收）";
  const cls = isLive
    ? "bg-[var(--color-up-bg)] text-[var(--color-up)] border-[var(--color-up-border)]"
    : "bg-[var(--warning-bg)] text-[var(--warning-fg)] border-[var(--warning-border)]";
  const icon = isLive ? "bolt" : "schedule";
  return (
    <span className={cn("inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] border", cls)}>
      <Icon name={icon} size={11} filled={isLive} />
      {label}
      {quote.quoteTime && <span className="text-[var(--text-tertiary)] ml-1">@ {quote.quoteTime}</span>}
    </span>
  );
}
