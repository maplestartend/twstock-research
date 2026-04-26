"""TWSE / TPEX OpenAPI + MOPS 公開觀測站 財報抓取器。

兩條路：
- **最新一季全市場**：TWSE/TPEX OpenAPI（JSON），`fetch_latest_*()` 系列
  - 綜合損益表：t187ap06_{L,X}_{basi,bd,ci,fh,ins,mim} (6 產業 × 2 市場 = 12 endpoints)
  - 資產負債表：t187ap07_{L,X}_{basi,bd,ci,fh,ins,mim} (12 endpoints)
  - 合計 24 個請求，耗時 ~5~10 秒
- **任意年季全市場（歷史回補）**：MOPS POST `ajax_t163sb04`，`fetch_history_*()` 系列
  - 只能抓綜合損益表（足以做累計差分 → 單季 → TTM）
  - 上市/上櫃 各一個 POST 即可拿到全產業，~2 秒/季
  - 用於回補過去 4~8 季以解鎖 YoY、TTM、ROE 計算

輸出寫入 `financials_cumulative` 表（與 FinMind 單季 `financials` 分開，
因為 OpenAPI / MOPS 給的是「當季累計」：Q1 = Q1 單季、Q2 = H1 累計、Q4 = 全年累計）。

下游 `fundamentals.fundamental_snapshot()` 會在 FinMind 單季無資料時 fallback 到此表，
並利用累計差分計算單季與 TTM。
"""
from __future__ import annotations

import io
import logging
import re
import time
from typing import Iterable

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# 產業分類後綴（TWSE/TPEX OpenAPI 共用）
_INDUSTRIES: tuple[str, ...] = (
    "basi",  # 證券業
    "bd",    # 證券投資信託事業（投信）
    "ci",    # 一般業（主要）
    "fh",    # 金控業
    "ins",   # 保險業
    "mim",   # 票券業
)

# 綜合損益表
_INCOME_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap06_{market}_{ind}"
# 資產負債表
_BALANCE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap07_{market}_{ind}"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; stock-tools/1.0)"}

# ----- 欄位對應 -----
# 各產業綜合損益表欄位略有差異（一般業最完整；金控 / 保險有特殊科目），
# 這裡僅對應「所有產業都應有的核心欄位」，其他產業特有欄位略過。
INCOME_FIELD_MAP: dict[str, str] = {
    # 中文欄位 -> FinMind 英文 type（與 financials 表既有命名一致）
    "營業收入": "Revenue",
    "收入": "Revenue",                          # 金控業
    "營業成本": "CostOfGoodsSold",
    "營業毛利（毛損）淨額": "GrossProfit",
    "營業毛利（毛損）": "GrossProfit",          # 若無「淨額」欄位
    "營業費用": "OperatingExpenses",
    "營業利益（損失）": "OperatingIncome",
    "營業外收入及支出": "TotalNonoperatingIncomeAndExpense",
    "稅前淨利（淨損）": "PreTaxIncome",
    "所得稅費用（利益）": "TAX",
    "繼續營業單位本期淨利（淨損）": "IncomeFromContinuingOperations",
    "本期淨利（淨損）": "IncomeAfterTaxes",
    "其他綜合損益（淨額）": "OtherComprehensiveIncome",
    "本期綜合損益總額": "TotalConsolidatedProfitForThePeriod",
    "淨利（淨損）歸屬於非控制權益": "NoncontrollingInterests",
    "基本每股盈餘（元）": "EPS",
}

BALANCE_FIELD_MAP: dict[str, str] = {
    "歸屬於母公司業主之權益合計": "EquityAttributableToOwnersOfParent",
    "權益總額": "TotalEquity",
    "資產總額": "TotalAssets",
    "負債總額": "TotalLiabilities",
    "流動資產": "CurrentAssets",
    "非流動資產": "NonCurrentAssets",
    "流動負債": "CurrentLiabilities",
    "非流動負債": "NonCurrentLiabilities",
    "每股參考淨值": "BookValuePerShare",
}


def _quarter_end(year: int, quarter: int) -> str:
    """民國年 + 季 → 西元季末日期。114/Q4 → 2025-12-31"""
    ce_year = year + 1911
    q_end = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[quarter]
    return f"{ce_year}-{q_end}"


def _to_float(v) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        # 千分位逗號處理（保險起見）
        s = str(v).replace(",", "")
        return float(s)
    except (TypeError, ValueError):
        return None


def _fetch_one(url: str, timeout: int = 30) -> list[dict]:
    r = requests.get(url, timeout=timeout, headers=_HEADERS)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError(f"非預期回傳格式: {url}")
    return data


def _to_long(
    records: Iterable[dict],
    field_map: dict[str, str],
    value_scale_thousand_to_unit: bool = True,
) -> list[dict]:
    """把 OpenAPI 的寬表 records 轉成 long-format 列表（依 field_map 過濾欄位）。

    Args:
        value_scale_thousand_to_unit: 若 True，將千元轉為元（比例 1000）。
            EPS 欄位一律不乘 1000（單位是「元/股」）。
    """
    rows: list[dict] = []
    for rec in records:
        sid = str(rec.get("公司代號", "")).strip()
        if not sid:
            continue
        try:
            year = int(rec.get("年度"))
            quarter = int(rec.get("季別"))
        except (TypeError, ValueError):
            continue
        d = _quarter_end(year, quarter)
        for zh, en in field_map.items():
            if zh not in rec:
                continue
            raw = rec.get(zh)
            val = _to_float(raw)
            if val is None:
                continue
            # EPS 是「元/股」，不用乘 1000；其餘財務金額原為千元，轉為元
            is_per_share = en in ("EPS", "BookValuePerShare")
            scale = 1.0 if (is_per_share or not value_scale_thousand_to_unit) else 1000.0
            rows.append({
                "date": d,
                "stock_id": sid,
                "type": en,
                "value": val * scale,
                "year": year + 1911,
                "quarter": quarter,
                "origin_name": zh,
            })
    return rows


def fetch_latest_income_statement(delay: float = 0.3, timeout: int = 30) -> pd.DataFrame:
    """抓「最新一季」上市+上櫃全市場綜合損益表（累計值）。"""
    all_rows: list[dict] = []
    for market in ("L", "X"):
        for ind in _INDUSTRIES:
            url = _INCOME_URL.format(market=market, ind=ind)
            try:
                records = _fetch_one(url, timeout)
            except Exception as e:
                logger.warning("income %s %s 抓取失敗: %s", market, ind, e)
                time.sleep(delay)
                continue
            all_rows.extend(_to_long(records, INCOME_FIELD_MAP))
            logger.debug("income %s %s: %d 筆 records", market, ind, len(records))
            time.sleep(delay)
    if not all_rows:
        return pd.DataFrame(columns=["date", "stock_id", "type", "value", "year", "quarter", "origin_name"])
    df = pd.DataFrame(all_rows)
    df = df.drop_duplicates(subset=["stock_id", "date", "type"], keep="first")
    return df.sort_values(["stock_id", "date", "type"]).reset_index(drop=True)


def fetch_latest_balance_sheet(delay: float = 0.3, timeout: int = 30) -> pd.DataFrame:
    """抓「最新一季」上市+上櫃全市場資產負債表（期末餘額）。"""
    all_rows: list[dict] = []
    for market in ("L", "X"):
        for ind in _INDUSTRIES:
            url = _BALANCE_URL.format(market=market, ind=ind)
            try:
                records = _fetch_one(url, timeout)
            except Exception as e:
                logger.warning("balance %s %s 抓取失敗: %s", market, ind, e)
                time.sleep(delay)
                continue
            all_rows.extend(_to_long(records, BALANCE_FIELD_MAP))
            logger.debug("balance %s %s: %d 筆 records", market, ind, len(records))
            time.sleep(delay)
    if not all_rows:
        return pd.DataFrame(columns=["date", "stock_id", "type", "value", "year", "quarter", "origin_name"])
    df = pd.DataFrame(all_rows)
    df = df.drop_duplicates(subset=["stock_id", "date", "type"], keep="first")
    return df.sort_values(["stock_id", "date", "type"]).reset_index(drop=True)


def fetch_latest_all(delay: float = 0.3, timeout: int = 30) -> pd.DataFrame:
    """抓「最新一季」綜損 + 資產負債表合併。"""
    inc = fetch_latest_income_statement(delay=delay, timeout=timeout)
    bal = fetch_latest_balance_sheet(delay=delay, timeout=timeout)
    if inc.empty and bal.empty:
        return pd.DataFrame(columns=["date", "stock_id", "type", "value", "year", "quarter", "origin_name"])
    df = pd.concat([inc, bal], ignore_index=True)
    df = df.drop_duplicates(subset=["stock_id", "date", "type"], keep="first")
    return df.sort_values(["stock_id", "date", "type"]).reset_index(drop=True)


# ======================================================================
# 歷史季財報（MOPS 公開觀測站）
# ======================================================================
_MOPS_HISTORY_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t163sb04"
_MOPS_HISTORY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0",
    "Referer": "https://mopsov.twse.com.tw/mops/web/t163sb04",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded",
}

# 第一欄為公司代號（4 碼數字 + 可選字母）
_SID_PATTERN = re.compile(r"^\d{4}[A-Z]?$")


def _fetch_history_html(market: str, roc_year: int, season: int, *, timeout: int = 30) -> str | None:
    """POST 到 MOPS 抓「指定市場 + 年 + 季」的綜合損益表 HTML。

    market: 'sii' 上市 / 'otc' 上櫃
    """
    body = (
        f"encodeURIComponent=1&step=1&firstin=1&off=1&isQuery=Y"
        f"&TYPEK={market}&year={roc_year}&season={season:02d}"
    )
    try:
        r = requests.post(_MOPS_HISTORY_URL, data=body, headers=_MOPS_HISTORY_HEADERS, timeout=timeout)
    except Exception as e:
        logger.warning("MOPS history %s %d/Q%d 連線失敗: %s", market, roc_year, season, e)
        return None
    if r.status_code != 200 or len(r.content) < 5000:
        logger.warning(
            "MOPS history %s %d/Q%d 回傳異常 (HTTP %d, %d bytes)",
            market, roc_year, season, r.status_code, len(r.content),
        )
        return None
    # MOPS ajax 端點實測為 utf-8
    r.encoding = "utf-8"
    return r.text


def _parse_history_html(
    html: str, year_ce: int, quarter: int, field_map: dict[str, str] | None = None,
) -> list[dict]:
    """把 MOPS 歷史季綜合損益表 HTML 解析成 long-format 列表。

    一頁含多個產業的 table（一般業、金控業、證券業、銀行業、保險業、其他），
    每個產業 table 欄數略異，但中文欄名共用 INCOME_FIELD_MAP 的 key。
    """
    fmap = field_map if field_map is not None else INCOME_FIELD_MAP
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        return []

    d = _quarter_end(year_ce - 1911, quarter)
    rows: list[dict] = []
    for tbl in tables:
        if tbl.shape[0] < 1 or tbl.shape[1] < 5:
            continue
        # flatten multi-index columns
        cols = [str(c[-1]) if isinstance(c, tuple) else str(c) for c in tbl.columns]
        # 標準化欄名：去空白
        cols = [re.sub(r"\s+", "", c) for c in cols]
        tbl = tbl.copy()
        tbl.columns = cols
        first_col = cols[0] if cols else ""
        # 第一欄要是「公司代號」（容許「公司 代號」「公司代碼」等變體）
        if "公司" not in first_col or "代" not in first_col:
            continue
        # 與 field_map 中文欄名取交集，至少需有 ≥ 4 個欄位才視為有效產業表
        matched = [c for c in cols if c in fmap]
        if len(matched) < 4:
            continue
        for _, r in tbl.iterrows():
            sid = str(r.iloc[0]).strip()
            if not _SID_PATTERN.match(sid):
                continue
            for zh in matched:
                val = _to_float(r.get(zh))
                if val is None:
                    continue
                en = fmap[zh]
                is_per_share = en in ("EPS", "BookValuePerShare")
                scale = 1.0 if is_per_share else 1000.0
                rows.append({
                    "date": d,
                    "stock_id": sid,
                    "type": en,
                    "value": val * scale,
                    "year": year_ce,
                    "quarter": quarter,
                    "origin_name": zh,
                })
    return rows


def fetch_history_income_statement(
    year_ce: int, quarter: int, *, delay: float = 0.5, timeout: int = 30,
) -> pd.DataFrame:
    """抓「指定西元年 + 季」上市+上櫃全市場綜合損益表（累計值）。

    Args:
        year_ce: 西元年（2024、2025 等）
        quarter: 1~4
    """
    roc_year = year_ce - 1911
    all_rows: list[dict] = []
    for market in ("sii", "otc"):
        html = _fetch_history_html(market, roc_year, quarter, timeout=timeout)
        if html:
            parsed = _parse_history_html(html, year_ce, quarter)
            all_rows.extend(parsed)
            logger.info(
                "MOPS history %s %d Q%d: %d 筆 / %d 檔",
                market, year_ce, quarter, len(parsed),
                len({r["stock_id"] for r in parsed}),
            )
        time.sleep(delay)
    if not all_rows:
        return pd.DataFrame(columns=["date", "stock_id", "type", "value", "year", "quarter", "origin_name"])
    df = pd.DataFrame(all_rows)
    df = df.drop_duplicates(subset=["stock_id", "date", "type"], keep="first")
    return df.sort_values(["stock_id", "date", "type"]).reset_index(drop=True)


def fetch_history_quarters(
    quarters: list[tuple[int, int]], *, delay: float = 0.5, timeout: int = 30,
) -> pd.DataFrame:
    """抓多個 (year_ce, quarter) 的歷史季財報。

    例：fetch_history_quarters([(2023, 1), (2023, 2), (2023, 3), (2023, 4), (2024, 1)])
    """
    dfs: list[pd.DataFrame] = []
    for i, (y, q) in enumerate(quarters, 1):
        df = fetch_history_income_statement(y, q, delay=delay, timeout=timeout)
        logger.info("[%d/%d] %d Q%d: %d 筆", i, len(quarters), y, q, len(df))
        if not df.empty:
            dfs.append(df)
    if not dfs:
        return pd.DataFrame(columns=["date", "stock_id", "type", "value", "year", "quarter", "origin_name"])
    out = pd.concat(dfs, ignore_index=True)
    out = out.drop_duplicates(subset=["stock_id", "date", "type"], keep="first")
    return out.sort_values(["stock_id", "date", "type"]).reset_index(drop=True)


# ======================================================================
# 累計值 → 單季差分（寫入 financials_quarterly_derived）
# ======================================================================
# 這些科目用「累計差分」算單季有意義（流量類）。
# 資產負債表科目（Equity/Assets/Liabilities）是期末餘額，不差分。
_DIFF_TYPES = {
    "Revenue", "CostOfGoodsSold", "GrossProfit", "OperatingExpenses",
    "OperatingIncome", "TotalNonoperatingIncomeAndExpense",
    "PreTaxIncome", "TAX", "IncomeFromContinuingOperations",
    "IncomeAfterTaxes", "OtherComprehensiveIncome",
    "TotalConsolidatedProfitForThePeriod", "NoncontrollingInterests",
    "EPS",
}


def derive_quarterly_from_cumulative(db) -> int:
    """從 `financials_cumulative` 累計值差分產生單季值，寫入 `financials_quarterly_derived`。

    差分公式：
        Q1 單季 = Q1 累計
        Qn 單季 = Qn 累計 − Q(n-1) 累計   (n=2,3,4)

    注意：
    - 一檔股票若只在 financials_cumulative 有 Q4 而無 Q1~Q3 → 該年 Q4 無法差分，跳過
    - 一檔股票只有 Q2 而無 Q1 → 跳過 Q2 差分
    - Q1 因為「累計就是單季」直接複製過去
    - 只差 _DIFF_TYPES 中的流量科目；Equity 等期末餘額不差分

    回傳寫入筆數。
    """
    import pandas as pd

    with db.connect() as conn:
        cum = pd.read_sql_query(
            "SELECT date, stock_id, type, value, year, quarter FROM financials_cumulative",
            conn,
        )
    if cum.empty:
        return 0

    # 只處理流量科目
    cum = cum[cum["type"].isin(_DIFF_TYPES)].copy()
    if cum.empty:
        return 0

    cum["year"] = cum["year"].astype(int)
    cum["quarter"] = cum["quarter"].astype(int)

    rows: list[dict] = []
    # 依 (stock_id, type, year) 分組，按季排序差分
    for (sid, t, y), g in cum.groupby(["stock_id", "type", "year"], sort=False):
        g = g.sort_values("quarter")
        # 累計值對映 quarter
        cum_by_q = dict(zip(g["quarter"], g["value"]))
        for q in (1, 2, 3, 4):
            if q not in cum_by_q:
                continue
            if q == 1:
                single = cum_by_q[1]
            else:
                prev = cum_by_q.get(q - 1)
                if prev is None:
                    continue  # 上一季缺資料，無法差分
                single = cum_by_q[q] - prev
            rows.append({
                "date": _quarter_end(y - 1911, q),
                "stock_id": sid,
                "type": t,
                "value": float(single),
                "year": int(y),
                "quarter": q,
            })

    if not rows:
        return 0
    df = pd.DataFrame(rows)
    return db.upsert_df(df, "financials_quarterly_derived")
