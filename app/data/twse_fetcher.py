"""證交所（TWSE）每日全市場 Open Data 抓取。

每個端點一次回傳當日全市場資料，免 API key。
- MI_INDEX：每日收盤行情（OHLCV，全上市）
- T86：三大法人買賣超
- MI_MARGN：融資融券餘額
- BWIBBU_d：本益比/殖利率/股價淨值比
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BASE = "https://www.twse.com.tw"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


class TwseError(RuntimeError):
    pass


def _num(v: Any) -> float | None:
    """解析 TWSE 數字字串（去千分位、處理 '-'/'--'/空字串）。"""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "--", "---", "X"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _sign(s: str) -> int:
    """從 '<p style= color:red>+</p>' 或類似字串判斷 +/-。"""
    if not s:
        return 0
    text = re.sub(r"<[^>]+>", "", str(s)).strip()
    if "-" in text:
        return -1
    if "+" in text:
        return 1
    return 0


class TwseFetcher:
    def __init__(self, request_delay: float = 1.0):
        self.request_delay = request_delay
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    # ----------------------------------------------------------------------
    def _get_json(self, url: str, params: dict[str, Any]) -> dict | None:
        resp = self.session.get(url, params=params, timeout=30)
        time.sleep(self.request_delay)
        if resp.status_code != 200:
            raise TwseError(f"HTTP {resp.status_code} {url}")
        # 非交易日會回 HTML（含警告訊息）
        if not resp.text.lstrip().startswith(("{", "[")):
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    @staticmethod
    def _find_table(j: dict, title_contains: str) -> dict | None:
        for t in j.get("tables", []) or []:
            if title_contains in (t.get("title") or ""):
                return t
        return None

    # ======================================================================
    # 1) 每日收盤行情（OHLCV）
    # ======================================================================
    def daily_ohlcv(self, date_ymd: str) -> pd.DataFrame:
        """date_ymd 格式 YYYYMMDD（西元）。回傳全上市股票當日 OHLCV。"""
        url = f"{BASE}/exchangeReport/MI_INDEX"
        j = self._get_json(url, {"response": "json", "date": date_ymd, "type": "ALLBUT0999"})
        if not j or j.get("stat") != "OK":
            return pd.DataFrame()
        table = self._find_table(j, "每日收盤行情")
        if not table:
            return pd.DataFrame()

        fields = table.get("fields", [])
        rows = table.get("data", [])
        if not rows:
            return pd.DataFrame()

        # 預期 fields：證券代號, 證券名稱, 成交股數, 成交筆數, 成交金額, 開盤價, 最高價, 最低價, 收盤價,
        # 漲跌(+/-), 漲跌價差, 最後揭示買價, ...
        def idx(name: str) -> int:
            try:
                return fields.index(name)
            except ValueError:
                return -1

        i_id, i_name = idx("證券代號"), idx("證券名稱")
        i_vol, i_turn, i_amt = idx("成交股數"), idx("成交筆數"), idx("成交金額")
        i_open, i_hi, i_lo, i_close = idx("開盤價"), idx("最高價"), idx("最低價"), idx("收盤價")
        i_sign, i_spread = idx("漲跌(+/-)"), idx("漲跌價差")

        data = []
        for r in rows:
            sid = (r[i_id] or "").strip()
            if not sid:
                continue
            sign = _sign(r[i_sign]) if i_sign >= 0 else 0
            spread = _num(r[i_spread]) if i_spread >= 0 else None
            if spread is not None:
                spread = spread * sign if sign else spread
            data.append({
                "date": date_ymd,
                "stock_id": sid,
                "stock_name": (r[i_name] or "").strip(),
                "open": _num(r[i_open]) if i_open >= 0 else None,
                "high": _num(r[i_hi]) if i_hi >= 0 else None,
                "low": _num(r[i_lo]) if i_lo >= 0 else None,
                "close": _num(r[i_close]) if i_close >= 0 else None,
                "volume": _num(r[i_vol]) if i_vol >= 0 else None,
                "amount": _num(r[i_amt]) if i_amt >= 0 else None,
                "turnover": _num(r[i_turn]) if i_turn >= 0 else None,
                "spread": spread,
            })
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        return df

    # ======================================================================
    # 1b) 價格指數（加權指數等）
    # ======================================================================
    def daily_indices(self, date_ymd: str) -> pd.DataFrame:
        """從 MI_INDEX table[0] 抽取當日價格指數（發行量加權股價指數 等）。"""
        url = f"{BASE}/exchangeReport/MI_INDEX"
        j = self._get_json(url, {"response": "json", "date": date_ymd, "type": "ALLBUT0999"})
        if not j or j.get("stat") != "OK":
            return pd.DataFrame()
        tables = j.get("tables", []) or []
        if not tables:
            return pd.DataFrame()
        table = tables[0]
        fields = table.get("fields", [])
        rows = table.get("data", [])
        if not rows:
            return pd.DataFrame()
        # fields: ['指數','收盤指數','漲跌(+/-)','漲跌點數','漲跌百分比(%)','特殊處理註記']
        data = []
        for r in rows:
            name = (r[0] or "").strip()
            if not name:
                continue
            close = _num(r[1])
            sign = _sign(r[2]) if len(r) > 2 else 0
            change = _num(r[3])
            change_pct = _num(r[4])
            if close is None:
                continue
            if change is not None and sign:
                change = change * sign
            if change_pct is not None and sign:
                change_pct = change_pct * sign
            data.append({
                "date": date_ymd,
                "index_name": name,
                "close": close,
                "change": change,
                "change_pct": change_pct,
            })
        df = pd.DataFrame(data)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        return df

    # ======================================================================
    # 2) 三大法人買賣超
    # ======================================================================
    def institutional(self, date_ymd: str) -> pd.DataFrame:
        url = f"{BASE}/fund/T86"
        j = self._get_json(url, {"response": "json", "date": date_ymd, "selectType": "ALL"})
        if not j or j.get("stat") != "OK":
            return pd.DataFrame()
        fields = j.get("fields", [])
        rows = j.get("data", [])
        if not rows:
            return pd.DataFrame()

        def idx(name: str) -> int:
            try:
                return fields.index(name)
            except ValueError:
                return -1

        # 欄位：證券代號, 證券名稱,
        # 外陸資買進股數(不含外資自營商), 外陸資賣出股數(不含外資自營商), 外陸資買賣超股數(不含外資自營商),
        # 外資自營商買進股數, 外資自營商賣出股數, 外資自營商買賣超股數,
        # 投信買進股數, 投信賣出股數, 投信買賣超股數,
        # 自營商買賣超股數, 自營商(自行買賣)買進, 自營商(自行買賣)賣出, 自營商(自行買賣)買賣超,
        # 自營商(避險)買進, 自營商(避險)賣出, 自營商(避險)買賣超,
        # 三大法人買賣超股數
        i_id = idx("證券代號")
        i_foreign = idx("外陸資買賣超股數(不含外資自營商)")
        i_fdealer = idx("外資自營商買賣超股數")
        i_trust = idx("投信買賣超股數")
        i_dealer_total = idx("自營商買賣超股數")

        data = []
        for r in rows:
            sid = (r[i_id] or "").strip()
            if not sid:
                continue
            foreign = _num(r[i_foreign]) if i_foreign >= 0 else 0
            fdealer = _num(r[i_fdealer]) if i_fdealer >= 0 else 0
            foreign_net = (foreign or 0) + (fdealer or 0)
            data.append({
                "date": date_ymd,
                "stock_id": sid,
                "foreign_net": foreign_net,
                "investment_trust_net": _num(r[i_trust]) if i_trust >= 0 else 0,
                "dealer_net": _num(r[i_dealer_total]) if i_dealer_total >= 0 else 0,
            })
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        return df

    # ======================================================================
    # 3) 融資融券
    # ======================================================================
    def margin(self, date_ymd: str) -> pd.DataFrame:
        url = f"{BASE}/exchangeReport/MI_MARGN"
        j = self._get_json(url, {"response": "json", "date": date_ymd, "selectType": "ALL"})
        if not j or j.get("stat") != "OK":
            return pd.DataFrame()
        table = self._find_table(j, "融資融券")
        if not table:
            return pd.DataFrame()
        fields = table.get("fields", [])
        rows = table.get("data", [])
        if not rows:
            return pd.DataFrame()

        # 欄位（含重複名稱，按順序對照）：
        # 代號, 名稱, [融資]買進, [融資]賣出, 現金償還, 前日餘額, 今日餘額, 次一營業日限額,
        # [融券]買進, [融券]賣出, 現券償還, [融券]前日餘額, [融券]今日餘額, 次一營業日限額, 備註
        # 位置對應：
        data = []
        for r in rows:
            sid = (r[0] or "").strip()
            if not sid:
                continue
            m_prev = _num(r[5]) if len(r) > 5 else None
            m_today = _num(r[6]) if len(r) > 6 else None
            s_prev = _num(r[11]) if len(r) > 11 else None
            s_today = _num(r[12]) if len(r) > 12 else None
            data.append({
                "date": date_ymd,
                "stock_id": sid,
                "margin_balance": m_today,
                "margin_change": (m_today - m_prev) if (m_today is not None and m_prev is not None) else None,
                "short_balance": s_today,
                "short_change": (s_today - s_prev) if (s_today is not None and s_prev is not None) else None,
            })
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        return df

    # ======================================================================
    # 5) 未來除權息行事曆 (TWT49U，只能查「今天起的區間」)
    # ======================================================================
    def upcoming_dividends(self, str_date_ymd: str, end_date_ymd: str) -> pd.DataFrame:
        """除權除息計算結果表。strDate/endDate 都必須是今天起的區間。"""
        url = f"{BASE}/exchangeReport/TWT49U"
        j = self._get_json(url, {
            "response": "json",
            "strDate": str_date_ymd,
            "endDate": end_date_ymd,
        })
        if not j or j.get("stat") != "OK":
            return pd.DataFrame()
        fields = j.get("fields", [])
        rows = j.get("data", [])
        if not rows:
            return pd.DataFrame()

        def idx(name: str) -> int:
            try:
                return fields.index(name)
            except ValueError:
                return -1

        i_date = idx("資料日期")
        i_id = idx("股票代號")
        i_name = idx("股票名稱")
        i_cum = idx("除權息前收盤價")
        i_ex = idx("除權息參考價")
        i_value = idx("權值+息值")
        i_type = idx("權/息")

        def _roc_to_ad(roc: str) -> str | None:
            """民國年字串 '115年04月24日' → '2026-04-24'。"""
            if not roc:
                return None
            import re as _re
            m = _re.match(r"(\d+)年(\d+)月(\d+)日", roc)
            if not m:
                return None
            y, mo, d = int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3))
            return f"{y:04d}-{mo:02d}-{d:02d}"

        data = []
        for r in rows:
            sid = (r[i_id] or "").strip() if i_id >= 0 else ""
            if not sid:
                continue
            data.append({
                "date": _roc_to_ad(r[i_date]) if i_date >= 0 else None,
                "stock_id": sid,
                "stock_name": (r[i_name] or "").strip() if i_name >= 0 else "",
                "cum_price": _num(r[i_cum]) if i_cum >= 0 else None,
                "ex_price": _num(r[i_ex]) if i_ex >= 0 else None,
                "dividend_value": _num(r[i_value]) if i_value >= 0 else None,
                "type": (r[i_type] or "").strip() if i_type >= 0 else "",
            })
        df = pd.DataFrame(data)
        if not df.empty:
            df = df.dropna(subset=["date"])
            df["date"] = pd.to_datetime(df["date"])
        return df

    # ======================================================================
    # 4) PER / PBR / 殖利率
    # ======================================================================
    def per_pbr(self, date_ymd: str) -> pd.DataFrame:
        url = f"{BASE}/exchangeReport/BWIBBU_d"
        j = self._get_json(url, {"response": "json", "date": date_ymd, "selectType": "ALL"})
        if not j or j.get("stat") != "OK":
            return pd.DataFrame()
        fields = j.get("fields", [])
        rows = j.get("data", [])
        if not rows:
            return pd.DataFrame()

        def idx(name: str) -> int:
            try:
                return fields.index(name)
            except ValueError:
                return -1

        i_id = idx("證券代號")
        i_yld = idx("殖利率(%)")
        i_per = idx("本益比")
        i_pbr = idx("股價淨值比")

        data = []
        for r in rows:
            sid = (r[i_id] or "").strip()
            if not sid:
                continue
            data.append({
                "date": date_ymd,
                "stock_id": sid,
                "per": _num(r[i_per]) if i_per >= 0 else None,
                "pbr": _num(r[i_pbr]) if i_pbr >= 0 else None,
                "dividend_yield": _num(r[i_yld]) if i_yld >= 0 else None,
            })
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        return df
