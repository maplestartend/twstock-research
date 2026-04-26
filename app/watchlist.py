"""自選股（watchlist.yaml）的讀寫與增刪。"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.config import PROJECT_ROOT

WATCHLIST_PATH = PROJECT_ROOT / "watchlist.yaml"


def load() -> dict[str, str]:
    if not WATCHLIST_PATH.exists():
        return {}
    with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return {str(k): str(v) for k, v in (raw.get("stocks") or {}).items()}


def save(stocks: dict[str, str]) -> None:
    """保持代號字串格式（避免 YAML 把 "2330" 存成 int）。"""
    # 按代號字典序排序
    ordered = dict(sorted(stocks.items(), key=lambda x: x[0]))
    payload = {"stocks": ordered}
    tmp = WATCHLIST_PATH.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        # default_flow_style=False + 引號保留代號字串
        yaml.safe_dump(
            payload, f, allow_unicode=True, default_flow_style=False, sort_keys=False
        )
    tmp.replace(WATCHLIST_PATH)


def add(stock_id: str, stock_name: str = "") -> bool:
    """新增一檔。已存在則不動，回傳 False。"""
    stocks = load()
    sid = str(stock_id).strip()
    if not sid:
        return False
    if sid in stocks:
        return False
    stocks[sid] = stock_name or sid
    save(stocks)
    return True


def add_many(items: dict[str, str]) -> int:
    """批次新增，回傳實際新增（已存在的不算）筆數。"""
    stocks = load()
    added = 0
    for sid, name in items.items():
        sid = str(sid).strip()
        if not sid or sid in stocks:
            continue
        stocks[sid] = name or sid
        added += 1
    if added:
        save(stocks)
    return added


def remove(stock_id: str) -> bool:
    stocks = load()
    sid = str(stock_id).strip()
    if sid not in stocks:
        return False
    del stocks[sid]
    save(stocks)
    return True


def remove_many(stock_ids: list[str]) -> int:
    stocks = load()
    removed = 0
    for sid in stock_ids:
        sid = str(sid).strip()
        if sid in stocks:
            del stocks[sid]
            removed += 1
    if removed:
        save(stocks)
    return removed


def contains(stock_id: str) -> bool:
    return str(stock_id).strip() in load()
