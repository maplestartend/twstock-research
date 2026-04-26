"""Router 層共用工具：數值轉換 / 日期格式化 / 批次 SQL helper / 股票名稱查詢。

把分散在各 router 的小工具集中，避免 5 份雷同的 _sf / _safe_float / _name_map。
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from app.data.db import Database


def safe_float(v: Any) -> float | None:
    """轉成有限 float；None / NaN / Inf / 不可轉 → None。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def safe_float_or_zero(v: Any) -> float:
    """safe_float 的「有預設值」版本，給「必填數字」型欄位用。"""
    f = safe_float(v)
    return 0.0 if f is None else f


def fmt_date(v: Any) -> str:
    """把 str / pd.Timestamp / datetime 統一成 YYYY-MM-DD；None → 空字串。"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v[:10]
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    try:
        return pd.to_datetime(v).strftime("%Y-%m-%d")
    except Exception:
        return str(v)[:10]


def make_placeholders(n: int) -> str:
    """產生 "?,?,?,..." 給 SQL `IN (...)`。n=0 回傳空字串。"""
    return ",".join("?" * n)


def get_stock_name(db: Database, sid: str) -> str:
    """單檔股票名稱查詢；找不到 → 回 sid 自身（避免 None 灑到 UI）。"""
    with db.connect() as conn:
        r = conn.execute(
            "SELECT stock_name FROM stock_info WHERE stock_id=?", (sid,)
        ).fetchone()
    return r["stock_name"] if r and r["stock_name"] else sid


def get_stock_names(db: Database, sids: list[str]) -> dict[str, str]:
    """批次股票名稱查詢；缺名稱者用 sid 自身代替。空 list → 空 dict。"""
    if not sids:
        return {}
    ph = make_placeholders(len(sids))
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT stock_id, stock_name FROM stock_info WHERE stock_id IN ({ph})",
            sids,
        ).fetchall()
    return {r["stock_id"]: (r["stock_name"] or r["stock_id"]) for r in rows}
