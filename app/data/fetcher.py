from __future__ import annotations

import logging
import time
from datetime import date, datetime

import pandas as pd
import requests

from app.config import FinMindConfig
from app.data.http_client import make_session

logger = logging.getLogger(__name__)


class FinMindError(RuntimeError):
    pass


class FinMindFetcher:
    """FinMind API v4 wrapper. 每個 method 回傳已轉型的 pandas DataFrame。"""

    def __init__(self, config: FinMindConfig, request_delay: float = 0.5):
        self.config = config
        self.request_delay = request_delay
        # 帶 Retry 的 session：FinMind 偶發 502 / quota 暫時逾時可自動重試
        self.session = make_session()

    def _get(self, dataset: str, data_id: str | None, start: str, end: str | None = None) -> pd.DataFrame:
        params: dict[str, str] = {
            "dataset": dataset,
            "start_date": start,
            "token": self.config.token,
        }
        if data_id:
            params["data_id"] = data_id
        if end:
            params["end_date"] = end

        url = f"{self.config.base_url}/data"
        try:
            resp = self.session.get(url, params=params, timeout=10)
        except requests.RequestException as e:
            # Timeout / ConnectionError 等都包成 FinMindError，讓 caller 統一用一個 except 接。
            raise FinMindError(f"{dataset}/{data_id}: {type(e).__name__}: {e}") from e
        time.sleep(self.request_delay)

        if resp.status_code != 200:
            raise FinMindError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        payload = resp.json()
        if payload.get("status") != 200:
            raise FinMindError(f"{dataset}/{data_id}: {payload.get('msg')}")

        data = payload.get("data", [])
        if not data:
            return pd.DataFrame()
        return pd.DataFrame(data)

    # ---------- 股票清單 ----------
    def stock_info(self) -> pd.DataFrame:
        """全市場股票基本資訊（含 ETF）。"""
        df = self._get("TaiwanStockInfo", data_id=None, start="2020-01-01")
        return df

    # ---------- 日線價量 ----------
    def daily_price(self, stock_id: str, start: str, end: str | None = None) -> pd.DataFrame:
        df = self._get("TaiwanStockPrice", data_id=stock_id, start=start, end=end)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        numeric_cols = ["open", "max", "min", "close", "spread", "Trading_Volume", "Trading_money", "Trading_turnover"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.rename(columns={
            "max": "high",
            "min": "low",
            "Trading_Volume": "volume",
            "Trading_money": "amount",
            "Trading_turnover": "turnover",
        })
        return df

    # ---------- 三大法人買賣超 ----------
    def institutional(self, stock_id: str, start: str, end: str | None = None) -> pd.DataFrame:
        df = self._get("TaiwanStockInstitutionalInvestorsBuySell", data_id=stock_id, start=start, end=end)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        for col in ("buy", "sell"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df["net"] = df["buy"] - df["sell"]
        # pivot 成寬表：每一天一列，三大法人各一欄
        pivot = df.pivot_table(
            index=["date", "stock_id"],
            columns="name",
            values="net",
            aggfunc="sum",
        ).reset_index()
        pivot.columns.name = None
        return pivot

    # ---------- 融資融券 ----------
    def margin(self, stock_id: str, start: str, end: str | None = None) -> pd.DataFrame:
        df = self._get("TaiwanStockMarginPurchaseShortSale", data_id=stock_id, start=start, end=end)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        return df

    # ---------- 季報（綜合損益表） ----------
    def financial_statements(self, stock_id: str, start: str, end: str | None = None) -> pd.DataFrame:
        df = self._get("TaiwanStockFinancialStatements", data_id=stock_id, start=start, end=end)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df

    # ---------- 本益比 / 殖利率 / 股價淨值比 ----------
    def per_pbr(self, stock_id: str, start: str, end: str | None = None) -> pd.DataFrame:
        df = self._get("TaiwanStockPER", data_id=stock_id, start=start, end=end)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        for col in ("PER", "PBR", "dividend_yield"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    # ---------- 全市場（bulk）----------
    def bulk_daily_price(self, date_str: str) -> pd.DataFrame:
        """單一交易日、全市場所有股票的日線。"""
        df = self._get("TaiwanStockPrice", data_id=None, start=date_str, end=date_str)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        numeric_cols = ["open", "max", "min", "close", "spread", "Trading_Volume", "Trading_money", "Trading_turnover"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.rename(columns={
            "max": "high",
            "min": "low",
            "Trading_Volume": "volume",
            "Trading_money": "amount",
            "Trading_turnover": "turnover",
        })
        return df

    def bulk_institutional(self, date_str: str) -> pd.DataFrame:
        """單一交易日、全市場三大法人買賣超。"""
        df = self._get("TaiwanStockInstitutionalInvestorsBuySell", data_id=None, start=date_str, end=date_str)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        for col in ("buy", "sell"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df["net"] = df["buy"] - df["sell"]
        pivot = df.pivot_table(
            index=["date", "stock_id"],
            columns="name",
            values="net",
            aggfunc="sum",
        ).reset_index()
        pivot.columns.name = None
        return pivot

    # ---------- 月營收 ----------
    def monthly_revenue(self, stock_id: str, start: str) -> pd.DataFrame:
        df = self._get("TaiwanStockMonthRevenue", data_id=stock_id, start=start)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        for col in ("revenue", "revenue_month", "revenue_year"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values("date").reset_index(drop=True)
        # 月增月減 / 年增年減
        df["mom_pct"] = df["revenue"].pct_change(1)
        df["yoy_pct"] = df["revenue"].pct_change(12)
        return df

    def monthly_revenue_all(self, start: str) -> pd.DataFrame:
        """一次拉全市場月營收（不帶 data_id）。省 API 額度。"""
        df = self._get("TaiwanStockMonthRevenue", data_id=None, start=start)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        for col in ("revenue", "revenue_month", "revenue_year"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values(["stock_id", "date"]).reset_index(drop=True)
        df["mom_pct"] = df.groupby("stock_id")["revenue"].pct_change(1)
        df["yoy_pct"] = df.groupby("stock_id")["revenue"].pct_change(12)
        return df

    # ---------- 除權息執行結果 ----------
    def dividend_result(self, stock_id: str, start: str) -> pd.DataFrame:
        """TaiwanStockDividendResult：每次除權息的前/後參考價。"""
        df = self._get("TaiwanStockDividendResult", data_id=stock_id, start=start)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        for col in ("before_price", "after_price", "stock_and_cache_dividend"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    # ---------- 分割事件（1:N 等）----------
    def split_events(self, stock_id: str, start: str) -> pd.DataFrame:
        """TaiwanStockSplitPrice：股票分割事件。"""
        df = self._get("TaiwanStockSplitPrice", data_id=stock_id, start=start)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        for col in ("before_price", "after_price"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    # ---------- 現金股利 ----------
    def dividend(self, stock_id: str, start: str) -> pd.DataFrame:
        df = self._get("TaiwanStockDividend", data_id=stock_id, start=start)
        if df.empty:
            return df
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        return df


def today_str() -> str:
    return date.today().isoformat()
