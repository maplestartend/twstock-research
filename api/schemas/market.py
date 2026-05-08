"""大盤 / 市場寬度相關 DTO。"""
from __future__ import annotations

from api.schemas.common import CamelModel


class MarketSnapshot(CamelModel):
    date: str | None = None
    close: float | None = None
    change_pct: float | None = None  # % 單位（非小數）


class MarketIntradayQuote(CamelModel):
    """大盤指數盤中即時值（mis.twse.com.tw）。

    - 預設抓 TWSE 加權指數（ex_ch=tse_t00.tw）
    - 盤後 / mis 異常 → 422，前端應 fallback 到 /api/market/snapshot 的收盤值
    - change_pct 為小數（0.0123 = 1.23%），與股票 `IntradayQuoteView.changePct` 對齊；
      但 `MarketSnapshot.changePct` 沿用百分比表示（為避免破壞既有前端格式器，不一併調整）
    """
    index_id: str
    name: str | None = None
    value: float
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    change_pct: float | None = None  # 小數
    quote_time: str | None = None
    is_live: bool = True
    quote_source: str = "match"


class MarketBreadth(CamelModel):
    n_total: int = 0
    n_up: int = 0
    n_down: int = 0
    n_unchanged: int = 0
    advance_decline_ratio: float | None = None
    pct_above_ma20: float | None = None
    pct_above_ma60: float | None = None
    n_new_high_50d: int = 0
    n_new_low_50d: int = 0
    new_high_low_ratio: float | None = None
    health_label: str | None = None  # e.g. "強勢", "多頭", "中性", "偏空", "弱勢"
    health_tone: str = "neutral"  # up | down | neutral | warning


class IndustryRotationRow(CamelModel):
    industry: str
    n_members: int = 0
    ret_1d: float | None = None              # 等權當日報酬（給排行表）
    ret_1d_weighted: float | None = None     # 成交值加權當日報酬（給熱力圖著色）
    ret_5d: float | None = None
    ret_20d: float | None = None
    ret_60d: float | None = None
    heat: float | None = None
    total_amount: float | None = None        # 最新交易日成交金額加總（TWD，給熱力圖磚塊面積）
    n_up: int = 0                            # 當日 ret_1d > 0 的成員家數
    n_flat: int = 0                          # 當日 ret_1d == 0 的成員家數
    n_down: int = 0                          # 當日 ret_1d < 0 的成員家數


class IndustryRotationResponse(CamelModel):
    """`/api/market/industry-rotation` 的回傳殼，多帶一個資料截止日期。"""
    as_of: str | None = None     # YYYY-MM-DD，daily_price 全表最新日期
    rows: list[IndustryRotationRow] = []


class IndustryMemberRow(CamelModel):
    stock_id: str
    stock_name: str
    close: float | None = None
    ret_1d: float | None = None
    ret_5d: float | None = None
    ret_20d: float | None = None
