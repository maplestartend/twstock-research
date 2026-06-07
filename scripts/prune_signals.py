"""S2-4：signal_history + signal_history_factor_parts retention。

每天 market_update 寫入兩張歷史表 → 不清理就無限長大：
- signal_history       ~2,000 列/天
- signal_history_factor_parts  ~48,000 列/天（每檔 × 3 horizon × ~21 sub-factor）

本腳本壓縮歷史以控制 DB 體積，兩張表用同一套保留策略：
- 近 N 天（預設 365）逐日完整保留（雷達 / 歷史追蹤 / diagnostics 預設窗需要）
- 超過 N 天只保留週一（壓縮率 ≈ 5x）

用法：
    python -m scripts.prune_signals                  # 刪兩表舊列（不可逆），不 VACUUM
    python -m scripts.prune_signals --dry-run        # 只報告會刪幾筆，不動資料
    python -m scripts.prune_signals --keep 365       # 改保留近 365 天
    python -m scripts.prune_signals --vacuum         # 刪完後 VACUUM 回收磁碟
    python -m scripts.prune_signals --vacuum-weekly  # 只在週日 VACUUM（排程用，best-effort）

跑頻率：market_update 跑完之後。daily-update.bat 每天跑 `--vacuum-weekly`。

⚠️ VACUUM 需要 exclusive lock：API server（uvicorn）開著時會 SQLITE_BUSY。
   - `--vacuum-weekly` 是 best-effort：拿不到鎖就記 WARN 跳過、不中斷 daily-update，
     釋放的空間會留在 DB 內成為 free pages，下次成功 VACUUM 再回收。
   - 手動完整 VACUUM（例如首次清積壓）請先 `stop.bat` 收掉服務，再跑 `--vacuum`。

刪 signal_history_factor_parts 會連動清空 factor_ic_cache：IC cache key 只追蹤
signal_history 的 MAX(as_of)，不追蹤 factor_parts 內容；不清的話 lookback > N 天的
sub-factor IC 會回到 prune 前的舊結果（stale）。詳見 prune_all()。
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import time
from datetime import timedelta

from app.config import Config
from app.data.clock import taipei_today
from app.data.db import Database

logger = logging.getLogger("prune_signals")

# 兩張表共用：近 365 天逐日 + 之前只留週一。
# 為什麼統一 365：符合「保留 1 年」的心智模型，且讓 aggregate factor IC（讀 signal_history）
# 與 sub-factor IC（讀 factor_parts）的日期覆蓋一致；/diagnostics 預設 lookback=120 天
# 完全落在逐日窗內，預設畫面不受影響。
_DEFAULT_KEEP_DAYS = 365

_SIGNAL_TABLE = "signal_history"
_PARTS_TABLE = "signal_history_factor_parts"

# 週一壓縮的 WHERE：strftime('%w', as_of) 0=Sun..6=Sat，週一 = '1'。
# table 用字串插值（SQLite 不能 bind identifier）；只接受本模組常數、非使用者輸入 → 安全
# （同 prune_warrants.py / app.backup.py 既有做法）。
_PRUNE_WHERE = "as_of < ? AND strftime('%w', as_of) != '1'"


def prune(
    db: Database,
    *,
    table: str = _SIGNAL_TABLE,
    keep_days: int = _DEFAULT_KEEP_DAYS,
    dry_run: bool = False,
) -> dict:
    """壓縮單一歷史表（近 keep_days 天逐日 + 之前只留週一）。

    回傳統計 dict：{table, before, after, deleted, cutoff_date, dry_run}
    """
    today = taipei_today()
    cutoff_iso = (today - timedelta(days=keep_days)).isoformat()

    with db.connect() as conn:
        before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if dry_run:
            to_delete = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {_PRUNE_WHERE}", (cutoff_iso,)
            ).fetchone()[0]
            return {
                "table": table,
                "before": before,
                "after": before - to_delete,
                "deleted": to_delete,
                "cutoff_date": cutoff_iso,
                "dry_run": True,
            }
        cur = conn.execute(f"DELETE FROM {table} WHERE {_PRUNE_WHERE}", (cutoff_iso,))
        deleted = cur.rowcount
        conn.commit()
        after = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    return {
        "table": table,
        "before": before,
        "after": after,
        "deleted": deleted,
        "cutoff_date": cutoff_iso,
        "dry_run": False,
    }


def prune_all(
    db: Database,
    *,
    keep_days: int = _DEFAULT_KEEP_DAYS,
    dry_run: bool = False,
) -> dict:
    """壓縮 signal_history + signal_history_factor_parts，並在實際刪到 factor_parts 列時
    清空 factor_ic_cache。

    為什麼要清 cache：factor_ic_cache 的 key 是
    `{IC_ALGO_VERSION}:{signal_history.MAX(as_of)}:n{distinct as_of}`，**只追蹤
    signal_history、不追蹤 factor_parts 內容**。prune factor_parts 後若不清 cache，
    lookback_days > keep_days 的 /api/diagnostics/sub-factor-ic 會 cache hit 回到 prune 前
    的舊 IC（stale）。這與 backfill_signal_history.py --clear 的「刪 parts → 清 cache」是同一
    個不變式。only 在「真的刪到 parts 列」時清，避免無謂讓 cache 失效。

    回傳 {signal_history: {...}, factor_parts: {...}, ic_cache_cleared: int}
    """
    sh = prune(db, table=_SIGNAL_TABLE, keep_days=keep_days, dry_run=dry_run)
    fp = prune(db, table=_PARTS_TABLE, keep_days=keep_days, dry_run=dry_run)

    cache_cleared = 0
    if not dry_run and fp["deleted"] > 0:
        with db.connect() as conn:
            cur = conn.execute("DELETE FROM factor_ic_cache")
            cache_cleared = cur.rowcount
            conn.commit()
        logger.info(
            "factor_ic_cache 已清空 %d 列（factor_parts 有刪除 → sub-factor IC 將重算）",
            cache_cleared,
        )

    return {"signal_history": sh, "factor_parts": fp, "ic_cache_cleared": cache_cleared}


def _db_size_mb(db: Database) -> float:
    try:
        return os.path.getsize(db.path) / 1024 / 1024
    except OSError:
        return 0.0


def vacuum(db: Database, *, best_effort: bool = False) -> bool:
    """VACUUM 回收磁碟。回傳 True=完成、False=best-effort 模式下因 DB 被佔用而跳過。

    沿用 prune_warrants.py 已驗證的 WAL save/restore 模式：PRAGMA journal_mode 不能在
    VACUUM 進行時改，先把 WAL 切成 DELETE、VACUUM 完再切回 WAL。

    best_effort=True（排程用）：DB 被 API server 鎖住（database is locked）時記 WARN 並
    回 False，不 raise；釋放的 free pages 留在檔內、下次成功 VACUUM 再回收。
    best_effort=False（手動 --vacuum）：任何錯誤照常 raise，讓使用者看到。
    """
    logger.info("VACUUM 中…（數分鐘，期間 DB 鎖住、不要跑其他寫入）")
    t0 = time.time()
    try:
        with db.connect() as conn:
            old_mode = (conn.execute("PRAGMA journal_mode").fetchone()[0] or "").lower()
            try:
                if old_mode == "wal":
                    conn.execute("PRAGMA journal_mode=DELETE")
                conn.execute("VACUUM")
            finally:
                if old_mode == "wal":
                    try:
                        conn.execute("PRAGMA journal_mode=WAL")
                    except sqlite3.OperationalError:
                        pass  # 鎖住時切不回 WAL；下次 connect 的 _init_schema 會重設
    except sqlite3.OperationalError as e:
        if best_effort and "lock" in str(e).lower():
            logger.warning(
                "VACUUM 跳過：DB 被佔用、拿不到 exclusive lock（API server 開著？）。"
                "空間會在下次成功 VACUUM 時回收。(%s)", e,
            )
            return False
        raise
    logger.info("VACUUM 完成 (%.1fs)，DB 大小：%.1f MB", time.time() - t0, _db_size_mb(db))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prune signal_history + factor_parts（近 N 天逐日 + 之前只留週一）"
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=_DEFAULT_KEEP_DAYS,
        help=f"保留最近幾天的逐日資料（預設 {_DEFAULT_KEEP_DAYS}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只統計會刪幾筆，不實際刪除、不 VACUUM",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="刪完後完整 VACUUM 回收磁碟（需 exclusive lock，先 stop.bat）",
    )
    parser.add_argument(
        "--vacuum-weekly",
        action="store_true",
        help="只在週日 VACUUM（排程用，best-effort：拿不到鎖就跳過不報錯）",
    )
    parser.add_argument(
        "--no-vacuum",
        action="store_true",
        help="即使指定 --vacuum / --vacuum-weekly 也一律不 VACUUM",
    )
    args = parser.parse_args()

    if args.keep < 1:
        parser.error("--keep 必須 >= 1")

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    db = Database(Config.load().database.path)
    result = prune_all(db, keep_days=args.keep, dry_run=args.dry_run)

    mode = "[DRY-RUN]" if args.dry_run else "[DONE]"
    for key in ("signal_history", "factor_parts"):
        r = result[key]
        logger.info(
            "%s %s: %s 筆 -> %s 筆 (刪 %s 筆, cutoff=%s)",
            mode, r["table"],
            f"{r['before']:,}", f"{r['after']:,}", f"{r['deleted']:,}", r["cutoff_date"],
        )

    if args.dry_run:
        return 0

    # VACUUM 決策：--no-vacuum 一律不跑；--vacuum 強制跑；--vacuum-weekly 只在週日 best-effort。
    if args.no_vacuum:
        return 0
    if args.vacuum:
        vacuum(db, best_effort=False)
    elif args.vacuum_weekly:
        if taipei_today().weekday() == 6:  # Monday=0 .. Sunday=6
            vacuum(db, best_effort=True)
        else:
            logger.info("今日非週日，跳過每週 VACUUM（free pages 待下次回收）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
