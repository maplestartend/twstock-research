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


# snapshot 計分時實際讀的核心 dataset。任一張比 daily_price 落後 → 早盤跑 snapshot 會
# 用「昨日 OHLCV + 前日法人/融資」算出誤導性的籌碼分數。daily_price 只代表盤後第一波
# 抓到，要等四張表同步才算 stable。
_SCORING_DATASETS: tuple[str, ...] = (
    "daily_price",
    "institutional",
    "margin",
    "per_pbr",
)


def latest_dataset_dates(db: Database) -> dict[str, str | None]:
    """每張 scoring 用 dataset 的最新一筆日期，給 UI dq 頁與診斷用。"""
    out: dict[str, str | None] = {}
    with db.connect() as conn:
        for table in _SCORING_DATASETS:
            row = conn.execute(f"SELECT MAX(date) AS m FROM {table}").fetchone()
            out[table] = row["m"] if row and row["m"] else None
    return out


def all_datasets_synced(db: Database) -> bool:
    """四張 scoring dataset 是否同步到同一個交易日。給 dq / freshness 頁顯示用。
    缺其中一張（例：per_pbr 還沒抓）視為「沒同步」。"""
    dates = list(latest_dataset_dates(db).values())
    if any(d is None for d in dates):
        return False
    return max(dates) == min(dates)  # type: ignore[type-var]


def is_stale(db: Database) -> bool:
    """snapshot 落後 daily_price → True，且僅在四張核心表同步時才會觸發重算。

    早盤盤後資料分批進 DB（OHLCV 先到、法人/融資/per_pbr 後到）。若只看 daily_price 就重算 snapshot，
    會用「今日 OHLCV + 昨日法人」混雜算分；改成「四張同步」才允許重算，未同步時暫時用舊 snapshot。
    """
    price = _latest_price_date(db)
    if not price:
        return False
    snap = _latest_snapshot_date(db)
    if snap and snap >= price:
        return False
    # 落後了，但只有四張表都同步才能放心重算（否則重算結果反而錯）
    return all_datasets_synced(db)


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
