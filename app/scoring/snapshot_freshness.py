"""signal_history 新鮮度檢查與自動補跑。

設計動機：
雷達 / 自選 / 持股 列表頁讀 signal_history 最新一筆當「當下分數」（避免每次即時跑
score_all 太慢），但 daily_price 可能比 snapshot 新（晚抓的盤後資料、隔夜跑了
market_update 但忘了 snapshot 等）。本模組在列表 API 開頭呼叫 ensure_fresh()，若
snapshot.as_of 落後於 daily_price.MAX(date) 就阻塞重跑一次，跑完所有讀者都拿到
新分數，與個股詳情頁的即時計算對齊。

回測用途（history 頁、分數走勢折線圖）讀的是「歷史快照」，不受影響。
"""
from __future__ import annotations

import logging
import threading

from app.data.db import Database
from app.scoring.history import snapshot_today

logger = logging.getLogger(__name__)

# 跨 request 共用的鎖。第一個發現過舊的 request 會阻塞重跑，其他併發的 request 會等
# 同一把鎖；double-check 後若已被別人重算過就直接返回，避免重複工作。
_refresh_lock = threading.Lock()


def _latest_price_date(db: Database) -> str | None:
    with db.connect() as conn:
        row = conn.execute("SELECT MAX(date) AS m FROM daily_price").fetchone()
    return row["m"] if row and row["m"] else None


def _latest_snapshot_date(db: Database) -> str | None:
    with db.connect() as conn:
        row = conn.execute("SELECT MAX(as_of) AS m FROM signal_history").fetchone()
    return row["m"] if row and row["m"] else None


def is_stale(db: Database) -> bool:
    """snapshot 落後 daily_price → True；snapshot 不存在但有價格 → True。"""
    price = _latest_price_date(db)
    if not price:
        return False
    snap = _latest_snapshot_date(db)
    if not snap:
        return True
    return snap < price


def ensure_fresh(db: Database) -> bool:
    """檢查並（若有需要）阻塞重跑 snapshot。回傳是否實際觸發了重算。

    呼叫成本：一次 SQL MAX query（< 5ms）。實際重算只在過舊時發生。
    失敗（例如重算過程例外）只記 log，不擲出 — 列表頁仍可用舊 snapshot 顯示。
    """
    if not is_stale(db):
        return False
    with _refresh_lock:
        # 等到鎖時可能已被別人跑完，再 check 一次避免重做
        if not is_stale(db):
            return False
        try:
            n = snapshot_today(db)
            logger.info("snapshot_freshness: 自動重算完成，寫入 %d 筆", n)
            return True
        except Exception:
            logger.exception("snapshot_freshness: 自動重算失敗，將沿用舊 snapshot")
            return False
