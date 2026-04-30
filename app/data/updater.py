"""協調 FinMind 抓取與 SQLite 寫入的業務邏輯層。"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from app.data.db import Database
from app.data.fetcher import FinMindError, FinMindFetcher, today_str

logger = logging.getLogger(__name__)

INSTITUTIONAL_NAME_MAP = {
    "Foreign_Investor": "foreign_net",
    "Foreign_Dealer_Self": "foreign_net",
    "Investment_Trust": "investment_trust_net",
    "Dealer_self": "dealer_net",
    "Dealer_Hedging": "dealer_net",
    "Dealer": "dealer_net",
}


def _finmind_quarter_publish_date(quarter_end: object) -> str | None:
    """單季 FinMind row 的法定公告下限：Q1=05-15、Q2=08-14、Q3=11-14、Q4=次年 03-31。
    quarter_end 可能是 pandas Timestamp / datetime.date / 字串 'YYYY-MM-DD'。
    """
    if quarter_end is None:
        return None
    s = str(quarter_end)[:10]
    if len(s) < 10:
        return None
    year = s[:4]
    md = s[5:10]
    if md == "03-31":
        return f"{year}-05-15"
    if md == "06-30":
        return f"{year}-08-14"
    if md == "09-30":
        return f"{year}-11-14"
    if md == "12-31":
        try:
            return f"{int(year) + 1}-03-31"
        except ValueError:
            return None
    return None


class DataUpdater:
    def __init__(self, fetcher: FinMindFetcher, db: Database, default_start: str):
        self.fetcher = fetcher
        self.db = db
        self.default_start = default_start

    def _resolve_start(self, stock_id: str, dataset: str) -> str:
        last = self.db.get_last_fetch_date(stock_id, dataset)
        if not last:
            return self.default_start
        next_day = (pd.to_datetime(last) + timedelta(days=1)).date().isoformat()
        return next_day

    # ---------- 股票清單 ----------
    def update_stock_info(self) -> int:
        df = self.fetcher.stock_info()
        if df.empty:
            return 0
        keep = df[["stock_id", "stock_name", "industry_category", "type"]].drop_duplicates("stock_id")
        return self.db.upsert_df(keep, "stock_info")

    # ---------- 日線 ----------
    def update_daily_price(self, stock_id: str) -> int:
        start = self._resolve_start(stock_id, "daily_price")
        end = today_str()
        if start > end:
            return 0
        df = self.fetcher.daily_price(stock_id, start, end)
        if df.empty:
            return 0
        cols = ["date", "stock_id", "open", "high", "low", "close", "volume", "amount", "turnover", "spread"]
        df = df[[c for c in cols if c in df.columns]]
        n = self.db.upsert_df(df, "daily_price")
        last_date = df["date"].max().strftime("%Y-%m-%d")
        self.db.set_last_fetch_date(stock_id, "daily_price", last_date)
        return n

    # ---------- 三大法人 ----------
    def update_institutional(self, stock_id: str) -> int:
        start = self._resolve_start(stock_id, "institutional")
        end = today_str()
        if start > end:
            return 0
        df = self.fetcher.institutional(stock_id, start, end)
        if df.empty:
            return 0
        # 把 FinMind 的欄位整併為三欄：foreign_net / investment_trust_net / dealer_net
        value_cols = [c for c in df.columns if c not in ("date", "stock_id")]
        out = pd.DataFrame({"date": df["date"], "stock_id": df["stock_id"]})
        out["foreign_net"] = 0.0
        out["investment_trust_net"] = 0.0
        out["dealer_net"] = 0.0
        for col in value_cols:
            target = INSTITUTIONAL_NAME_MAP.get(col)
            if target:
                out[target] = out[target].add(df[col].fillna(0), fill_value=0)
        n = self.db.upsert_df(out, "institutional")
        last_date = out["date"].max().strftime("%Y-%m-%d")
        self.db.set_last_fetch_date(stock_id, "institutional", last_date)
        return n

    # ---------- 融資融券 ----------
    def update_margin(self, stock_id: str) -> int:
        start = self._resolve_start(stock_id, "margin")
        end = today_str()
        if start > end:
            return 0
        df = self.fetcher.margin(stock_id, start, end)
        if df.empty:
            return 0
        rename = {
            "MarginPurchaseTodayBalance": "margin_balance",
            "MarginPurchaseBuy": "margin_buy",
            "MarginPurchaseSell": "margin_sell",
            "ShortSaleTodayBalance": "short_balance",
            "ShortSaleBuy": "short_buy",
            "ShortSaleSell": "short_sell",
        }
        df = df.rename(columns=rename)
        if "margin_buy" in df.columns and "margin_sell" in df.columns:
            df["margin_change"] = df["margin_buy"] - df["margin_sell"]
        else:
            df["margin_change"] = None
        if "short_buy" in df.columns and "short_sell" in df.columns:
            df["short_change"] = df["short_buy"] - df["short_sell"]
        else:
            df["short_change"] = None
        keep = ["date", "stock_id", "margin_balance", "margin_change", "short_balance", "short_change"]
        df = df[[c for c in keep if c in df.columns]]
        n = self.db.upsert_df(df, "margin")
        last_date = df["date"].max().strftime("%Y-%m-%d")
        self.db.set_last_fetch_date(stock_id, "margin", last_date)
        return n

    # ---------- 本益比 / 殖利率 ----------
    def update_per_pbr(self, stock_id: str) -> int:
        start = self._resolve_start(stock_id, "per_pbr")
        end = today_str()
        if start > end:
            return 0
        df = self.fetcher.per_pbr(stock_id, start, end)
        if df.empty:
            return 0
        df = df.rename(columns={"PER": "per", "PBR": "pbr"})
        keep = ["date", "stock_id", "per", "pbr", "dividend_yield"]
        df = df[[c for c in keep if c in df.columns]]
        n = self.db.upsert_df(df, "per_pbr")
        last_date = df["date"].max().strftime("%Y-%m-%d")
        self.db.set_last_fetch_date(stock_id, "per_pbr", last_date)
        return n

    # ---------- 財報 ----------
    def update_financials(self, stock_id: str) -> int:
        start = self._resolve_start(stock_id, "financials")
        end = today_str()
        if start > end:
            return 0
        df = self.fetcher.financial_statements(stock_id, start, end)
        if df.empty:
            return 0
        df = df.rename(columns={"origin_name": "origin_name"})
        df["publish_date"] = df["date"].map(_finmind_quarter_publish_date)
        keep = ["date", "stock_id", "type", "value", "origin_name", "publish_date"]
        df = df[[c for c in keep if c in df.columns]]
        n = self.db.upsert_df(df, "financials")
        last_date = df["date"].max().strftime("%Y-%m-%d")
        self.db.set_last_fetch_date(stock_id, "financials", last_date)
        return n

    # ---------- 一鍵更新單檔 ----------
    def update_stock_all(self, stock_id: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for name, fn in [
            ("daily_price", self.update_daily_price),
            ("institutional", self.update_institutional),
            ("margin", self.update_margin),
            ("per_pbr", self.update_per_pbr),
            ("financials", self.update_financials),
        ]:
            try:
                result[name] = fn(stock_id)
            except FinMindError as e:
                logger.warning("%s %s failed: %s", stock_id, name, e)
                result[name] = -1
        return result
