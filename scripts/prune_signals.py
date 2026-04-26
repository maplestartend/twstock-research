"""S2-4: signal_history retention.

每天 market_update 寫入 ~2,000 列 → 一年 ~50 萬列。本腳本壓縮歷史以控制 DB 體積：
- 近 90 天逐日完整保留（雷達 / 歷史追蹤頁需要）
- 超過 90 天只保留週一（壓縮率 ≈ 5x）

用法：
    python -m scripts.prune_signals               # 實際刪除（不可逆）
    python -m scripts.prune_signals --dry-run     # 只報告會刪幾筆
    python -m scripts.prune_signals --keep 60     # 改保留近 60 天

跑頻率：market_update 跑完之後、或單獨排程每週一次。
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from app.config import Config
from app.data.clock import taipei_today
from app.data.db import Database

logger = logging.getLogger("prune_signals")

# 每月跑完平均剩幾筆 = 4 (週一) / 22 (交易日) ≈ 18% → 壓縮率 ~5x
_DEFAULT_KEEP_DAYS = 90


def prune(db: Database, *, keep_days: int = _DEFAULT_KEEP_DAYS, dry_run: bool = False) -> dict:
    """執行 signal_history 壓縮。

    回傳統計 dict：{before, after, deleted, cutoff_date}
    """
    today = taipei_today()
    cutoff: date = today - timedelta(days=keep_days)
    cutoff_iso = cutoff.isoformat()

    with db.connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM signal_history").fetchone()[0]
        # 該刪的 = 早於 cutoff 且不是週一
        # SQLite strftime('%w', ...) 0=Sunday ~ 6=Saturday，週一 = '1'
        delete_sql = (
            "DELETE FROM signal_history "
            "WHERE as_of < ? AND strftime('%w', as_of) != '1'"
        )
        if dry_run:
            count_sql = (
                "SELECT COUNT(*) FROM signal_history "
                "WHERE as_of < ? AND strftime('%w', as_of) != '1'"
            )
            to_delete = conn.execute(count_sql, (cutoff_iso,)).fetchone()[0]
            return {
                "before": before,
                "after": before - to_delete,
                "deleted": to_delete,
                "cutoff_date": cutoff_iso,
                "dry_run": True,
            }
        cur = conn.execute(delete_sql, (cutoff_iso,))
        deleted = cur.rowcount
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM signal_history").fetchone()[0]

    return {
        "before": before,
        "after": after,
        "deleted": deleted,
        "cutoff_date": cutoff_iso,
        "dry_run": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune signal_history (近 90 天逐日 + 之前只留週一)")
    parser.add_argument(
        "--keep",
        type=int,
        default=_DEFAULT_KEEP_DAYS,
        help=f"保留最近幾天的逐日資料（預設 {_DEFAULT_KEEP_DAYS}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只統計會刪幾筆，不實際刪除",
    )
    args = parser.parse_args()

    if args.keep < 1:
        parser.error("--keep 必須 >= 1")

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    db = Database(Config.load().database.path)
    result = prune(db, keep_days=args.keep, dry_run=args.dry_run)
    mode = "[DRY-RUN]" if result["dry_run"] else "[DONE]"
    logger.info(
        "%s signal_history: %s 筆 -> %s 筆 (刪 %s 筆, cutoff=%s)",
        mode,
        f"{result['before']:,}",
        f"{result['after']:,}",
        f"{result['deleted']:,}",
        result["cutoff_date"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
