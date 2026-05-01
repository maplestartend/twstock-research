// FastAPI 薄封裝。Server Components 直接呼叫；Client Components 走 fetch。
// 開發期 next.config.mjs 的 rewrites 會把 /api/* 代理到 http://127.0.0.1:8000。

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

/**
 * 把 FastAPI 的 detail 訊息或 HTTP 狀態碼轉成「使用者看得懂的中文」。
 * 4 個進階頁的常見失敗都會走過這裡。
 */
export function humanizeApiError(raw: string | unknown): string {
  const msg = typeof raw === "string" ? raw : raw instanceof Error ? raw.message : String(raw);
  // 常見 backend detail patterns
  if (/insufficient.*data|insufficient_data/i.test(msg)) {
    return "這檔股票的歷史資料還不夠（至少需要 60 個交易日）。如果是新上市股票，請等更多交易日累積後再回測。";
  }
  if (/not.*found|404/.test(msg)) {
    return "找不到這檔股票或這項資料。確認代號是否正確（如 2330），或先跑一次「market_update」更新資料。";
  }
  if (/no_trades|沒有.*交易|0 trades/i.test(msg)) {
    return "回測期間沒有觸發任何進場條件。試試把進場門檻調低（例如 65 → 55）、或拉長回測天數。";
  }
  if (/timeout|逾時/i.test(msg)) {
    return "計算超時。請縮小參數範圍（減少股票數量或組合數）後重試。";
  }
  if (/422|Unprocessable/i.test(msg)) {
    return "輸入的參數有問題。請檢查股票代號格式（4~6 位數字）、數字範圍是否合理。";
  }
  if (/500|Internal Server Error/i.test(msg)) {
    return "後端發生未預期錯誤。請查看 FastAPI 視窗的 log，或重啟服務後重試。";
  }
  if (/Failed to fetch|NetworkError|ECONNREFUSED|fetch failed|ECONNRESET/i.test(msg)) {
    return "連不上後端服務。請確認 FastAPI 是否啟動（檢查 port 8000）。";
  }
  // 沒匹配到既有 pattern 直接回原文，但去掉「GET /api/... → 422」這類技術前綴
  return msg.replace(/^(GET|POST|PUT|DELETE|PATCH)\s+\S+\s*→?\s*\d*\s*/i, "");
}

export type FetchOptions = {
  revalidate?: number;   // 秒；Next.js RSC ISR
  noCache?: boolean;
  /** Next.js fetch cache tags — 之後 revalidateTag(tag) 可精準清除這支 endpoint 的快取
   *  而不必 revalidatePath('/', 'layout') 把整層 Data Cache 連帶清掉。 */
  tags?: string[];
};

/**
 * fetch 失敗時 throw 的錯誤型別。
 * 帶 status 是為了讓上層判斷「真的沒資料 (404)」vs「後端壞了 (5xx)」vs「網路斷 (status=0)」，
 * 否則只能 regex match Error.message，邏輯易錯。
 */
export class ApiError extends Error {
  status: number;          // HTTP 狀態；網路錯為 0
  path: string;
  detail?: string;         // FastAPI 回傳的 detail 欄位（若有）
  constructor(path: string, status: number, message: string, detail?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
    this.detail = detail;
  }
  isNotFound() { return this.status === 404; }
  isClientError() { return this.status >= 400 && this.status < 500; }
  isServerError() { return this.status >= 500; }
  isNetworkError() { return this.status === 0; }
}

export async function apiGet<T>(path: string, opts: FetchOptions = {}): Promise<T> {
  const url = path.startsWith("http") ? path : `${BASE}${path}`;
  const init: RequestInit & { next?: { revalidate?: number; tags?: string[] } } = {};
  if (opts.noCache) {
    init.cache = "no-store";
  } else {
    const next: { revalidate?: number; tags?: string[] } = {};
    next.revalidate = opts.revalidate ?? 60;
    if (opts.tags && opts.tags.length) next.tags = opts.tags;
    init.next = next;
  }

  // 一次 retry：FastAPI 偶發 sqlite busy / 短暫 thread pool 飽和會回 5xx 或 connection reset。
  // 對 GET 是 idempotent，250ms 後重試一次足以掩蓋大多數 transient 失敗。
  // 4xx 不 retry（client 錯誤、404 等）— 直接 throw 讓 SectionError 顯示。
  let lastError: ApiError | null = null;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const res = await fetch(url, init);
      if (res.ok) return (await res.json()) as T;
      let detail: string | undefined;
      try {
        const body = await res.clone().json() as { detail?: string };
        detail = body?.detail;
      } catch {}
      const apiErr = new ApiError(
        path,
        res.status,
        `GET ${path} → ${res.status} ${res.statusText}${detail ? ` (${detail})` : ""}`,
        detail,
      );
      if (apiErr.isClientError()) throw apiErr;
      lastError = apiErr;
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.isClientError()) throw e;
        lastError = e;
      } else {
        lastError = new ApiError(path, 0, e instanceof Error ? e.message : String(e));
      }
    }
    if (attempt === 0) await new Promise((r) => setTimeout(r, 250));
  }
  throw lastError ?? new ApiError(path, 0, "unknown error");
}

/**
 * 任何錯誤都吞掉、回 null。給「資料缺值不該炸頁」的場景用，例如 Topbar 的市場快照
 * （盤後資料還沒進 DB → 404 → null → Topbar 渲染空白即可，不必整頁 error boundary）。
 *
 * 真的需要分流「404 = 沒資料」vs「5xx = 後端壞了」的場景，請改用 apiGet 並 catch ApiError：
 *   try { await apiGet(...) } catch (e) {
 *     if (e instanceof ApiError && e.isNotFound()) return <EmptyState />;
 *     throw e;  // 5xx 讓 error.tsx 接
 *   }
 */
export async function apiGetOptional<T>(
  path: string,
  opts: FetchOptions = {},
): Promise<T | null> {
  try {
    return await apiGet<T>(path, opts);
  } catch {
    return null;
  }
}


/** Snapshot freshness：signal_history 最新日 vs daily_price 最新日。 */
export type SnapshotStatus = {
  snapshotAsOf: string | null;
  dailyPriceAsOf: string | null;
  isStale: boolean;
  datasetsSynced?: boolean;
  datasetDates?: Record<string, string | null>;
  staleReason?: string;
  canRefresh?: boolean;
};

export type RefreshSnapshotResponse = {
  rowsWritten: number;
  triggered: boolean;
};

/** signal_history 最新一天 vs 上一天的差異（戰情室 delta panel）。 */
export type HitChange = {
  stockId: string;
  stockName: string;
  composite: number | null;
  strategies: string[];
};

export type ScoreMover = {
  stockId: string;
  stockName: string;
  prevComposite: number | null;
  latestComposite: number | null;
  delta: number | null;
};

export type SnapshotDelta = {
  latestAsOf: string | null;
  prevAsOf: string | null;
  newHits: HitChange[];
  droppedHits: HitChange[];
  bigMovers: ScoreMover[];
};

/** 自選 / 持股 N 日綜合分數變化（widget on dashboard）。 */
export type ScoreChange = {
  stockId: string;
  stockName: string;
  inWatchlist: boolean;
  inHoldings: boolean;
  latestScore: number | null;
  prevScore: number | null;
  delta: number | null;
  asOfLatest: string | null;
  asOfPrev: string | null;
};

/* ===============================================================
   Response types — 手寫版（未來可換成 openapi-typescript 自動產生）
   欄位為 camelCase，因為 FastAPI 的 alias_generator = to_camel。
   =============================================================== */

export type MarketSnapshot = {
  date: string | null;
  close: number | null;
  changePct: number | null;
};

export type MarketBreadth = {
  nTotal: number; nUp: number; nDown: number; nUnchanged: number;
  advanceDeclineRatio: number | null;
  pctAboveMa20: number | null;
  pctAboveMa60: number | null;
  nNewHigh50D: number; nNewLow50D: number;
  newHighLowRatio: number | null;
  healthLabel: string | null;
  healthTone: "up" | "down" | "neutral" | "warning";
};

export type PortfolioSummary = {
  totalMarketValue: number;
  totalCost: number;
  unrealizedPnl: number;
  unrealizedPnlPct: number | null;
  netUnrealizedPnl?: number;
  netUnrealizedPnlPct?: number | null;
  estimatedSellCosts?: number;
  todayPnl: number;
  todayPnlPct: number | null;
  holdingCount: number;
};

export type HoldingRow = {
  stockId: string; stockName: string;
  shares: number; avgCost: number;
  entryDate: string | null;        // trade_log 起算的最早買入日；個股詳情拉動態停利時帶上
  price: number | null; prevClose: number | null; todayPct: number | null;
  marketValue: number | null;
  unrealizedPnl: number | null; unrealizedPnlPct: number | null;
  netUnrealizedPnl?: number | null; netUnrealizedPnlPct?: number | null;
  estimatedSellCosts?: number | null;
  shortScore: number | null; midScore: number | null; longScore: number | null; compositeScore: number | null;
  warnings: string[];
  // ATR 動態停損（trailing 優先，無進場日 fallback fixed；資料不足為 null）
  atrStop: number | null;
  atrDistancePct: number | null;   // (price - stop) / price，正值=安全、負值=已破停損
  atrKind: "trailing" | "fixed" | null;
  atrBelowStop: boolean;
  inWatchlist: boolean;
};

export type AtrStopView = {
  stockId: string;
  multiplier: number;
  period: number;
  fixed: {
    stopPrice: number;
    atr: number;
    distancePct: number | null;
    entryRef: number;
  } | null;
  trailing: {
    stopPrice: number;
    atr: number;
    peakSinceEntry: number;
    latestClose: number;
    belowStop: boolean;
  } | null;
  // Chandelier-style 動態停利。需 entry_date + entry_price 才有值
  takeProfit: {
    takeProfitPrice: number;
    atr: number;
    peakSinceEntry: number;
    latestClose: number;
    daysHeld: number;
    unrealizedPnlPct: number;
    armed: boolean;             // 浮盈 ≥ arm_pnl AND 持有 ≥ arm_days
    triggered: boolean;         // armed AND latest_close ≤ take_profit_price
    multiplier: number;
    armPnlThreshold: number;
    armDaysThreshold: number;
  } | null;
};

export type RiskAlert = {
  severity: "info" | "warning" | "critical";
  title: string; description: string; stockId: string | null;
};

export type RadarHit = {
  stockId: string; stockName: string;
  close: number | null;
  short: number | null; mid: number | null; long: number | null; composite: number | null;
  vrMacd: number | null;    // VR(26) 量能分數；給「量能動能」策略當主要排序依據
  recommendation: string | null;
  strategies: string | null;
  market?: string | null;   // "上市" | "上櫃" | "ETF" | "其他"
};

export type RadarStrategy = {
  name: string;
  description: string;
  hitCount: number;
  stocksOnly?: boolean;   // true = 個股限定（ETF 無 EPS/ROE/月營收）
};

export type TradeRow = {
  id: number;
  tradeDate: string;
  stockId: string; stockName: string | null;
  action: "BUY" | "SELL";
  shares: number; price: number;
  fee: number | null; tax: number | null;
  note: string | null;
  entryReason: string | null;
  tags: string | null;     // 逗號分隔："短線強勢,法人連買"
};

export type JournalStatRow = {
  tag: string;
  count: number;
  winRate: number | null;
  avgPnlPct: number | null;
  totalPnl: number;
};

export type RealizedPnlRow = {
  stockId: string; stockName: string | null;
  buyDate: string; sellDate: string;
  shares: number; buyPrice: number; sellPrice: number;
  cost: number; proceed: number;
  pnl: number; pnlPct: number | null;
};

export type RealizedPnlSummary = {
  totalPnl: number; pairCount: number; winCount: number;
  winRate: number | null;
  rows: RealizedPnlRow[];
};

export type WatchlistMover = {
  stockId: string; stockName: string;
  close: number | null; changePct: number | null; compositeScore: number | null;
  market?: string | null;
};

export type WatchlistOverviewRow = {
  stockId: string; stockName: string;
  close: number | null; changePct: number | null;
  short: number | null; mid: number | null; long: number | null; composite: number | null;
  recommendation: string | null; asOf: string | null;
  market?: string | null;   // "上市" | "上櫃" | "ETF" | "其他"
};

export type IndustryRotationRow = {
  industry: string; nMembers: number;
  ret1D: number | null;            // 等權當日報酬（給排行表）
  ret1DWeighted: number | null;    // 成交值加權當日報酬（給熱力圖著色）
  ret5D: number | null; ret20D: number | null; ret60D: number | null;
  heat: number | null;
  totalAmount: number | null;      // 最新交易日成交金額加總（TWD，給熱力圖磚塊面積）
  nUp: number;                     // 當日 ret_1d > 0 家數
  nFlat: number;                   // 當日 ret_1d == 0 家數
  nDown: number;                   // 當日 ret_1d < 0 家數
};

export type IndustryRotationResponse = {
  asOf: string | null;
  rows: IndustryRotationRow[];
};

export type IndustryMemberRow = {
  stockId: string; stockName: string;
  close: number | null;
  ret1D: number | null; ret5D: number | null; ret20D: number | null;
};

export type ExDividendEvent = {
  exDate: string; stockId: string; stockName: string;
  cashDividend: number | null; stockDividend: number | null;
  inHoldings?: boolean; inWatchlist?: boolean;
};

export type ExDividendCalendarEvent = {
  exDate: string; stockId: string; stockName: string;
  cumPrice: number | null; exPrice: number | null;
  dividendValue: number | null; eventType: string | null;
  yieldPct: number | null;
  inHoldings: boolean; inWatchlist: boolean;
};

export type HistoryPerfRow = {
  stockId: string; stockName: string;
  snapshotClose: number | null; latestClose: number | null; changePct: number | null;
  short: number | null; mid: number | null; long: number | null; composite: number | null;
  recommendation: string | null;
  strategies: string | null;
  latestDate: string | null;
};

export type HistoryPerfSummary = {
  asOf: string;
  latestDate: string | null;
  daysElapsed: number;
  hitCount: number; winCount: number; lossCount: number;
  winRate: number | null;
  avgChangePct: number | null;
  rows: HistoryPerfRow[];
  truncated: boolean;          // hit_count > rows.length 時為 true（被 hard cap 截）
};

// ===== Backtest =====
export type BacktestConfig = {
  entryThreshold: number; exitThreshold: number;
  stopLossPct: number; takeProfitPct: number;
  maxHoldDays: number; slippageBps: number;
  feeRate: number | null; taxRate: number;
  lookbackDays: number; useAdj: boolean;
  // ATR 動態停利（Chandelier）：預設 off 維持原行為
  trailingTpMode?: "off" | "both" | "only";
  trailingTpAtrMultiplier?: number;
  trailingTpArmPnl?: number;
  trailingTpArmDays?: number;
  trailingTpAtrPeriod?: number;
};

export type BacktestTrade = {
  entryDate: string; exitDate: string; holdDays: number;
  entryPrice: number; exitPrice: number;
  grossReturn: number; netReturn: number;
  exitReason: string;
};

export type BacktestDailyPoint = {
  date: string; close: number | null; shortScore: number | null;
};

export type BacktestSummary = {
  stockId: string; stockName: string | null;
  nTrades: number; winRate: number;
  avgReturn: number; totalReturn: number;
  maxDrawdown: number; buyAndHold: number; alpha: number;
  sharpe: number | null;          // per-trade mean / std
  sortino: number | null;         // per-trade mean / 下行 std
  calmar: number | null;          // total_return / |MDD|
};

export type BacktestResponse = {
  summary: BacktestSummary;
  trades: BacktestTrade[];
  dailySeries: BacktestDailyPoint[];
  config: BacktestConfig;
};

// ===== Portfolio backtest =====
export type PortfolioRow = {
  stockId: string; stockName: string | null;
  nTrades: number; winRate: number; avgReturn: number;
  totalReturn: number; maxDrawdown: number;
  buyAndHold: number; alpha: number;
  alphaVs0050: number | null; alphaVsTaiex: number | null;
  sharpe: number | null;
  sortino: number | null;
  calmar: number | null;
};

export type PortfolioAggregate = {
  nStocks: number; nWithTrades: number;
  avgStrategyReturn: number; avgBuyAndHold: number; avgAlpha: number;
  overallWinrate: number;
  bm0050: number | null; bmTaiex: number | null;
};

export type PortfolioBacktestResponse = {
  summary: PortfolioAggregate;
  rows: PortfolioRow[];
  config: BacktestConfig;
  startDate: string | null; endDate: string | null;
};

// ===== Grid search =====
export type GridSearchRow = {
  entry: number; exit: number; sl: number; tp: number;
  trailingTpK: number | null;       // null = 該組沒開動態停利；非 null 表示 mode="both" + K 倍數
  avgAlpha: number; avgTotalReturn: number;
  overallWinrate: number; nTradesTotal: number;
};

export type GridSearchResponse = {
  combos: number;
  rows: GridSearchRow[];
  best: GridSearchRow | null;
  elapsedSec: number;
};

export type WalkForwardSplitRow = {
  split: number; trainPeriod: string; testPeriod: string;
  bestEntry: number | null; bestExit: number | null;
  trainReturn: number; trainSharpe: number | null;
  testReturn: number; testSharpe: number | null;
  testAlpha0050: number | null; testNTrades: number;
};

export type WalkForwardResponse = {
  splits: WalkForwardSplitRow[];
  avgTrainReturn: number; avgTestReturn: number;
  overfitWarning: boolean; note: string | null;
};

// ===== Weight tuner =====
export type StockBreakdown = {
  stockId: string; stockName: string; close: number | null;
  shortParts: Record<string, number | null>;
  midParts: Record<string, number | null>;
  longParts: Record<string, number | null>;
  shortDefault: number | null;
  midDefault: number | null;
  longDefault: number | null;
  compositeDefault: number | null;
};

export type DefaultWeights = {
  short: Record<string, number>;
  mid: Record<string, number>;
  long: Record<string, number>;
};

export type TunerBreakdownResponse = {
  stocks: StockBreakdown[];
  defaultWeights: DefaultWeights;
};

export type WeightSet = {
  short: Record<string, number>;
  mid: Record<string, number>;
  long: Record<string, number>;
};

export type BuiltinPreset = {
  name: string;          // "default" / "conservative" / ...
  label: string;         // 「保守存股型」等中文標籤
  description: string;
  weights: WeightSet;
};

export type UserPreset = {
  name: string;          // 使用者命名
  description: string;
  weights: WeightSet;
  createdAt: string | null;
  updatedAt: string | null;
};

export type PresetListResponse = {
  builtin: BuiltinPreset[];
  user: UserPreset[];
};

export type VisibleKeysResponse = {
  short: string[];
  mid: string[];
  long: string[];
};

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const url = path.startsWith("http") ? path : `${BASE}${path}`;
  const hasBody = body !== undefined;
  const res = await fetch(url, {
    method: "POST",
    headers: hasBody ? { "Content-Type": "application/json; charset=utf-8" } : undefined,
    body: hasBody ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      if (j && typeof j.detail === "string") msg = j.detail;
      else msg = JSON.stringify(j);
    } catch {}
    throw new Error(msg);
  }
  return (await res.json()) as T;
}

export async function apiDelete<T = unknown>(path: string): Promise<T> {
  const url = path.startsWith("http") ? path : `${BASE}${path}`;
  const res = await fetch(url, { method: "DELETE", cache: "no-store" });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      if (j && typeof j.detail === "string") msg = j.detail;
    } catch {}
    throw new Error(msg);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export type DataFreshness = {
  table: string; label: string;
  latestDate: string | null; lagDays: number | null;
  tone: "ok" | "warning" | "error" | "neutral";
};

export type PositionSuggestRequest = {
  capital: number;
  entry_price: number;
  stop_price: number;
  risk_per_trade?: number;
  lot_size?: number;
};

export type PositionSuggestResponse = {
  maxShares: number;
  maxLots: number;
  maxPositionValue: number;
  riskAmount: number;
  riskPerShare: number;
};

export type FactorICRow = {
  factor: string;
  horizon: number;
  ic: number | null;
  icIr: number | null;
  topQuintileReturn: number | null;
  botQuintileReturn: number | null;
  nDates: number;
  avgNStocks: number;
  icCiLo: number | null;  // 95% CI 下界（Newey-West HAC）
  icCiHi: number | null;
};

export type FactorICResponse = {
  lookbackDays: number;
  horizons: number[];
  forwardReturnBasis: string;
  executionAssumption: string;
  icCiMethod: string;
  rows: FactorICRow[];
};

export type SubFactorICRow = {
  horizon: string;            // 'short' | 'mid' | 'long'
  factor: string;             // 'rsi', 'kd', 'ma_alignment', etc.
  forwardHorizon: number;     // 5 / 20 / 60
  ic: number | null;
  icIr: number | null;
  topQuintileReturn: number | null;
  botQuintileReturn: number | null;
  nDates: number;
  avgNStocks: number;
  icCiLo: number | null;
  icCiHi: number | null;
};

export type SubFactorICResponse = {
  lookbackDays: number;
  horizons: number[];
  forwardReturnBasis: string;
  executionAssumption: string;
  icCiMethod: string;
  rows: SubFactorICRow[];
};

export type RollingICRow = {
  date: string;                    // YYYY-MM-DD
  short: number | null;
  mid: number | null;
  long: number | null;
  composite: number | null;
  vrMacd: number | null;
};

export type RollingICResponse = {
  horizon: number;                 // forward horizon (天)
  window: number;                  // rolling window 大小
  lookbackDays: number;
  rows: RollingICRow[];
};

export type StockMeta = {
  stockId: string; stockName: string;
  industry: string | null; marketType: string | null;
};

export type OHLCV = {
  date: string; open: number; high: number; low: number; close: number;
  volume: number | null;
};

export type IndicatorPoint = {
  date: string;
  ma5: number | null; ma20: number | null; ma60: number | null;
  k9: number | null; d9: number | null;
  rsi14: number | null; bbUpper: number | null; bbLower: number | null;
};

export type StockPriceBundle = {
  stockId: string; ohlcv: OHLCV[]; indicators: IndicatorPoint[];
};

export type ScoreParts = {
  total: number | null;
  completeness: number;
  parts: Record<string, number | null>;
};

export type StockScoreView = {
  stockId: string; stockName: string; asOf: string; close: number;
  short: ScoreParts; mid: ScoreParts; long: ScoreParts;
  compositeScore: number | null;
  dataCompleteness: number;
  isStale: boolean;
  staleDays: number;
  isPending: boolean;        // as_of=今日且當下 < 14:00 → 資料盤中尚未確認
  // 盤中即時 / what-if 重算痕跡：是否使用了 live/override price 重算
  livePriceUsed: boolean;
  livePrice: number | null;
  recommendation: string;
  entry: string[]; stopLoss: string[]; takeProfit: string[]; warnings: string[];
};

export type IntradayQuoteView = {
  stockId: string;
  price: number;
  prevClose: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  bid1: number | null;
  ask1: number | null;
  volumeLots: number | null;
  quoteTime: string | null;     // "HH:MM:SS"
  isLive: boolean;              // false = 走 prevClose fallback（盤前/休市/三項都缺）
  // price 取得來源（5 秒撮合制 + 漲跌停鎖死的 fallback 鏈）：
  //   match → prev_match → limit_up/limit_down → midpoint → ask_only/bid_only → prev_close
  quoteSource:
    | "match" | "prev_match"
    | "limit_up" | "limit_down"
    | "midpoint" | "ask_only" | "bid_only"
    | "prev_close";
  changePct: number | null;     // (price - prevClose) / prevClose
};

export type ScoreHistoryPoint = {
  date: string;
  short: number | null; mid: number | null; long: number | null; composite: number | null;
};

/** LLM 對個股的中文敘事（POST /api/stocks/{id}/narrative）。
 *  cached=true 表示從 narrative_cache 撈出，沒打 LLM；cached=false 是這次剛打完並存進快取。 */
export type NarrativeView = {
  stockId: string;
  asOf: string;
  kind: string;        // "stock_overview" 等；未來會多 "radar_hit" / "backtest_report"
  narrative: string;
  model: string;
  cached: boolean;
};

/** GET /api/system/narrative-status — 前端用此控制 AI 按鈕是否可按。 */
export type NarrativeStatus = {
  available: boolean;
  model: string | null;
};


// ===== DQ (data quality) =====
export type PriceAnomaly = {
  stockId: string; stockName: string; market: string | null;
  kind: "limit_up" | "limit_down" | "volume_spike" | "stale" | "huge_gap";
  severity: "info" | "warning" | "critical";
  date: string; value: number | null; note: string;
};

export type StockGap = {
  stockId: string; stockName: string; table: string;
  missingDays: number; expected: number;
};

export type DqSummary = {
  asOf: string | null; nAnomalies: number; nGaps: number;
  anomalies: PriceAnomaly[]; gaps: StockGap[]; scope: string;
};

// ===== Search =====
export type SearchHit = {
  stockId: string; stockName: string;
  market: string | null; industry: string | null;
  inWatchlist: boolean;
};



// ===== Event-driven backtest (ex-dividend / split) =====
export type EventTradeRow = {
  stockId: string; stockName: string; exDate: string; year: string;
  eventType: "dividend" | "split";
  entryDate: string | null; entryPrice: number | null;
  exitDate: string | null; exitPrice: number | null;
  cashDividend: number; stockDividend: number;
  priceReturn: number | null; totalReturn: number | null;
};

export type StockEventStatsRow = {
  stockId: string; stockName: string; nEvents: number;
  winRate: number | null; avgTotalReturn: number | null; avgDividendYield: number | null;
};

export type EventBacktestSummary = {
  nEvents: number; nWithData: number;
  winRate: number | null; avgTotalReturn: number | null; avgPriceReturn: number | null;
  avgDividendYield: number | null; medianTotalReturn: number | null;
  bestReturn: number | null; worstReturn: number | null;
  totalDividend: number;
};

export type EventBacktestRequest = {
  stockIds: string[]; entryOffset: number; exitOffset: number;
  sinceYear: number; minDividend: number;
};

export type EventBacktestResponse = {
  summary: EventBacktestSummary;
  byStock: StockEventStatsRow[];
  trades: EventTradeRow[];
  configEcho: EventBacktestRequest;
};

// ===== Alerts =====
export type AlertRuleKind =
  | "price_below"
  | "price_above"
  | "score_drop"
  | "score_rise"
  | "atr_breached";

export type AlertRule = {
  id: number;
  stockId: string;
  ruleKind: AlertRuleKind;
  threshold: number | null;
  note: string | null;
  active: boolean;
  lastTriggeredAt: string | null;
  createdAt: string;
  // server-side 即時評估 (僅 active 規則；資料不足時 actualValue 為 null)
  actualValue: number | null;
  triggered: boolean;
};

export type AlertHit = {
  ruleId: number;
  stockId: string;
  stockName: string;
  ruleKind: AlertRuleKind;
  threshold: number | null;
  actualValue: number | null;
  message: string;
};
