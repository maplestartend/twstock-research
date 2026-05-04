"""持股 / 風險 DTO。"""
from __future__ import annotations

from api.schemas.common import CamelModel, StockRef, StockRefOptional


class PortfolioSummary(CamelModel):
    total_market_value: float = 0.0
    total_cost: float = 0.0
    unrealized_pnl: float = 0.0          # 毛損益（不含賣出成本）
    unrealized_pnl_pct: float | None = None
    net_unrealized_pnl: float = 0.0      # 扣預估賣出手續費 + 證交稅 0.3%
    net_unrealized_pnl_pct: float | None = None
    estimated_sell_costs: float = 0.0    # 預估「現在全賣」的稅費總和
    today_pnl: float = 0.0
    today_pnl_pct: float | None = None
    holding_count: int = 0


class HoldingRow(StockRef):
    shares: float
    avg_cost: float
    entry_date: str | None = None        # 持有期間的最早買入日（trade_log 起算）；給前端拉動態停利用
    price: float | None = None
    prev_close: float | None = None
    today_pct: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None       # 毛
    unrealized_pnl_pct: float | None = None
    net_unrealized_pnl: float | None = None   # 扣預估賣出成本
    net_unrealized_pnl_pct: float | None = None
    estimated_sell_costs: float | None = None
    short_score: float | None = None
    mid_score: float | None = None
    long_score: float | None = None
    composite_score: float | None = None
    warnings: list[str] = []
    # ATR 動態停損：trailing 優先（有 entry_date 才算），其次 fixed（用 avg_cost 當參考）
    atr_stop: float | None = None        # 停損價
    atr_distance_pct: float | None = None  # (latest_close - stop) / latest_close，正值表示距停損還有空間
    atr_kind: str | None = None          # "trailing" | "fixed" | None
    atr_below_stop: bool = False         # latest_close < stop → UI 應顯示紅色警示
    # ATR 動態停利（Chandelier 3×ATR；需 entry_date + entry_price 才算得出）
    atr_take_profit: float | None = None       # 停利價
    atr_take_profit_distance_pct: float | None = None  # (latest_close - tp) / latest_close
    atr_take_profit_armed: bool = False         # 浮盈 ≥ 8% AND 持有 ≥ 5 日才啟動
    atr_take_profit_triggered: bool = False     # armed AND latest_close ≤ tp → 建議出場
    in_watchlist: bool = False           # 該檔是否已在 watchlist.yaml


class HoldingContext(CamelModel):
    """個股頁所需的最小持倉資訊（避免每次拉整包 /holdings）。"""
    stock_id: str
    shares: float
    avg_cost: float
    entry_date: str | None = None


class RiskAlert(CamelModel):
    severity: str  # "info" | "warning" | "critical"
    title: str
    description: str
    stock_id: str | None = None


class TradeRow(StockRefOptional):
    id: int
    trade_date: str
    action: str  # "BUY" | "SELL"
    shares: float
    price: float
    fee: float | None = None
    tax: float | None = None
    note: str | None = None
    entry_reason: str | None = None
    tags: str | None = None    # 逗號分隔


class JournalStatRow(CamelModel):
    """每個 tag 的勝率彙總（基於 FIFO realized 配對）。"""
    tag: str
    count: int
    win_rate: float | None = None
    avg_pnl_pct: float | None = None
    total_pnl: float = 0.0


class JournalUpdateBody(CamelModel):
    """retroactive 更新單筆 trade journal。任一欄位 None 代表「不動」、空字串代表「清空」。"""
    entry_reason: str | None = None
    tags: str | None = None
    note: str | None = None


class RealizedPnlRow(StockRefOptional):
    buy_date: str
    sell_date: str
    shares: float
    buy_price: float
    sell_price: float
    cost: float
    proceed: float
    pnl: float
    pnl_pct: float | None = None


class RealizedPnlSummary(CamelModel):
    total_pnl: float = 0.0
    pair_count: int = 0
    win_count: int = 0
    win_rate: float | None = None
    rows: list[RealizedPnlRow] = []
