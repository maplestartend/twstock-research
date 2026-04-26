"""櫃買中心（TPEx / 上櫃）每日全市場 Open Data 抓取。"""
from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd
import requests

from app.data.twse_fetcher import _num  # 共用數字解析

logger = logging.getLogger(__name__)

BASE = "https://www.tpex.org.tw"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


class TpexError(RuntimeError):
    pass


def _ad_to_date_str(date_ymd: str) -> str:
    """YYYYMMDD → YYYY/MM/DD。TPEx 新版 API 接受西元年。"""
    return f"{date_ymd[:4]}/{date_ymd[4:6]}/{date_ymd[6:8]}"


class TpexFetcher:
    def __init__(self, request_delay: float = 1.0):
        self.request_delay = request_delay
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def _get_json(self, path: str, params: dict[str, Any]) -> dict | None:
        url = f"{BASE}{path}"
        resp = self.session.get(url, params=params, timeout=30)
        time.sleep(self.request_delay)
        if resp.status_code != 200:
            raise TpexError(f"HTTP {resp.status_code} {url}")
        if not resp.text.lstrip().startswith(("{", "[")):
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    @staticmethod
    def _first_table(j: dict) -> dict | None:
        tables = j.get("tables") or []
        for t in tables:
            if t.get("data"):
                return t
        return None

    # ======================================================================
    # 1) 每日收盤行情
    # ======================================================================
    def daily_ohlcv(self, date_ymd: str) -> pd.DataFrame:
        j = self._get_json(
            "/www/zh-tw/afterTrading/dailyQuotes",
            {"date": _ad_to_date_str(date_ymd), "type": "EW", "response": "json"},
        )
        if not j or j.get("stat") not in ("OK", "ok"):
            return pd.DataFrame()
        t = self._first_table(j)
        if not t:
            return pd.DataFrame()

        # 欄位：[0]代號 [1]名稱 [2]收盤 [3]漲跌 [4]開盤 [5]最高 [6]最低 [7]均價
        # [8]成交股數 [9]成交金額 [10]成交筆數
        data = []
        for r in t["data"]:
            sid = (r[0] or "").strip()
            if not sid:
                continue
            data.append({
                "date": date_ymd,
                "stock_id": sid,
                "stock_name": (r[1] or "").strip(),
                "open": _num(r[4]),
                "high": _num(r[5]),
                "low": _num(r[6]),
                "close": _num(r[2]),
                "volume": _num(r[8]),
                "amount": _num(r[9]),
                "turnover": _num(r[10]),
                "spread": _num(r[3]),
            })
        df = pd.DataFrame(data)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        return df

    # ======================================================================
    # 2) 三大法人
    # ======================================================================
    def institutional(self, date_ymd: str) -> pd.DataFrame:
        j = self._get_json(
            "/www/zh-tw/insti/dailyTrade",
            {"date": _ad_to_date_str(date_ymd), "type": "Daily", "sect": "EW", "response": "json"},
        )
        if not j or j.get("stat") not in ("OK", "ok"):
            return pd.DataFrame()
        t = self._first_table(j)
        if not t:
            return pd.DataFrame()

        # 24 欄：[0]代號 [1]名稱
        # [2-4]外資 買/賣/淨   [5-7]外資自營商   [8-10]外資合計
        # [11-13]投信   [14-16]自營商自行  [17-19]自營商避險   [20-22]自營商合計
        # [23]三大法人合計
        data = []
        for r in t["data"]:
            sid = (r[0] or "").strip()
            if not sid:
                continue
            data.append({
                "date": date_ymd,
                "stock_id": sid,
                "foreign_net": _num(r[10]) or 0,
                "investment_trust_net": _num(r[13]) or 0,
                "dealer_net": _num(r[22]) or 0,
            })
        df = pd.DataFrame(data)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        return df

    # ======================================================================
    # 3) 融資融券
    # ======================================================================
    def margin(self, date_ymd: str) -> pd.DataFrame:
        j = self._get_json(
            "/www/zh-tw/margin/balance",
            {"date": _ad_to_date_str(date_ymd), "response": "json"},
        )
        if not j or j.get("stat") not in ("OK", "ok"):
            return pd.DataFrame()
        t = self._first_table(j)
        if not t:
            return pd.DataFrame()

        # 20 欄：[2]前資餘額 [6]資餘額   [10]前券餘額 [14]券餘額
        data = []
        for r in t["data"]:
            sid = (r[0] or "").strip()
            if not sid:
                continue
            m_prev, m_today = _num(r[2]), _num(r[6])
            s_prev, s_today = _num(r[10]), _num(r[14])
            data.append({
                "date": date_ymd,
                "stock_id": sid,
                "margin_balance": m_today,
                "margin_change": (m_today - m_prev) if (m_today is not None and m_prev is not None) else None,
                "short_balance": s_today,
                "short_change": (s_today - s_prev) if (s_today is not None and s_prev is not None) else None,
            })
        df = pd.DataFrame(data)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        return df

    # ======================================================================
    # 4) PER / PBR / 殖利率
    # ======================================================================
    def per_pbr(self, date_ymd: str) -> pd.DataFrame:
        j = self._get_json(
            "/www/zh-tw/afterTrading/peQryDate",
            {"date": _ad_to_date_str(date_ymd), "response": "json"},
        )
        if not j or j.get("stat") not in ("OK", "ok"):
            return pd.DataFrame()
        t = self._first_table(j)
        if not t:
            return pd.DataFrame()

        # 欄位：[0]代號 [1]名稱 [2]本益比 [3]每股股利 [4]股利年度 [5]殖利率% [6]股價淨值比 [7]財報年/季
        data = []
        for r in t["data"]:
            sid = (r[0] or "").strip()
            if not sid:
                continue
            data.append({
                "date": date_ymd,
                "stock_id": sid,
                "per": _num(r[2]),
                "pbr": _num(r[6]),
                "dividend_yield": _num(r[5]),
            })
        df = pd.DataFrame(data)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        return df
