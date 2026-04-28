"""TWSE / TPEX OpenAPI + MOPS 舊版備用域的月營收抓取器。

兩條路：
- 最新月全市場：走 TWSE/TPEX OpenAPI（JSON），`fetch_latest_monthly_revenue()`
- 歷史任意月全市場：走 MOPS 備用域 HTML，`fetch_monthly_revenue_by_ym(roc_year, month)`

兩者都免 token、無流量限制（合理節流即可）。
輸出 DataFrame 欄位與 monthly_revenue 表一致。
"""
from __future__ import annotations

import io
import logging
import re
import time
from datetime import date

import pandas as pd
import requests

from app.data.http_client import make_session

logger = logging.getLogger(__name__)

# 共用 retry session：MOPS / OpenAPI 都會偶發 502 / 連線重置，自動重試 3 次
_session = make_session()

TWSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"   # 上市
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"  # 上櫃

# MOPS 舊版備用域：可直接抓歷史月營收 HTML
MOPS_SII_URL = "https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_{roc_year}_{month}_0.html"  # 上市
MOPS_OTC_URL = "https://mopsov.twse.com.tw/nas/t21/otc/t21sc03_{roc_year}_{month}_0.html"  # 上櫃

_HEADERS = {"User-Agent": "Mozilla/5.0"}
_MOPS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

# OpenAPI 回傳的中文欄位名
_K_YM = "資料年月"
_K_ID = "公司代號"
_K_REV = "營業收入-當月營收"
_K_MOM = "營業收入-上月比較增減(%)"
_K_YOY = "營業收入-去年同月增減(%)"


def _parse_ym(ym: str) -> tuple[int, int]:
    """'11503' → (2026, 3)"""
    ym = str(ym).strip()
    roc = int(ym[:-2])
    m = int(ym[-2:])
    return roc + 1911, m


def _publish_date(year: int, month: int) -> str:
    """資料月份 → publish date（最遲公告日：次月 10 號）。

    台股月營收依《證交法》規定**最遲於次月 10 日**公告。原本固定停在「次月 1 日」會
    讓 backtest / radar 在每月 1~10 號之間誤把本月還沒公告的資料當成可用，造成
    look-ahead bias（金融分析師審查 Critical #2）。

    保守做法：統一停在 10 號（少數公司 5、6 號公告，仍在這之後讀也合理）。
    若要更精準可改成讀「實際公告日」，需 MOPS 另一支 endpoint，免費版未提供。
    """
    if month == 12:
        return f"{year + 1}-01-10"
    return f"{year}-{month + 1:02d}-10"


def _to_float(v, scale: float = 1.0) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v) * scale
    except (TypeError, ValueError):
        return None


def _fetch_one(url: str, timeout: int) -> list[dict]:
    r = _session.get(url, timeout=timeout, headers=_HEADERS)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError(f"非預期回傳格式: {url}")
    return data


def fetch_latest_monthly_revenue(timeout: int = 30) -> pd.DataFrame:
    """抓最新一個月 上市+上櫃 全市場月營收。"""
    rows: list[dict] = []
    for url, market in [(TWSE_URL, "twse"), (TPEX_URL, "tpex")]:
        try:
            payload = _fetch_one(url, timeout)
        except Exception as e:
            logger.warning("%s 取得失敗: %s", market, e)
            continue
        for item in payload:
            ym_raw = item.get(_K_YM)
            sid = item.get(_K_ID)
            if not ym_raw or not sid:
                continue
            try:
                y, m = _parse_ym(ym_raw)
            except Exception:
                continue
            rev_thousand = _to_float(item.get(_K_REV))
            mom = _to_float(item.get(_K_MOM))
            yoy = _to_float(item.get(_K_YOY))
            rows.append({
                "date": _publish_date(y, m),
                "stock_id": str(sid).strip(),
                "revenue": rev_thousand * 1000 if rev_thousand is not None else None,
                "revenue_month": m,
                "revenue_year": y,
                "mom_pct": mom / 100 if mom is not None else None,
                "yoy_pct": yoy / 100 if yoy is not None else None,
            })
    if not rows:
        return pd.DataFrame(columns=[
            "date", "stock_id", "revenue", "revenue_month", "revenue_year", "mom_pct", "yoy_pct",
        ])
    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["stock_id", "date"], keep="first")
    df = df.sort_values(["stock_id", "date"]).reset_index(drop=True)
    return df


# ======================================================================
# 歷史月營收（MOPS 舊版備用域 HTML）
# ======================================================================
def _parse_mops_html(html: str, year: int, month: int) -> list[dict]:
    """解析 MOPS 月營收頁面的 HTML，回傳 dict list。

    頁面由多個產業別的 table 組成，每個資料表 11 欄：
    [公司代號, 公司名稱, 當月營收, 上月營收, 去年當月營收,
     上月比較增減(%), 去年同月增減(%), 累計當月, 累計去年, 前期比較增減(%), 備註]
    """
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        return []

    rows: list[dict] = []
    # 股票代號符合 4 碼數字的才當真資料列（過濾表頭、總計行）
    sid_pattern = re.compile(r"^\d{4}[A-Z]?$")

    for tbl in tables:
        if tbl.shape[1] != 11:
            continue
        # multi-index columns 時 flatten
        tbl = tbl.copy()
        tbl.columns = [str(c[-1]) if isinstance(c, tuple) else str(c) for c in tbl.columns]
        for _, r in tbl.iterrows():
            sid = str(r.iloc[0]).strip()
            if not sid_pattern.match(sid):
                continue
            rev = _to_float(r.iloc[2])        # 當月營收（千元）
            mom = _to_float(r.iloc[5])        # 上月比較增減(%)
            yoy = _to_float(r.iloc[6])        # 去年同月增減(%)
            rows.append({
                "date": _publish_date(year, month),
                "stock_id": sid,
                "revenue": rev * 1000 if rev is not None else None,  # 轉元
                "revenue_month": month,
                "revenue_year": year,
                "mom_pct": mom / 100 if mom is not None else None,
                "yoy_pct": yoy / 100 if yoy is not None else None,
            })
    return rows


def fetch_monthly_revenue_by_ym(
    year: int,
    month: int,
    *,
    timeout: int = 30,
    delay: float = 0.5,
) -> pd.DataFrame:
    """抓指定「資料年月」的全市場月營收（上市 + 上櫃）。

    Args:
        year: 西元年（2022、2025 等）
        month: 1~12
        delay: 兩次請求間隔秒數（對 MOPS 節流保險）

    資料空、URL 404 等情形回空 DataFrame。
    """
    roc = year - 1911
    all_rows: list[dict] = []
    for tmpl, market in [(MOPS_SII_URL, "sii"), (MOPS_OTC_URL, "otc")]:
        url = tmpl.format(roc_year=roc, month=month)
        try:
            r = _session.get(url, headers=_MOPS_HEADERS, timeout=timeout)
            if r.status_code != 200:
                logger.warning("MOPS %s %d/%d: HTTP %d", market, year, month, r.status_code)
                continue
            r.encoding = "big5"  # MOPS 老頁面是 big5
            all_rows.extend(_parse_mops_html(r.text, year, month))
        except Exception as e:
            logger.warning("MOPS %s %d/%d 抓取失敗: %s", market, year, month, e)
        time.sleep(delay)

    if not all_rows:
        return pd.DataFrame(columns=[
            "date", "stock_id", "revenue", "revenue_month", "revenue_year", "mom_pct", "yoy_pct",
        ])
    df = pd.DataFrame(all_rows)
    df = df.drop_duplicates(subset=["stock_id", "date"], keep="first")
    df = df.sort_values(["stock_id", "date"]).reset_index(drop=True)
    return df


def fetch_monthly_revenue_range(
    start_ym: tuple[int, int],
    end_ym: tuple[int, int],
    *,
    delay: float = 0.5,
) -> pd.DataFrame:
    """抓指定年月範圍（含端點）所有月份的全市場月營收。

    例：fetch_monthly_revenue_range((2024, 1), (2026, 3)) → 27 個月 × 2 市場 = 54 請求
    """
    y_s, m_s = start_ym
    y_e, m_e = end_ym
    ym_list: list[tuple[int, int]] = []
    y, m = y_s, m_s
    while (y, m) <= (y_e, m_e):
        ym_list.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1

    dfs: list[pd.DataFrame] = []
    for i, (y, m) in enumerate(ym_list, 1):
        df = fetch_monthly_revenue_by_ym(y, m, delay=delay)
        logger.info("[%d/%d] %d-%02d: %d 筆", i, len(ym_list), y, m, len(df))
        if not df.empty:
            dfs.append(df)
    if not dfs:
        return pd.DataFrame(columns=[
            "date", "stock_id", "revenue", "revenue_month", "revenue_year", "mom_pct", "yoy_pct",
        ])
    return pd.concat(dfs, ignore_index=True).sort_values(["stock_id", "date"]).reset_index(drop=True)
