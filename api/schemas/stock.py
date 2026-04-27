"""個股詳情相關 DTO。"""
from __future__ import annotations

from api.schemas.common import CamelModel, StockRef


class StockMeta(StockRef):
    industry: str | None = None
    market_type: str | None = None


class OHLCV(CamelModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


class IndicatorPoint(CamelModel):
    date: str
    ma5: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    k9: float | None = None
    d9: float | None = None
    rsi14: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None


class ScoreParts(CamelModel):
    total: float | None = None           # 資料不足時為 None，前端渲染 "—"
    completeness: float = 1.0              # 有效子指標權重比例，1.0 = 全部齊全
    parts: dict[str, float | None]


class StockScoreView(StockRef):
    as_of: str
    close: float
    short: ScoreParts
    mid: ScoreParts
    long: ScoreParts
    composite_score: float | None = None
    data_completeness: float = 1.0         # 三維加權後的整體可信度
    is_stale: bool = False                 # 最新資料日期距今 > 3 天
    stale_days: int = 0
    is_pending: bool = False               # as_of=今日且當下 < 14:00 → 資料尚未收盤確認
    # 盤中即時 / what-if 重算：若呼叫時帶 ?live=1 或 ?override_price=X，這兩個欄位會被填上。
    # 前端據此標示「盤中估算」並切換 UI 顏色，避免使用者把它當成收盤後 final 分數。
    live_price_used: bool = False
    live_price: float | None = None
    recommendation: str
    entry: list[str] = []
    stop_loss: list[str] = []
    take_profit: list[str] = []
    warnings: list[str] = []


class IntradayQuoteView(CamelModel):
    """盤中即時報價（mis.twse.com.tw）。盤後 / 興櫃 / 抓不到 → 422，前端可 fallback 收盤分數。

    quote_source 表示 price 從哪取得（5 秒撮合制下 z 多數時刻為空，要走 fallback）：
      "match"      — 最新撮合價 (z)
      "prev_match" — 前一筆撮合價 (pz)
      "midpoint"   — 最佳買賣中價 ((a1+b1)/2)；盤中正常波動會用到
      "prev_close" — 昨收 fallback；前三都缺，is_live=False
    """
    stock_id: str
    price: float
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    bid1: float | None = None
    ask1: float | None = None
    volume_lots: float | None = None
    quote_time: str | None = None
    is_live: bool = True       # False = 走 prev_close fallback（盤前/休市/三項都缺）
    quote_source: str = "match"
    change_pct: float | None = None     # (price - prev_close) / prev_close


class ScoreHistoryPoint(CamelModel):
    date: str
    short: float | None = None
    mid: float | None = None
    long: float | None = None
    composite: float | None = None


class StockPriceBundle(CamelModel):
    """K 線圖一次要的資料。"""

    stock_id: str
    ohlcv: list[OHLCV]
    indicators: list[IndicatorPoint]


class RadarHit(StockRef):
    close: float | None = None
    short: float | None = None
    mid: float | None = None
    long: float | None = None
    composite: float | None = None
    recommendation: str | None = None
    strategies: str | None = None
    market: str | None = None  # "上市" | "上櫃" | "ETF" | "其他"


class RadarStrategy(CamelModel):
    name: str
    description: str
    hit_count: int = 0
    stocks_only: bool = False  # True = 該策略只對個股有意義（ETF 無 EPS/ROE/月營收）


class WatchlistMover(StockRef):
    close: float | None = None
    change_pct: float | None = None
    composite_score: float | None = None
    market: str | None = None  # "上市" | "上櫃" | "ETF" | "其他"


class WatchlistOverviewRow(StockRef):
    close: float | None = None
    change_pct: float | None = None       # 當日漲跌幅（小數）
    short: float | None = None
    mid: float | None = None
    long: float | None = None
    composite: float | None = None
    recommendation: str | None = None
    as_of: str | None = None
    market: str | None = None             # "上市" | "上櫃" | "ETF" | "其他"


class ExDividendEvent(StockRef):
    """Dashboard 頂部用（讀 dividend 表）。"""
    ex_date: str
    cash_dividend: float | None = None
    stock_dividend: float | None = None
    in_holdings: bool = False
    in_watchlist: bool = False


class ExDividendCalendarEvent(StockRef):
    """除權息行事曆用（現場從 TWSE TWT49U 抓）。"""
    ex_date: str
    cum_price: float | None = None        # 除權息前收盤價
    ex_price: float | None = None         # 除權息參考價
    dividend_value: float | None = None   # 權值 + 息值
    event_type: str | None = None         # "權" | "息" | "權/息"
    yield_pct: float | None = None        # 殖利率估算 = dividend_value / cum_price
    in_holdings: bool = False
    in_watchlist: bool = False


class HistoryPerfRow(StockRef):
    snapshot_close: float | None = None
    latest_close: float | None = None
    change_pct: float | None = None
    short: float | None = None
    mid: float | None = None
    long: float | None = None
    composite: float | None = None
    recommendation: str | None = None
    strategies: str | None = None
    latest_date: str | None = None


class HistoryPerfSummary(CamelModel):
    as_of: str
    latest_date: str | None = None
    days_elapsed: int = 0
    hit_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float | None = None
    avg_change_pct: float | None = None
    rows: list[HistoryPerfRow] = []
    truncated: bool = False     # 命中數超過 hard cap 被截掉時為 True；hit_count 仍是原始全數


class DataFreshness(CamelModel):
    table: str
    label: str
    latest_date: str | None = None
    lag_days: int | None = None
    tone: str = "neutral"  # "ok" | "warning" | "error"
