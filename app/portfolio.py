"""庫存股（持股）與交易紀錄管理。

holdings 表：目前持倉（每檔一列）
trade_log 表：每筆買/賣的紀錄（append-only，不刪改）

買賣發生時呼叫 `record_trade()`，會同時寫 trade_log 並更新 holdings。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd

from app.config import Config
from app.data.db import Database

BUY = "BUY"
SELL = "SELL"

# 台股公定費率
STANDARD_FEE_RATE = 0.001425   # 手續費（單邊，未打折）
DEFAULT_TAX_RATE = 0.003       # 證交稅（一般股；僅賣方，不受折扣影響）
ETF_TAX_RATE = 0.001           # 股票型 ETF 證交稅（千分之一）
BOND_ETF_TAX_RATE = 0.0        # 債券 ETF 免證交稅


def tax_rate_for(
    stock_id: str | None,
    *,
    industry_category: str | None = None,
) -> float:
    """依代號回傳賣方證交稅率：股票型 ETF=0.1%，債券 ETF=0%，其他 0.3%。

    判斷規則（依優先序）：
    1. 若 caller 傳入 industry_category（從 stock_info join 出來），且字串包含 "ETF" → 視為 ETF。
       這是最可靠的來源（TWSE / TPEX 官方分類），避免代號正則漏判（如新發行 ETF 代號改成 010xxx 系列）。
    2. fallback 代號規則：以 "00" 開頭、長度 ≥ 4 → ETF。對所有現存上市/上櫃 ETF (00xx, 006xxx, 00xxxL/R, 00xxxB)
       都正確；CLAUDE.md 慣例。
    3. 在 ETF 內部，代號結尾 B（00679B、00772B、00937B 等）→ 債券 ETF 0%，其他股票型 ETF 0.1%。
    """
    if not stock_id:
        return DEFAULT_TAX_RATE
    sid = str(stock_id).strip().upper()

    is_etf_by_category = bool(industry_category and "ETF" in industry_category.upper())
    is_etf_by_code = sid.startswith("00") and len(sid) >= 4
    is_etf = is_etf_by_category or is_etf_by_code

    if is_etf:
        return BOND_ETF_TAX_RATE if sid.endswith("B") else ETF_TAX_RATE
    return DEFAULT_TAX_RATE


def _broker_cfg():
    """延遲讀取避免循環 import，方便單元測試 mock。"""
    return Config.load().broker


def effective_fee(shares: float, price: float) -> float:
    """依 config 的折扣 + 最低手續費計算實際手續費。"""
    b = _broker_cfg()
    raw = shares * price * STANDARD_FEE_RATE * b.fee_discount
    return max(raw, b.fee_min)


@dataclass
class Holding:
    stock_id: str
    shares: float
    avg_cost: float
    entry_date: str | None
    note: str | None

    def market_value(self, price: float) -> float:
        return self.shares * price

    def cost_basis(self) -> float:
        return self.shares * self.avg_cost

    def unrealized_pnl(self, price: float) -> float:
        """毛損益 = (現價 − 均成本) × 股數。注意 avg_cost 已含買進手續費。"""
        return (price - self.avg_cost) * self.shares

    def estimated_sell_costs(self, price: float) -> float:
        """估算「現在賣出要扣的成本」= 賣出手續費 + 證交稅。
        手續費含 broker 折扣與最低收費；稅率依 stock_id 決定（一般 0.3%、股票 ETF 0.1%、債券 ETF 0%）。"""
        sell_amount = self.shares * price
        fee = effective_fee(self.shares, price)
        tax = sell_amount * tax_rate_for(self.stock_id)
        return fee + tax

    def net_unrealized_pnl(self, price: float) -> float:
        """淨損益 = 毛損益 − 預估賣出成本（手續費 + 證交稅）。
        這比毛損益更貼近「現在賣掉真的拿回多少」。"""
        return self.unrealized_pnl(price) - self.estimated_sell_costs(price)

    def unrealized_pnl_pct(self, price: float) -> float:
        """毛損益百分比（不扣賣出稅）。"""
        if self.avg_cost <= 0:
            return 0.0
        return (price - self.avg_cost) / self.avg_cost

    def net_unrealized_pnl_pct(self, price: float) -> float:
        """淨損益百分比（已扣預估賣出稅+手續費）。"""
        cost = self.cost_basis()
        if cost <= 0:
            return 0.0
        return self.net_unrealized_pnl(price) / cost


# ======================================================================
# 持股 CRUD
# ======================================================================
def list_holdings(db: Database) -> list[Holding]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT stock_id, shares, avg_cost, entry_date, note FROM holdings WHERE shares > 0 ORDER BY stock_id"
        ).fetchall()
    return [Holding(
        stock_id=r["stock_id"], shares=float(r["shares"]),
        avg_cost=float(r["avg_cost"]), entry_date=r["entry_date"], note=r["note"],
    ) for r in rows]


def get_holding(db: Database, stock_id: str) -> Holding | None:
    with db.connect() as conn:
        r = conn.execute(
            "SELECT stock_id, shares, avg_cost, entry_date, note FROM holdings WHERE stock_id=?",
            (stock_id,),
        ).fetchone()
    if not r or float(r["shares"]) <= 0:
        return None
    return Holding(
        stock_id=r["stock_id"], shares=float(r["shares"]),
        avg_cost=float(r["avg_cost"]), entry_date=r["entry_date"], note=r["note"],
    )


# ======================================================================
# 交易紀錄
# ======================================================================
def record_trade(
    db: Database,
    trade_date: str | date,
    stock_id: str,
    action: Literal["BUY", "SELL"],
    shares: float,
    price: float,
    fee: float | None = None,
    tax: float | None = None,
    note: str | None = None,
) -> int:
    """寫入一筆交易，並更新 holdings 的 shares / avg_cost。"""
    action = action.upper()
    if action not in (BUY, SELL):
        raise ValueError(f"action must be BUY or SELL, got {action}")
    if shares <= 0 or price <= 0:
        raise ValueError("shares 與 price 必須 > 0")

    if fee is None:
        fee = round(effective_fee(shares, price))
    if tax is None:
        tax = round(shares * price * tax_rate_for(stock_id)) if action == SELL else 0

    td = trade_date.isoformat() if isinstance(trade_date, date) else str(trade_date)

    with db.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO trade_log (trade_date, stock_id, action, shares, price, fee, tax, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (td, stock_id, action, float(shares), float(price), float(fee), float(tax), note),
        )
        trade_id = cur.lastrowid

        # 更新 holdings
        row = conn.execute(
            "SELECT shares, avg_cost FROM holdings WHERE stock_id=?", (stock_id,)
        ).fetchone()
        cur_shares = float(row["shares"]) if row else 0.0
        cur_cost = float(row["avg_cost"]) if row else 0.0

        if action == BUY:
            new_shares = cur_shares + shares
            # 平均成本：把手續費也計入
            gross_cost = cur_cost * cur_shares + shares * price + fee
            new_avg = gross_cost / new_shares if new_shares > 0 else 0
            conn.execute(
                """
                INSERT INTO holdings (stock_id, shares, avg_cost, entry_date)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(stock_id) DO UPDATE SET
                    shares = excluded.shares,
                    avg_cost = excluded.avg_cost,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (stock_id, new_shares, new_avg, td if not row else None),
            )
        else:  # SELL
            new_shares = max(cur_shares - shares, 0)
            if new_shares == 0:
                # 全部賣光 → 直接刪掉 holdings row（不留 0-share 殭屍，Critical #8）
                conn.execute("DELETE FROM holdings WHERE stock_id = ?", (stock_id,))
            else:
                # 賣出不改 avg_cost
                conn.execute(
                    """
                    UPDATE holdings SET shares = ?, avg_cost = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE stock_id = ?
                    """,
                    (new_shares, cur_cost, stock_id),
                )
        conn.commit()
    return trade_id


def delete_trade(db: Database, trade_id: int) -> None:
    """刪除一筆交易並重建該股 holdings（從 trade_log 重算）。"""
    with db.connect() as conn:
        row = conn.execute("SELECT stock_id FROM trade_log WHERE id = ?", (trade_id,)).fetchone()
        if not row:
            return
        sid = row["stock_id"]
        conn.execute("DELETE FROM trade_log WHERE id = ?", (trade_id,))
        conn.commit()
    rebuild_holding(db, sid)


def rebuild_holding(db: Database, stock_id: str) -> None:
    """從 trade_log 重算 holdings 的 shares 與 avg_cost。

    全部賣光（shares==0）→ 直接從 holdings 表刪掉，不再留 0-share row（金融分析師審查
    Critical #8）。原版會留 0-share row，雖然 list_holdings 用 `WHERE shares > 0` 過濾掉，
    但 holdings 表會逐筆累積殭屍紀錄；長期持倉系統建議刪除乾淨。
    """
    with db.connect() as conn:
        trades = conn.execute(
            "SELECT action, shares, price, fee, tax FROM trade_log WHERE stock_id=? ORDER BY trade_date, id",
            (stock_id,),
        ).fetchall()

        shares = 0.0
        avg_cost = 0.0
        for t in trades:
            act, s, p = t["action"], float(t["shares"]), float(t["price"])
            fee = float(t["fee"] or 0)
            if act == BUY:
                new_shares = shares + s
                avg_cost = ((avg_cost * shares) + (s * p) + fee) / new_shares if new_shares > 0 else 0
                shares = new_shares
            else:
                shares = max(shares - s, 0)
                if shares == 0:
                    avg_cost = 0

        if shares == 0:
            # 全部賣光 / 沒有任何 trade → 刪掉 row 而不是留 0-share 殭屍
            conn.execute("DELETE FROM holdings WHERE stock_id=?", (stock_id,))
        else:
            conn.execute(
                """
                INSERT INTO holdings (stock_id, shares, avg_cost)
                VALUES (?, ?, ?)
                ON CONFLICT(stock_id) DO UPDATE SET
                    shares = excluded.shares,
                    avg_cost = excluded.avg_cost,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (stock_id, shares, avg_cost),
            )
        conn.commit()


def load_trades(db: Database, stock_id: str | None = None) -> pd.DataFrame:
    query = "SELECT * FROM trade_log"
    params: list = []
    if stock_id:
        query += " WHERE stock_id=?"
        params.append(stock_id)
    query += " ORDER BY trade_date DESC, id DESC"
    with db.connect() as conn:
        return pd.read_sql_query(query, conn, params=params)


# ======================================================================
# 已實現損益計算（從 trade_log 依 FIFO 配對）
# ======================================================================
def realized_pnl(db: Database, stock_id: str | None = None) -> pd.DataFrame:
    """用 FIFO 把買賣配對，回傳已實現損益明細。"""
    query = "SELECT id, trade_date, stock_id, action, shares, price, fee, tax FROM trade_log"
    params: list = []
    if stock_id:
        query += " WHERE stock_id=?"
        params.append(stock_id)
    query += " ORDER BY stock_id, trade_date, id"
    with db.connect() as conn:
        trades = pd.read_sql_query(query, conn, params=params)
    if trades.empty:
        return pd.DataFrame()

    out_rows = []
    for sid, group in trades.groupby("stock_id"):
        buy_queue: list[dict] = []  # [{shares, price, fee_per_share}]
        for _, t in group.iterrows():
            if t["action"] == BUY:
                buy_queue.append({
                    "shares": float(t["shares"]),
                    "price": float(t["price"]),
                    "fee_per_share": float(t["fee"]) / float(t["shares"]) if t["shares"] else 0,
                    "trade_date": t["trade_date"],
                })
            else:
                remain = float(t["shares"])
                sell_price = float(t["price"])
                sell_fee = float(t["fee"] or 0) + float(t["tax"] or 0)
                sell_fee_per = sell_fee / float(t["shares"]) if t["shares"] else 0
                while remain > 0 and buy_queue:
                    head = buy_queue[0]
                    use = min(remain, head["shares"])
                    cost = (head["price"] + head["fee_per_share"]) * use
                    proceed = (sell_price - sell_fee_per) * use
                    out_rows.append({
                        "stock_id": sid,
                        "buy_date": head["trade_date"],
                        "sell_date": t["trade_date"],
                        "shares": use,
                        "buy_price": head["price"],
                        "sell_price": sell_price,
                        "cost": cost,
                        "proceed": proceed,
                        "pnl": proceed - cost,
                        "pnl_pct": (proceed - cost) / cost if cost else 0,
                    })
                    head["shares"] -= use
                    remain -= use
                    if head["shares"] <= 0:
                        buy_queue.pop(0)
    return pd.DataFrame(out_rows)


# 風險訊號舊版 `risk_signals(db, holding, latest_close, short_score)` 已移除。
# 統一走 `app.risk.enhanced_risk_signals(db, stock_id, avg_cost, entry_date, close, short_score)`，
# 後者多支援 ATR 動態停損，與 holdings router / report 共用同一個來源。
