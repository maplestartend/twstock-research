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
from app.scoring.version import current_engine_version

logger = logging.getLogger(__name__)

# 跨 request 共用的鎖。實際重算（snapshot_today）一次只跑一個：拿到鎖後 double-check
# is_stale，被別人重算過就直接返回，避免重複工作（single-flight）。
_refresh_lock = threading.Lock()
# 背景重算的「進行中」旗標：避免每個併發 request 都各自 spawn 一條 thread。
_refresh_in_progress = threading.Event()
# 守護「要不要 spawn 背景 thread」的決策，讓最多只有一條被啟動。
_spawn_lock = threading.Lock()


def refresh_in_progress() -> bool:
    """是否有背景重算正在進行（給 freshness 指示器顯示「重算中」用）。"""
    return _refresh_in_progress.is_set()


def _latest_price_date(db: Database) -> str | None:
    with db.connect() as conn:
        row = conn.execute("SELECT MAX(date) AS m FROM daily_price").fetchone()
    return row["m"] if row and row["m"] else None


def _latest_snapshot_date(db: Database) -> str | None:
    with db.connect() as conn:
        row = conn.execute("SELECT MAX(as_of) AS m FROM signal_history").fetchone()
    return row["m"] if row and row["m"] else None


def _latest_snapshot_engine_version(db: Database) -> str | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT engine_version FROM signal_history "
            "ORDER BY as_of DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    v = row["engine_version"]
    return str(v) if v else None


# snapshot 計分時實際讀的核心 dataset。任一張比 daily_price 落後 → 早盤跑 snapshot 會
# 用「昨日 OHLCV + 前日法人/融資」算出誤導性的籌碼分數。daily_price 只代表盤後第一波
# 抓到，要等四張表同步才算 stable。
_SCORING_DATASETS: tuple[str, ...] = (
    "daily_price",
    "institutional",
    "margin",
    "per_pbr",
)


def _dataset_dates_with_conn(conn) -> dict[str, str | None]:
    union_parts = [f"SELECT '{table}' AS t, MAX(date) AS m FROM {table}" for table in _SCORING_DATASETS]
    rows = conn.execute(" UNION ALL ".join(union_parts)).fetchall()
    out: dict[str, str | None] = {t: None for t in _SCORING_DATASETS}
    for r in rows:
        out[r["t"]] = r["m"] if r and r["m"] else None
    return out


def latest_dataset_dates(db: Database) -> dict[str, str | None]:
    """每張 scoring 用 dataset 的最新一筆日期，給 UI dq 頁與診斷用。"""
    with db.connect() as conn:
        return _dataset_dates_with_conn(conn)


def all_datasets_synced(db: Database) -> bool:
    """四張 scoring dataset 是否同步到同一個交易日。給 dq / freshness 頁顯示用。
    缺其中一張（例：per_pbr 還沒抓）視為「沒同步」。"""
    dates = list(latest_dataset_dates(db).values())
    if any(d is None for d in dates):
        return False
    return max(dates) == min(dates)  # type: ignore[type-var]


def freshness_status(db: Database) -> dict[str, object]:
    """回傳 snapshot 新鮮度完整狀態，供 API/前端顯示明確 stale 原因。"""
    with db.connect() as conn:
        dataset_dates = _dataset_dates_with_conn(conn)
        snap_row = conn.execute(
            "SELECT as_of, engine_version FROM signal_history ORDER BY as_of DESC LIMIT 1"
        ).fetchone()

    price = dataset_dates.get("daily_price")
    snap = snap_row["as_of"] if snap_row and snap_row["as_of"] else None
    snap_engine = str(snap_row["engine_version"]) if snap_row and snap_row["engine_version"] else None
    current_engine = current_engine_version()
    values = list(dataset_dates.values())
    datasets_synced = bool(values) and all(v is not None for v in values) and (max(values) == min(values))
    engine_match = bool(snap_engine) and (snap_engine == current_engine)

    stale_reason = "up_to_date"
    is_stale_now = False
    can_refresh = False

    if not price:
        stale_reason = "no_price_data"
    elif snap and snap >= price and not engine_match:
        stale_reason = "engine_version_mismatch"
        is_stale_now = True
        can_refresh = True
    elif snap and snap >= price:
        stale_reason = "up_to_date"
    elif not datasets_synced:
        stale_reason = "waiting_for_dataset_sync"
    else:
        is_stale_now = True
        can_refresh = True
        stale_reason = "snapshot_missing" if snap is None else "snapshot_behind"

    return {
        "snapshot_as_of": snap,
        "daily_price_as_of": price,
        "is_stale": is_stale_now,
        "datasets_synced": datasets_synced,
        "dataset_dates": dataset_dates,
        "stale_reason": stale_reason,
        "can_refresh": can_refresh,
        "engine_version_snapshot": snap_engine,
        "engine_version_current": current_engine,
        "engine_version_match": engine_match,
    }


def is_stale(db: Database) -> bool:
    """snapshot 落後 daily_price → True，且僅在四張核心表同步時才會觸發重算。

    早盤盤後資料分批進 DB（OHLCV 先到、法人/融資/per_pbr 後到）。若只看 daily_price 就重算 snapshot，
    會用「今日 OHLCV + 昨日法人」混雜算分；改成「四張同步」才允許重算，未同步時暫時用舊 snapshot。
    """
    return bool(freshness_status(db)["is_stale"])


def _do_refresh(db: Database) -> int | None:
    """實際重算：single-flight（_refresh_lock）+ 拿到鎖後 double-check。
    回傳寫入筆數；若拿到鎖時已不 stale（被別人跑完）回 None。"""
    with _refresh_lock:
        if not is_stale(db):
            return None
        n = snapshot_today(db)
        logger.info("snapshot_freshness: 重算完成，寫入 %d 筆", n)
        return n


def _background_refresh(db: Database) -> None:
    """背景 thread entry：跑 _do_refresh，無論成敗都清掉 in-progress 旗標。"""
    try:
        _do_refresh(db)
    except Exception:
        logger.exception("snapshot_freshness: 背景重算失敗，將沿用舊 snapshot")
    finally:
        _refresh_in_progress.clear()


def ensure_fresh(db: Database, *, blocking: bool = False) -> bool:
    """檢查 snapshot 新鮮度；過舊時觸發重算。回傳是否「同步」完成了一次重算。

    呼叫成本：一次 freshness query（< 1ms）。預設 **不阻塞** request：
    - 有舊 snapshot 可服務時 → 把重算丟到背景 daemon thread（single-flight），request 立即
      用現有（略舊）snapshot 回應。UI 的新鮮度指示器（/api/system/snapshot-status 的 is_stale）
      會顯示需重算 + 提供手動觸發。這移除了「資料更新後第一個訪客被卡 20-60 秒」的尾延遲。
    - 完全沒有任何 snapshot（首次啟動，沒有舊資料可服務）→ 仍**同步阻塞**跑一次，避免列表全空。
    - blocking=True：強制同步（保留給「要跑完才回」的呼叫者；目前 daily-update / restart.bat 走
      snapshot_today，不經此路徑）。

    失敗只記 log、不擲出 — 列表頁仍可用舊 snapshot 顯示。
    重算與個股詳情頁即時計分的對齊改由「背景跑完後」達成；要立刻對齊請用 restart.bat 或
    Topbar 的手動重算（兩者都走同步 snapshot_today）。
    """
    status = freshness_status(db)
    if not status["is_stale"]:
        return False

    # 沒有舊 snapshot 可服務（首次）或呼叫者要求同步 → 阻塞重算
    if blocking or status["snapshot_as_of"] is None:
        try:
            return _do_refresh(db) is not None
        except Exception:
            logger.exception("snapshot_freshness: 同步重算失敗，將沿用舊 snapshot")
            return False

    # 有舊 snapshot → 背景單飛重算，立即用舊 snapshot 回應
    with _spawn_lock:
        if _refresh_in_progress.is_set():
            return False
        _refresh_in_progress.set()
    try:
        threading.Thread(
            target=_background_refresh, args=(db,), name="snapshot-refresh", daemon=True
        ).start()
    except Exception:
        # thread 啟動失敗（極罕見）→ 清旗標，下個 request 可再試
        _refresh_in_progress.clear()
        logger.exception("snapshot_freshness: 背景重算 thread 啟動失敗")
    return False
