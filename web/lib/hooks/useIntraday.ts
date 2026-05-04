/**
 * 盤中即時報價輪詢 hook（共用給「個股詳情」與「我的持股」兩頁）。
 *
 * 抽出來的理由：StockScorePanel 已經有一份幾乎一樣的輪詢邏輯（trading-hours 30s /
 * off-hours 2 min / hidden tab pause / visibilitychange 立即補抓 / abort）。再加新頁
 * 不想再重複貼一份；同步抓兩種形狀（單檔 vs 批次）走同一份 polling cadence。
 *
 * - 單檔：useIntradayQuote(stockId) → IntradayQuoteView | null
 * - 批次：useHoldingsIntraday() → Record<stockId, IntradayQuoteView>
 *
 * 失敗策略：個別 fetch 失敗回 null（不維持舊值），讓 caller 自行 fallback 到收盤快照。
 *  上一筆成功值不保留是因為盤中切到非盤中（兼休市）時繼續顯示舊「即時」更誤導。
 */
"use client";

import { useEffect, useState } from "react";
import { apiGet, apiGetOptional, type IntradayQuoteView } from "@/lib/api";

const LIVE_REFRESH_MS = 30_000;
const LIVE_REFRESH_MS_OFFHOURS = 120_000;

/** 台北時區判斷現在是否為交易時段（週一~週五 9:00–13:30）。
 *  非交易時段時降頻輪詢（mis 此時只回昨收，重複打沒意義）。 */
function isTradingHoursTaipei(): boolean {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    weekday: "short",
    hour: "numeric",
    minute: "numeric",
    hour12: false,
  });
  const parts = fmt.formatToParts(new Date());
  const wd = parts.find((p) => p.type === "weekday")?.value ?? "";
  const hh = Number(parts.find((p) => p.type === "hour")?.value ?? 0);
  const mm = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
  if (wd === "Sat" || wd === "Sun") return false;
  const minutes = hh * 60 + mm;
  return minutes >= 9 * 60 && minutes <= 13 * 60 + 30;
}

/** Generic polling driver — tick 一次 + 之後依 trading-hour 設 interval；hidden tab pause、回前景補抓。
 *  enabled=false 時整個 effect 不啟動（讓同頁兩處 polling 可共享同一份來源、不重複輪詢）。
 *  skipInitialTick=true：caller 已從 SSR prefetch 拿到一份新鮮資料當初始 state，不必 mount 立刻再打一次外部，
 *    避免「server 渲染 → client 立即 fetch → 重新 setState 觸發畫面跳動」的肉眼可見閃爍。 */
function usePolling(
  tick: () => void | Promise<void>,
  deps: ReadonlyArray<unknown>,
  enabled = true,
  skipInitialTick = false,
): void {
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let handle: number | null = null;

    const safeTick = async () => {
      if (cancelled) return;
      try {
        await tick();
      } catch {
        // tick 自己負責 setState；這層 catch 只是防止 unhandledrejection
      }
    };

    const start = () => {
      if (handle != null) return;
      const interval = isTradingHoursTaipei() ? LIVE_REFRESH_MS : LIVE_REFRESH_MS_OFFHOURS;
      handle = window.setInterval(safeTick, interval);
    };
    const stop = () => {
      if (handle != null) {
        window.clearInterval(handle);
        handle = null;
      }
    };
    const onVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        // 回前景一定要立刻補抓（不管 skipInitialTick），因為使用者離開期間資料已舊
        safeTick();
        start();
      }
    };

    if (!skipInitialTick) safeTick();
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      cancelled = true;
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, skipInitialTick]);
}

/** 單檔輪詢：失敗或 422（興櫃 / 休市 / 上游異常）回 null，caller 端 fallback 收盤。
 *  initial：server 端先 fetch 一次塞進來當初始 state；提供時 mount 時不會再立刻發 request（避免閃爍）。 */
export function useIntradayQuote(
  stockId: string,
  initial: IntradayQuoteView | null = null,
): IntradayQuoteView | null {
  const [quote, setQuote] = useState<IntradayQuoteView | null>(initial);

  usePolling(
    async () => {
      const q = await apiGetOptional<IntradayQuoteView>(
        `/api/stocks/${encodeURIComponent(stockId)}/intraday`,
        { noCache: true },
      );
      setQuote(q);
    },
    [stockId],
    true,
    initial !== null, // 有初始值才 skip 第一次 tick
  );

  return quote;
}

/** 批次輪詢：回 Record<stockId, quote>。個別檔案失敗自動被後端略過，不在 map 裡。
 *  enabled=false：caller 已從別處拿到輪詢結果（例如父層 component 也在輪詢），不再啟動第二份 timer。
 *  initial：server 端先 fetch 一次塞進來當初始 state；提供時 mount 時不會再立刻發 request（避免閃爍）。 */
export function useHoldingsIntraday(
  enabled = true,
  initial: Record<string, IntradayQuoteView> = {},
): Record<string, IntradayQuoteView> {
  const [quotes, setQuotes] = useState<Record<string, IntradayQuoteView>>(initial);
  const hasInitial = Object.keys(initial).length > 0;

  usePolling(
    async () => {
      try {
        const list = await apiGet<IntradayQuoteView[]>(
          `/api/portfolio/holdings/intraday`,
          { noCache: true },
        );
        const next: Record<string, IntradayQuoteView> = {};
        for (const q of list) next[q.stockId] = q;
        setQuotes(next);
      } catch {
        // 整批失敗（後端掛了 / 網路斷）— 退回空 map，表單顯示收盤快照
        setQuotes({});
      }
    },
    [],
    enabled,
    hasInitial,
  );

  return quotes;
}
