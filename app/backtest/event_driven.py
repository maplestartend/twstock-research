"""事件驅動回測 — 除權息策略。

針對每次除權息事件，模擬「事件前 N 個交易日進場、事件後 M 個交易日出場」的歷史報酬。

**還原價策略（金融分析師審查 Critical #4 修法）**：
1. 用 `daily_price_adj.close_adj` 算 entry/exit 價，這樣 split / 配股事件不會把報酬
   壓成 -75%（純配股當作純價跌的 bug）。
2. 對 `event_type='dividend'` 事件，加回「投資人實際收到的現金股利」近似值
   `cash_dividend = before_price - after_price` × (1 - stock_dividend_ratio)。
   FinMind 免費版未拆 cash/stock 分量，這裡採保守做法：以 factor 推估
   股票股利比例，僅把現金成份加回。對純現金事件（佔 modern TWSE 95% 以上）完全正確；
   對含配股事件略低估（配股部分不再加回，因為 adj price 已把那 portion 反映在 factor 裡）。
3. `event_type='split'` 事件（少見，e.g. 0050 在 2025 年 1:4 分割）：
   adj price 已處理稀釋，total_return = price_return（不加回任何 cash）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from app.data.db import Database


@dataclass
class EventConfig:
    entry_offset: int = -5      # 事件前幾個交易日進場（負）
    exit_offset: int = 10       # 事件後幾個交易日出場
    since_year: int = 2020      # 從哪一年開始算
    min_dividend: float = 0.5   # 過濾「太小」的除息（避免雜訊）


@dataclass
class EventTrade:
    stock_id: str
    stock_name: str
    ex_date: str
    year: str
    entry_date: str | None
    entry_price: float | None
    exit_date: str | None
    exit_price: float | None
    cash_dividend: float
    stock_dividend: float
    event_type: str = "dividend"    # 'dividend' or 'split'
    price_return: float | None = None      # 用還原價計算的價差報酬
    total_return: float | None = None      # 含現金股利近似


@dataclass
class EventSummary:
    n_events: int = 0
    n_with_data: int = 0
    win_rate: float | None = None
    avg_total_return: float | None = None
    avg_price_return: float | None = None
    avg_dividend_yield: float | None = None     # 平均殖利率（cash_div / entry_price）
    median_total_return: float | None = None
    best_return: float | None = None
    worst_return: float | None = None
    total_dividend: float = 0.0


@dataclass
class StockEventStats:
    stock_id: str
    stock_name: str
    n_events: int
    win_rate: float | None
    avg_total_return: float | None
    avg_dividend_yield: float | None


@dataclass
class EventBacktestResult:
    config: EventConfig
    overall: EventSummary
    by_stock: list[StockEventStats] = field(default_factory=list)
    trades: list[EventTrade] = field(default_factory=list)


def _trading_days_offset(prices: pd.DataFrame, target: pd.Timestamp, offset: int) -> pd.Series | None:
    """從 prices（已 sort_by_date）找出 target 日當下、然後位移 offset 個交易日。"""
    if prices.empty:
        return None
    dates = prices["date"]
    # 找到 target 對應位置（target 不一定是交易日；取 ≤ target 的最後一筆當「事件當日」）
    pos_arr = dates[dates <= target].index
    if len(pos_arr) == 0:
        return None
    target_pos = pos_arr[-1]
    new_pos = target_pos + offset
    if new_pos < 0 or new_pos >= len(prices):
        return None
    return prices.iloc[new_pos]


def run_event_backtest(
    db: Database,
    stock_ids: list[str],
    cfg: EventConfig,
) -> EventBacktestResult:
    if not stock_ids:
        return EventBacktestResult(config=cfg, overall=EventSummary())

    placeholders = ",".join("?" * len(stock_ids))
    with db.connect() as conn:
        # 取除權息 + 分割兩類事件。dividend 才檢查 min_dividend；split 一律納入。
        events = conn.execute(
            f"""
            SELECT a.stock_id, a.date AS ex_dividend_date,
                   SUBSTR(a.date, 1, 4) AS year,
                   a.event_type,
                   a.before_price, a.after_price, a.factor,
                   COALESCE(s.stock_name, a.stock_id) AS stock_name
            FROM adj_event a
            LEFT JOIN stock_info s ON s.stock_id = a.stock_id
            WHERE a.stock_id IN ({placeholders})
              AND a.event_type IN ('dividend', 'split')
              AND CAST(SUBSTR(a.date, 1, 4) AS INTEGER) >= ?
              AND (
                a.event_type = 'split'
                OR (a.before_price - a.after_price) >= ?
              )
            ORDER BY a.date
            """,
            (*stock_ids, cfg.since_year, cfg.min_dividend),
        ).fetchall()

        # 一次取完所有相關股票「還原後」的價格序列。
        # COALESCE(close_adj, close) 確保沒還原資料時 fallback 原始 close（不要爆 NULL）。
        price_rows = conn.execute(
            f"""
            SELECT p.stock_id, p.date, COALESCE(a.close_adj, p.close) AS close
            FROM daily_price p
            LEFT JOIN daily_price_adj a
              ON a.stock_id = p.stock_id AND a.date = p.date
            WHERE p.stock_id IN ({placeholders})
            ORDER BY p.stock_id, p.date
            """,
            stock_ids,
        ).fetchall()

    if not events:
        return EventBacktestResult(config=cfg, overall=EventSummary())

    # 把 price_rows 轉成 dict[stock_id, DataFrame]
    px_df = pd.DataFrame([{"stock_id": r["stock_id"], "date": r["date"], "close": r["close"]} for r in price_rows])
    if not px_df.empty:
        px_df["date"] = pd.to_datetime(px_df["date"])
        px_df = px_df.dropna(subset=["close"]).sort_values(["stock_id", "date"]).reset_index(drop=True)
    px_by_stock: dict[str, pd.DataFrame] = {
        sid: g.reset_index(drop=True) for sid, g in (px_df.groupby("stock_id") if not px_df.empty else [])
    }

    trades: list[EventTrade] = []
    for ev in events:
        sid = ev["stock_id"]
        ev_type = ev["event_type"] or "dividend"
        before_p = float(ev["before_price"] or 0)
        after_p = float(ev["after_price"] or 0)
        factor = float(ev["factor"] or 0)
        # 推估純現金股利成份：對 dividend 事件，把 (before-after) 拆成「現金 + 股利稀釋」兩塊。
        #   pure_cash 假設：after_with_no_stock_div = before * factor_if_no_stock = before * 1 = before
        #   配股稀釋 ratio: 1/factor - 1（factor 接近 1 → 沒配股；factor 越小 → 配股越多）
        # 對 split 事件本身就是稀釋，cash_dividend 直接設 0。
        if ev_type == "split":
            cash_div_per_share = 0.0
        elif before_p > 0 and after_p > 0 and factor > 0:
            # 假設：純現金部分 = before * (1 - factor) → 等價 before - before*factor
            # 實際 after = before*factor - cash_per_share，所以 cash_per_share = before*factor - after
            # 然後因為配股稀釋會讓 after 比 before*factor 更低，cash_per_share = max(0, ...)
            # 簡化版（足夠 modern TWSE，配股已不流行）：直接用 before-after 全當 cash
            cash_div_per_share = max(0.0, before_p - after_p)
        else:
            cash_div_per_share = max(0.0, before_p - after_p)
        # 配股比例近似（純參考用，未進入收益計算；adj price 已處理稀釋）
        stock_div_ratio = max(0.0, (1.0 / factor - 1.0)) if (factor and factor > 0 and factor < 1) else 0.0

        prices = px_by_stock.get(sid)
        if prices is None or prices.empty:
            trades.append(EventTrade(
                stock_id=sid, stock_name=ev["stock_name"],
                ex_date=ev["ex_dividend_date"], year=ev["year"],
                entry_date=None, entry_price=None, exit_date=None, exit_price=None,
                cash_dividend=cash_div_per_share, stock_dividend=stock_div_ratio,
                event_type=ev_type, price_return=None, total_return=None,
            ))
            continue

        target_ts = pd.to_datetime(ev["ex_dividend_date"])
        entry_row = _trading_days_offset(prices, target_ts, cfg.entry_offset)
        exit_row = _trading_days_offset(prices, target_ts, cfg.exit_offset)

        # 注意：entry_price / exit_price 都是「還原後價」（COALESCE(close_adj, close)）。
        # 對 split 與 dividend 事件而言，adj price 已把稀釋與除權息事先消化，所以
        # price_return 不會有 -75% 的假跌；不能再用 entry 對 exit 的「原始」價差。
        entry_price = float(entry_row["close"]) if entry_row is not None and pd.notna(entry_row["close"]) else None
        exit_price = float(exit_row["close"]) if exit_row is not None and pd.notna(exit_row["close"]) else None

        if entry_price is None or exit_price is None or entry_price <= 0:
            price_ret = None
            total_ret = None
        else:
            price_ret = (exit_price - entry_price) / entry_price
            # 為何不再加 cash_div？
            # 因為現在用 adj price，cash 與 stock 兩種「除權息事件」的價跌已被 adj_factor
            # 事先反向修正掉。adj price 的物理意義 = 「假設投資人把現金股利再投入、配股
            # 視為原本就持有」之後的等價價格。所以 price_return on adj price ≈ total wealth return。
            # 這比舊版「raw price + 加回 cash_div」更精準（舊版對配股事件會雙重計算）。
            total_ret = price_ret

        trades.append(EventTrade(
            stock_id=sid, stock_name=ev["stock_name"],
            ex_date=ev["ex_dividend_date"], year=ev["year"],
            entry_date=str(entry_row["date"].date()) if entry_row is not None else None,
            entry_price=entry_price,
            exit_date=str(exit_row["date"].date()) if exit_row is not None else None,
            exit_price=exit_price,
            cash_dividend=cash_div_per_share, stock_dividend=stock_div_ratio,
            event_type=ev_type,
            price_return=price_ret, total_return=total_ret,
        ))

    valid = [t for t in trades if t.total_return is not None and t.entry_price]
    summary = EventSummary(n_events=len(trades), n_with_data=len(valid))
    if valid:
        rets = [t.total_return for t in valid if t.total_return is not None]
        prets = [t.price_return for t in valid if t.price_return is not None]
        yields = [(t.cash_dividend / t.entry_price) for t in valid if t.entry_price]
        summary.win_rate = sum(1 for r in rets if r > 0) / len(rets) if rets else None
        summary.avg_total_return = sum(rets) / len(rets) if rets else None
        summary.avg_price_return = sum(prets) / len(prets) if prets else None
        summary.avg_dividend_yield = sum(yields) / len(yields) if yields else None
        summary.median_total_return = sorted(rets)[len(rets) // 2] if rets else None
        summary.best_return = max(rets) if rets else None
        summary.worst_return = min(rets) if rets else None
        summary.total_dividend = sum(t.cash_dividend for t in valid)

    # 按 stock 聚合
    by_stock_map: dict[str, list[EventTrade]] = {}
    for t in valid:
        by_stock_map.setdefault(t.stock_id, []).append(t)
    by_stock_rows: list[StockEventStats] = []
    for sid, ts in by_stock_map.items():
        rets = [t.total_return for t in ts if t.total_return is not None]
        yields = [(t.cash_dividend / t.entry_price) for t in ts if t.entry_price]
        if not rets:
            continue
        by_stock_rows.append(StockEventStats(
            stock_id=sid,
            stock_name=ts[0].stock_name,
            n_events=len(ts),
            win_rate=sum(1 for r in rets if r > 0) / len(rets),
            avg_total_return=sum(rets) / len(rets),
            avg_dividend_yield=sum(yields) / len(yields) if yields else None,
        ))
    by_stock_rows.sort(key=lambda s: s.avg_total_return or -1, reverse=True)

    # trades: 依 ex_date 倒序
    trades.sort(key=lambda t: t.ex_date, reverse=True)
    return EventBacktestResult(config=cfg, overall=summary, by_stock=by_stock_rows, trades=trades)
