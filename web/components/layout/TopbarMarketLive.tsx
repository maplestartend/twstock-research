"use client";

/**
 * Topbar 大盤指數的客戶端覆蓋層。
 *
 * 為什麼客戶端覆蓋而不是 server fetch with revalidate：
 * - Topbar 在 layout.tsx 跨路由使用，原本 `revalidate:60` 讓使用者切頁時看到最多 60s 舊資料
 * - 個股詳情頁已是 30s polling 的「即時模式」，大盤跟著對齊才不會出現「個股已動、指數沒動」
 * - 與 LivePriceHeader 共享 `useMarketIntraday` 的 30s/120s（盤中/盤後）cadence
 *
 * 失敗（mis 422 / 網路斷）→ 回退到 SSR 收盤 snapshot（initialFallback）；
 * 成功時用即時值並標示 quoteTime。
 */
import { useMarketIntraday } from "@/lib/hooks/useIntraday";
import { Icon } from "@/components/primitives/Icon";
import { fmtPrice, fmtPct, taipeiDate, taipeiWeekday, tone, toneIcon, toneLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { MarketIntradayQuote, MarketSnapshot } from "@/lib/api";

const TONE_CLASS = {
  up: "text-[var(--color-up)]",
  down: "text-[var(--color-down)]",
  flat: "text-[var(--color-flat)]",
};

export function TopbarMarketLive({
  initialIntraday,
  initialFallback,
}: {
  /** SSR 已 prefetch 的盤中值（noCache）；提供時 mount 不再立刻打外部，避免「先收盤再跳即時」閃爍。 */
  initialIntraday: MarketIntradayQuote | null;
  /** EOD snapshot：盤中 API 失敗時 fallback。changePct 為百分比（與 MarketIntradayQuote 的小數不同）。 */
  initialFallback: MarketSnapshot | null;
}) {
  const live = useMarketIntraday(initialIntraday);

  // pct 一律處理成「小數」（fmtPct 期待 0.0123 = 1.23%）
  let value: number | null = null;
  let pct: number | null = null;
  let quoteTime: string | null = null;
  let isLive = false;
  let lastTradingDate: string | null = null;

  if (live) {
    value = live.value;
    pct = live.changePct;
    quoteTime = live.quoteTime;
    isLive = live.isLive && live.quoteSource !== "prev_close";
  } else if (initialFallback) {
    value = initialFallback.close;
    // EOD snapshot 的 changePct 是百分比（沿用既有格式），轉小數
    pct = initialFallback.changePct != null ? initialFallback.changePct / 100 : null;
    const today = taipeiDate(new Date());
    if (initialFallback.date && initialFallback.date !== today) {
      lastTradingDate = initialFallback.date;
    }
  }

  if (value == null) return null;

  const t = tone(pct);

  return (
    <div className="flex items-baseline gap-2 lg:gap-3">
      <span className="hidden sm:inline text-xs text-[var(--text-tertiary)] tracking-wide inline-flex items-center gap-1">
        加權指數
        {isLive && (
          <Icon
            name="bolt"
            size={11}
            filled
            className="text-[var(--color-up)]"
            label="即時"
          />
        )}
      </span>
      <span className="numeric text-base lg:text-xl font-bold">{fmtPrice(value)}</span>
      {pct != null && (
        <span className={cn("numeric text-sm font-medium inline-flex items-center gap-0.5", TONE_CLASS[t])}>
          <Icon name={toneIcon(pct)} size={18} label={toneLabel(pct)} />
          {fmtPct(pct, 2)}
        </span>
      )}
      {quoteTime && isLive && (
        <span className="hidden lg:inline numeric text-[11px] text-[var(--text-tertiary)] ml-1">
          @ {quoteTime}
        </span>
      )}
      {lastTradingDate && !live && (
        <span className="hidden lg:inline numeric text-[11px] text-[var(--text-tertiary)] ml-2">
          最近交易 {lastTradingDate} 週{taipeiWeekday(new Date(lastTradingDate + "T00:00:00+08:00"))}
        </span>
      )}
    </div>
  );
}
