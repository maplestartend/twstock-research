"""籌碼面指標：三大法人買賣超、融資融券動能。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def consecutive_days(series: pd.Series, sign: int) -> int:
    """從最新一天往回算，連續同方向天數（sign=1 連買 / sign=-1 連賣）。"""
    if series.empty:
        return 0
    count = 0
    for val in reversed(series.tolist()):
        if pd.isna(val):
            break
        if (sign > 0 and val > 0) or (sign < 0 and val < 0):
            count += 1
        else:
            break
    return count


def enrich_institutional(inst: pd.DataFrame) -> pd.DataFrame:
    """在 institutional DataFrame 上加入滾動累計等衍生欄位。"""
    inst = inst.sort_values("date").copy()
    for col in ("foreign_net", "investment_trust_net", "dealer_net"):
        if col not in inst.columns:
            inst[col] = 0.0
    inst["inst_total_net"] = inst["foreign_net"] + inst["investment_trust_net"] + inst["dealer_net"]
    inst["foreign_cum5"] = inst["foreign_net"].rolling(5, min_periods=1).sum()
    inst["foreign_cum20"] = inst["foreign_net"].rolling(20, min_periods=1).sum()
    inst["trust_cum5"] = inst["investment_trust_net"].rolling(5, min_periods=1).sum()
    inst["trust_cum20"] = inst["investment_trust_net"].rolling(20, min_periods=1).sum()
    return inst


def enrich_margin(margin: pd.DataFrame) -> pd.DataFrame:
    """融資變化率、券資比。"""
    margin = margin.sort_values("date").copy()
    # 融資餘額 5 日變化率（過熱 > 10% 要留意）
    margin["margin_balance_chg5"] = margin["margin_balance"].pct_change(5)
    margin["margin_balance_chg20"] = margin["margin_balance"].pct_change(20)
    # 券資比
    denom = margin["margin_balance"].replace(0, np.nan)
    margin["short_margin_ratio"] = margin["short_balance"] / denom
    return margin


def latest_chip_snapshot(inst: pd.DataFrame, margin: pd.DataFrame) -> dict:
    """回傳最新一天的關鍵籌碼面指標（給評分引擎使用）。"""
    snap: dict = {}

    if not inst.empty:
        inst = enrich_institutional(inst)
        last = inst.iloc[-1]
        snap["foreign_net"] = float(last.get("foreign_net", 0) or 0)
        snap["foreign_cum5"] = float(last.get("foreign_cum5", 0) or 0)
        snap["foreign_cum20"] = float(last.get("foreign_cum20", 0) or 0)
        snap["trust_net"] = float(last.get("investment_trust_net", 0) or 0)
        snap["trust_cum5"] = float(last.get("trust_cum5", 0) or 0)
        snap["trust_cum20"] = float(last.get("trust_cum20", 0) or 0)
        snap["inst_total_net"] = float(last.get("inst_total_net", 0) or 0)
        # 連買/連賣天數
        snap["foreign_streak_buy"] = consecutive_days(inst["foreign_net"].tail(30), 1)
        snap["foreign_streak_sell"] = consecutive_days(inst["foreign_net"].tail(30), -1)
        snap["trust_streak_buy"] = consecutive_days(inst["investment_trust_net"].tail(30), 1)
        snap["trust_streak_sell"] = consecutive_days(inst["investment_trust_net"].tail(30), -1)

    if not margin.empty:
        margin = enrich_margin(margin)
        last = margin.iloc[-1]
        snap["margin_balance"] = float(last.get("margin_balance", 0) or 0)
        snap["margin_chg5"] = float(last.get("margin_balance_chg5", 0) or 0)
        snap["margin_chg20"] = float(last.get("margin_balance_chg20", 0) or 0)
        snap["short_margin_ratio"] = float(last.get("short_margin_ratio", 0) or 0)

    return snap
