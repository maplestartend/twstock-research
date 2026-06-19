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

import { useEffect, useRef, useState } from "react";
import { apiGet, apiGetOptional, type IntradayQuoteView, type MarketIntradayQuote } from "@/lib/api";

const LIVE_REFRESH_MS = 30_000;
const LIVE_REFRESH_MS_OFFHOURS = 120_000;

/** 台北時區判斷現在是否為交易時段（週一~週五 9:00–13:30）。
 *  非交易時段時降頻輪詢（mis 此時只回昨收，重複打沒意義）。 */
export function isTradingHoursTaipei(): boolean {
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

/** 大盤指數輪詢：與個股 useIntradayQuote 共享 polling cadence。失敗回 null（caller fallback 收盤）。
 *  initial：server prefetch 過的盤中報價當初始 state；提供時 mount 不再立刻打外部，避免「先收盤再跳即時」閃爍。 */
export function useMarketIntraday(
  initial: MarketIntradayQuote | null = null,
): MarketIntradayQuote | null {
  const [quote, setQuote] = useState<MarketIntradayQuote | null>(initial);

  usePolling(
    async () => {
      const q = await apiGetOptional<MarketIntradayQuote>(
        `/api/market/intraday`,
        { noCache: true },
      );
      setQuote(q);
    },
    [],
    true,
    initial !== null,
  );

  return quote;
}

type QuotesMap = Record<string, IntradayQuoteView>;

/**
 * 批次持股報價的「單一輪詢來源」單例 store。
 *
 * 為什麼要單例：戰情室同時掛了頂部即時 KPI（LivePortfolioKpis）與下方持股明細表
 * （LiveHoldingsTable），兩者在不同的 Suspense 子樹，無法靠 React hook / shared prop 共享，
 * 各自 useHoldingsIntraday 會起兩份 30s 計時器、打兩條 /holdings/intraday。
 * 把輪詢搬到 module 級單例：不論幾個 component 訂閱，整個 app 只有一個計時器、一條 in-flight
 * 請求，多訂閱者拿同一份廣播結果。對 caller 完全透明（hook 簽名不變）。
 *
 * cadence / 可見性 / skip-initial-tick 行為刻意對齊上面的 usePolling：
 * trading-hour 30s、off-hour 120s、hidden tab 暫停、回前景立即補抓。
 */
const holdingsIntradayStore = (() => {
  let quotes: QuotesMap = {};
  let gen = 0; // 丟棄過期 tick 回應用（apiGet 無 abort，靠世代號保證後到的舊回應不覆寫新值）
  let handle: number | null = null;
  let visBound = false;
  const subs = new Set<(q: QuotesMap) => void>();

  const emit = () => {
    for (const cb of subs) cb(quotes);
  };

  const tick = async () => {
    const myGen = ++gen;
    try {
      const list = await apiGet<IntradayQuoteView[]>(
        `/api/portfolio/holdings/intraday`,
        { noCache: true },
      );
      if (myGen !== gen) return; // 已有更新的 tick 啟動，丟棄這次過期回應
      const next: QuotesMap = {};
      for (const q of list) next[q.stockId] = q;
      quotes = next;
      emit();
    } catch {
      // 整批失敗（後端掛了 / 網路斷）— 退回空 map，表單顯示收盤快照（與舊行為一致）
      if (myGen !== gen) return;
      quotes = {};
      emit();
    }
  };

  const schedule = () => {
    if (handle != null) return;
    const interval = isTradingHoursTaipei() ? LIVE_REFRESH_MS : LIVE_REFRESH_MS_OFFHOURS;
    handle = window.setInterval(() => {
      void tick();
    }, interval);
  };
  const unschedule = () => {
    if (handle != null) {
      window.clearInterval(handle);
      handle = null;
    }
  };
  const onVisibility = () => {
    if (document.hidden) {
      unschedule();
    } else {
      void tick(); // 回前景一定立即補抓（離開期間資料已舊）
      schedule();
    }
  };

  return {
    subscribe(cb: (q: QuotesMap) => void, initial: QuotesMap): () => void {
      const wasIdle = subs.size === 0;
      const hasInitial = Object.keys(initial).length > 0;
      if (wasIdle) {
        // 0→1：重設基準。有新鮮 SSR initial 就用它，否則回乾淨 {}（比照舊版各 hook 的
        // useState({}) 起點，避免把上一輪掛載殘留的舊即時 map 廣播給「空 initial」的首位訂閱者）。
        quotes = hasInitial ? initial : {};
        // 推進世代：作廢任何上一輪殘留、仍 in-flight 的 tick，避免它返回後以舊資料覆寫剛設的基準。
        gen++;
      }
      subs.add(cb);
      cb(quotes); // 立即交付當前快照：晚加入的訂閱者直接拿到既有即時值，不會先閃昨收
      if (wasIdle) {
        // 啟動輪詢。有新鮮 initial 就跳過 mount 當下那一 tick（比照 usePolling 的 skipInitialTick）
        if (!hasInitial) void tick();
        if (typeof document !== "undefined" && !document.hidden) schedule();
        if (typeof document !== "undefined" && !visBound) {
          document.addEventListener("visibilitychange", onVisibility);
          visBound = true;
        }
      }
      return () => {
        subs.delete(cb);
        if (subs.size === 0) {
          unschedule();
          if (visBound) {
            document.removeEventListener("visibilitychange", onVisibility);
            visBound = false;
          }
        }
      };
    },
  };
})();

/** 批次輪詢：回 Record<stockId, quote>。個別檔案失敗自動被後端略過，不在 map 裡。
 *  底層共用 holdingsIntradayStore 單例 → 全 app 只有一個計時器、一條請求（多處掛載自動去重）。
 *  enabled=false：caller 已從別處拿到輪詢結果（例如父層 component 也在輪詢），不訂閱、回 initial。
 *  initial：server 端先 fetch 一次塞進來當初始 state；提供時 mount 時不會再立刻發 request（避免閃爍）。 */
export function useHoldingsIntraday(
  enabled = true,
  initial: QuotesMap = {},
): QuotesMap {
  const [quotes, setQuotes] = useState<QuotesMap>(initial);
  // initial 凍進 ref：父層每次 render 都可能傳新物件參考，不該因此重訂閱（effect deps 只看 enabled）
  const initialRef = useRef(initial);
  initialRef.current = initial;

  useEffect(() => {
    if (!enabled) return;
    return holdingsIntradayStore.subscribe(setQuotes, initialRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  return enabled ? quotes : initial;
}

/**
 * 通用「清單盤中即時重算」輪詢：給雷達 /hits/live 與自選 /overview/live 共用。
 *
 * - enabled=false（收盤模式）：直接回 initial（SSR 的收盤快照），不啟動 timer。
 * - enabled=true（即時模式）：輪詢 `url`（trading-hour 30s / off-hour 120s / hidden pause），
 *   成功就把整包換成即時版（每列自帶 isLive）；失敗保留收盤快照、refreshing=false。
 * - 切回收盤（enabled false）時 reset 成 initial，避免殘留上一輪的即時數字。
 *
 * initial 用 ref 凍結，避免父層每次 render 產生新 ref 時觸發 effect churn。
 */
export function useLiveListData<T>(
  url: string,
  initial: T,
  enabled: boolean,
): { data: T; live: boolean } {
  const initialRef = useRef(initial);
  initialRef.current = initial;
  const [data, setData] = useState<T>(initial);
  const [live, setLive] = useState(false);

  // 切換模式 / 換頁（url 變）→ 先回到收盤快照基準
  useEffect(() => {
    if (!enabled) {
      setData(initialRef.current);
      setLive(false);
    }
  }, [enabled, url]);

  usePolling(
    async () => {
      try {
        const res = await apiGet<T>(url, { noCache: true });
        setData(res);
        setLive(true);
      } catch {
        setData(initialRef.current);
        setLive(false);
      }
    },
    [url],
    enabled,
    false,
  );

  return { data, live };
}
