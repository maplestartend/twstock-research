"""一次性清掉 daily_price / institutional / margin / per_pbr 裡非可交易股票的列。

背景：TWSE/TPEX OpenAPI 的 ALLBUT0999 端點會把權證 (5 碼) / 牛熊證 (6 碼) 一併回傳，舊版
fetcher 無腦塞進四張交易資料表。daily_price 82% 的列是權證 (5 碼)；institutional 多年下來累積
9.15M 列「孤兒」（6 碼 stock_id 在 stock_info 已不存在 — 過期權證早就被清掉了）。

採「白名單」語意：保留 stock_info.is_tradable=1 的，其它一律刪。比「黑名單」(is_tradable=0)
多刪掉那 9.15M 孤兒列；只要 stock_info 有正確標 is_tradable 給真股票/ETF，就不會誤殺。

新版 market_updater.py 已加 _filter_tradable() 阻擋未來新權證寫入。這支腳本是「一次性存量
清理」：把 DB 裡已有的非可交易列刪光 + VACUUM 把空間還給 OS。

預估：清完 DB 從 ~6.0 GB 縮到 ~1.5 GB（釋放 ~4.5 GB）。VACUUM 5-15 分鐘。

⚠️ 不要在 backfill / 抓資料 / FastAPI 跑的時候動，VACUUM 需要 exclusive lock。

用法：
    python -m scripts.prune_warrants               # dry-run 看會刪多少
    python -m scripts.prune_warrants --apply       # 真的刪 + VACUUM
    python -m scripts.prune_warrants --apply --no-vacuum   # 只刪不 VACUUM（之後手動跑）
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402
from app.data.db import Database  # noqa: E402

logger = logging.getLogger("prune_warrants")

# 只清「按 stock_id 拆分、且 is_tradable 概念有意義」的表。
# financials / financials_cumulative / financials_quarterly_derived / monthly_revenue 已驗證
# 沒有權證污染（權證沒財報），不需要清。
TARGET_TABLES = ("daily_price", "institutional", "margin", "per_pbr")


def _count_rows(db: Database) -> dict[str, tuple[int, int]]:
    """回傳 {table: (to_delete_rows, total_rows)}。

    to_delete = 不可交易 (is_tradable!=1)；包括明確標 0、NULL（沒標到）、stock_info 沒對應到。
    """
    out: dict[str, tuple[int, int]] = {}
    with db.connect() as conn:
        for tbl in TARGET_TABLES:
            try:
                total = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                # 白名單：留 is_tradable=1 的；其它都算待刪
                to_delete = conn.execute(
                    f"SELECT COUNT(*) FROM {tbl} t "
                    "WHERE t.stock_id NOT IN ("
                    "  SELECT stock_id FROM stock_info WHERE is_tradable = 1"
                    ")"
                ).fetchone()[0]
                out[tbl] = (to_delete, total)
            except Exception as e:
                logger.warning("無法統計 %s: %s", tbl, e)
                out[tbl] = (0, 0)
    return out


def _delete_warrants(db: Database) -> dict[str, int]:
    """白名單 DELETE：只留 stock_info.is_tradable=1 的列，其它一次刪除。"""
    out: dict[str, int] = {}
    for tbl in TARGET_TABLES:
        t0 = time.time()
        with db.connect() as conn:
            cur = conn.execute(
                f"DELETE FROM {tbl} WHERE stock_id NOT IN ("
                "  SELECT stock_id FROM stock_info WHERE is_tradable = 1"
                ")"
            )
            deleted = cur.rowcount
            conn.commit()
        elapsed = time.time() - t0
        logger.info("  %s: 刪 %d 列 (%.1fs)", tbl, deleted, elapsed)
        out[tbl] = deleted
    return out


def _vacuum(db: Database) -> None:
    logger.info("VACUUM 中… (5-15 分鐘，期間 DB 鎖住、不要跑其他寫入)")
    t0 = time.time()
    with db.connect() as conn:
        # PRAGMA journal_mode 不能在 VACUUM 進行時改；先存後復原
        old_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if old_mode.lower() == "wal":
            conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("VACUUM")
        if old_mode.lower() == "wal":
            conn.execute("PRAGMA journal_mode=WAL")
    logger.info("VACUUM 完成 (%.1fs)", time.time() - t0)


def _db_size_mb(db: Database) -> float:
    return os.path.getsize(db.path) / 1024 / 1024


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="實際執行（沒帶這個就只 dry-run 印計畫）")
    parser.add_argument("--no-vacuum", action="store_true",
                        help="刪完不跑 VACUUM（之後可手動跑）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    db = Database(Config.load().database.path)

    logger.info("=== 權證資料清理 ===")
    logger.info("DB 路徑: %s", db.path)
    logger.info("DB 大小（清前）: %.1f MB", _db_size_mb(db))
    logger.info("")

    counts = _count_rows(db)
    total_to_delete = sum(w for w, _ in counts.values())
    total_rows = sum(t for _, t in counts.values())
    logger.info("各表非可交易列數（含權證 + 孤兒）：")
    for tbl, (w, t) in counts.items():
        pct = (w / t * 100) if t else 0
        logger.info("  %-15s: 待刪 %10s / 總 %10s (%.1f%%)",
                    tbl, f"{w:,}", f"{t:,}", pct)
    logger.info("合計欲刪 %s 列（占 4 表共 %s 列的 %.1f%%）",
                f"{total_to_delete:,}", f"{total_rows:,}",
                (total_to_delete / total_rows * 100) if total_rows else 0)

    if not args.apply:
        logger.info("")
        logger.info("[dry-run] 實際清要加 --apply。VACUUM 期間 DB 會鎖住，建議先 stop.bat 收掉服務。")
        return 0

    logger.info("")
    logger.info("開始 DELETE…")
    deleted = _delete_warrants(db)
    logger.info("共刪 %s 列", f"{sum(deleted.values()):,}")

    if not args.no_vacuum:
        _vacuum(db)
        logger.info("DB 大小（清後）: %.1f MB", _db_size_mb(db))
    else:
        logger.info("--no-vacuum：DB 檔案大小不會立即縮小，需要手動跑 VACUUM。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
