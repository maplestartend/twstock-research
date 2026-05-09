"""加權指數 regime 偵測 — v5e #1（2026-05-09）。

動機：v5c IC 在 240 天樣本顯示 long 反向（-0.035），但 986 天跨 regime 仍正向
（+0.039）— 證明 long 因子的有效性與大盤 regime 強相關：
- 多頭 regime（強勢市場）：動能股壓過價值/品質股 → long sub-factor 反向
- 空頭/盤整 regime：價值/品質回防、成長動能熄火 → long sub-factor 正向

設計：偵測當前 regime → 動態調整 COMPOSITE_WEIGHTS 中 short/mid/long 比例。
- 多頭：保留 v5c 默認 0.20/0.60/0.20（mid 動能主導）
- 空頭：拉高 long 至 0.35（價值/品質保護），mid 降至 0.45
- 盤整：折衷 0.20/0.55/0.25

Regime 訊號用加權指數 vs MA200 / MA50 slope（穩定、不易 whipsaw）。

歷史 IC backtest 必要：v5e 用 backfill_signal_history --clear 重跑 + IC diagnostics
比較動態 vs 固定權重，差異不顯著就 rollback。
"""
from __future__ import annotations

from datetime import date
from typing import Literal

import pandas as pd

Regime = Literal["bull", "bear", "neutral"]


def detect_regime(db, as_of: str | date | None = None) -> Regime:
    """偵測當前加權指數 regime。

    回 "bull" / "bear" / "neutral"。資料不足（< 200 個交易日）→ "neutral"。

    規則：
    - 加權指數 close > MA200 + MA50 slope > 0 → bull
    - 加權指數 close < MA200 + MA50 slope < 0 → bear
    - 其他（過渡期、ma 接近 close）→ neutral
    """
    if as_of is None:
        as_of_str = None
    elif isinstance(as_of, str):
        as_of_str = as_of
    else:
        as_of_str = as_of.isoformat()

    with db.connect() as conn:
        sql = "SELECT date, close FROM index_daily WHERE index_name='發行量加權股價指數'"
        params: tuple = ()
        if as_of_str:
            sql += " AND date <= ?"
            params = (as_of_str,)
        sql += " ORDER BY date"
        df = pd.read_sql_query(sql, conn, params=params)

    if df.empty or len(df) < 200:
        return "neutral"

    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    df["ma50"] = df["close"].rolling(50).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    last = df.iloc[-1]
    if pd.isna(last["ma200"]) or pd.isna(last["ma50"]):
        return "neutral"

    # MA50 5 日 slope（變化率）
    if len(df) < 5:
        return "neutral"
    ma50_5d_ago = df["ma50"].iloc[-5]
    if pd.isna(ma50_5d_ago) or ma50_5d_ago == 0:
        return "neutral"
    slope = (last["ma50"] - ma50_5d_ago) / ma50_5d_ago

    close = last["close"]
    ma200 = last["ma200"]

    # 多頭：close 站上 ma200 + ma50 上揚 + close 跟 ma200 距離夠（>+3%）
    if close > ma200 * 1.03 and slope > 0.001:
        return "bull"
    # 空頭：close 跌破 ma200 + ma50 下降
    if close < ma200 * 0.97 and slope < -0.001:
        return "bear"
    return "neutral"


# 三套 COMPOSITE_WEIGHTS（regime-aware）
COMPOSITE_WEIGHTS_BY_REGIME: dict[Regime, dict[str, float]] = {
    "bull":    {"short": 0.20, "mid": 0.60, "long": 0.20},  # v5c 默認、mid 動能主導
    "bear":    {"short": 0.20, "mid": 0.45, "long": 0.35},  # long 升、避動能假高
    "neutral": {"short": 0.20, "mid": 0.55, "long": 0.25},  # 折衷
}


def composite_weights_for_regime(regime: Regime) -> dict[str, float]:
    """回對應 regime 的 COMPOSITE_WEIGHTS。"""
    return COMPOSITE_WEIGHTS_BY_REGIME.get(regime, COMPOSITE_WEIGHTS_BY_REGIME["bull"])


__all__ = ["Regime", "detect_regime", "composite_weights_for_regime", "COMPOSITE_WEIGHTS_BY_REGIME"]
