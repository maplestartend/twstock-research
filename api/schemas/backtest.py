"""策略回測相關 DTO。"""
from __future__ import annotations

from api.schemas.common import CamelModel, StockRef, StockRefOptional


class BacktestConfig(CamelModel):
    entry_threshold: float = 65.0
    exit_threshold: float = 40.0
    stop_loss_pct: float = 0.08
    take_profit_pct: float = 0.20
    max_hold_days: int = 60
    slippage_bps: float = 5.0
    fee_rate: float | None = None   # None = 依 config.yaml 的券商折扣
    tax_rate: float = 0.003
    lookback_days: int = 500
    use_adj: bool = True


class BacktestRequest(CamelModel):
    stock_id: str
    config: BacktestConfig | None = None


class BacktestTrade(CamelModel):
    entry_date: str
    exit_date: str
    hold_days: int
    entry_price: float
    exit_price: float
    gross_return: float
    net_return: float
    exit_reason: str        # "stop_loss" | "take_profit" | "score_exit" | "max_hold"


class BacktestDailyPoint(CamelModel):
    date: str
    close: float | None = None
    short_score: float | None = None


class BacktestSummary(StockRefOptional):
    n_trades: int = 0
    win_rate: float = 0.0
    avg_return: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = 0.0
    buy_and_hold: float = 0.0          # 已扣同等費用：fee × 2 + 證交稅 + 雙向滑價
    alpha: float = 0.0                 # = total_return - buy_and_hold（兩邊同成本基準）
    sharpe: float | None = None        # per-trade mean / std；n<2 或 std=0 → None
    sortino: float | None = None       # per-trade mean / 下行 std
    calmar: float | None = None        # total_return / |max_drawdown|；MDD<1% → None


class BacktestResponse(CamelModel):
    summary: BacktestSummary
    trades: list[BacktestTrade] = []
    daily_series: list[BacktestDailyPoint] = []
    # 回傳套用的 config（把 None 都填成實際值，方便前端顯示）
    config: BacktestConfig


# ===== Portfolio backtest =====
class PortfolioBacktestRequest(CamelModel):
    stock_ids: list[str]
    config: BacktestConfig | None = None


class PortfolioRow(StockRefOptional):
    n_trades: int = 0
    win_rate: float = 0.0
    avg_return: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = 0.0
    buy_and_hold: float = 0.0
    alpha: float = 0.0
    alpha_vs_0050: float | None = None
    alpha_vs_taiex: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    calmar: float | None = None


class PortfolioAggregate(CamelModel):
    n_stocks: int = 0
    n_with_trades: int = 0
    avg_strategy_return: float = 0.0
    avg_buy_and_hold: float = 0.0
    avg_alpha: float = 0.0
    overall_winrate: float = 0.0
    bm_0050: float | None = None
    bm_taiex: float | None = None


class PortfolioBacktestResponse(CamelModel):
    summary: PortfolioAggregate
    rows: list[PortfolioRow] = []
    config: BacktestConfig
    start_date: str | None = None
    end_date: str | None = None


# ===== Grid search =====
class GridSearchRequest(CamelModel):
    stock_ids: list[str]
    entry_list: list[float] = [60, 65, 70]
    exit_list: list[float] = [35, 40]
    sl_list: list[float] = [0.08, 0.10]      # 小數
    tp_list: list[float] = [0.15, 0.20]      # 小數
    max_hold_days: int = 60
    slippage_bps: float = 5.0
    lookback_days: int = 500


class GridSearchRow(CamelModel):
    entry: float
    exit: float
    sl: float
    tp: float
    avg_alpha: float = 0.0
    avg_total_return: float = 0.0
    overall_winrate: float = 0.0
    n_trades_total: int = 0


class GridSearchResponse(CamelModel):
    combos: int
    rows: list[GridSearchRow] = []
    best: GridSearchRow | None = None
    elapsed_sec: float = 0.0


# ===== Walk-forward =====
class WalkForwardRequest(CamelModel):
    stock_ids: list[str]
    entry_list: list[float] = [60, 65, 70]
    exit_list: list[float] = [35, 40]
    sl_list: list[float] = [0.08]
    tp_list: list[float] = [0.20]
    max_hold_days: int = 60
    slippage_bps: float = 5.0
    n_splits: int = 3
    train_ratio: float = 0.7


class WalkForwardSplitRow(CamelModel):
    split: int
    train_period: str
    test_period: str
    best_entry: float | None = None
    best_exit: float | None = None
    train_return: float = 0.0
    train_sharpe: float | None = None
    test_return: float = 0.0
    test_sharpe: float | None = None
    test_alpha_0050: float | None = None
    test_n_trades: int = 0


class WalkForwardResponse(CamelModel):
    splits: list[WalkForwardSplitRow] = []
    avg_train_return: float = 0.0
    avg_test_return: float = 0.0
    overfit_warning: bool = False
    note: str | None = None


# ===== Event-driven backtest (ex-dividend) =====
class EventBacktestRequest(CamelModel):
    stock_ids: list[str]
    entry_offset: int = -5     # 事件前幾個交易日進場（負）
    exit_offset: int = 10      # 事件後幾個交易日出場
    since_year: int = 2020
    min_dividend: float = 0.5  # 過濾現金股利 < N 的事件


class EventTradeRow(StockRef):
    ex_date: str
    year: str
    event_type: str = "dividend"   # 'dividend' | 'split'
    entry_date: str | None = None
    entry_price: float | None = None
    exit_date: str | None = None
    exit_price: float | None = None
    cash_dividend: float = 0.0     # 現金股利近似（每股，元）
    stock_dividend: float = 0.0    # 配股稀釋比例（純參考；adj price 已處理）
    price_return: float | None = None
    total_return: float | None = None


class StockEventStatsRow(StockRef):
    n_events: int
    win_rate: float | None = None
    avg_total_return: float | None = None
    avg_dividend_yield: float | None = None


class EventBacktestSummary(CamelModel):
    n_events: int = 0
    n_with_data: int = 0
    win_rate: float | None = None
    avg_total_return: float | None = None
    avg_price_return: float | None = None
    avg_dividend_yield: float | None = None
    median_total_return: float | None = None
    best_return: float | None = None
    worst_return: float | None = None
    total_dividend: float = 0.0


class EventBacktestResponse(CamelModel):
    summary: EventBacktestSummary
    by_stock: list[StockEventStatsRow] = []
    trades: list[EventTradeRow] = []
    config_echo: EventBacktestRequest
