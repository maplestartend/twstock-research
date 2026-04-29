"""把 signal_history 快照回填到指定的歷史日期區間。

當使用者第一次跑因子有效性檢定（/api/diagnostics/factor-ic）發現 signal_history 只有近幾天的
資料時，可用本腳本批次重算 N 天份的快照，讓 IC 分析有足夠樣本。

每天會跑一次 score_all(as_of=date)，含完整 fundamentals → 約 30-60 秒/天。
60 天 ≈ 30-60 分鐘。建議先用 --dry-run 看看會跑哪些日期再實際執行。

用法：
    python -m scripts.backfill_signal_history --days 60          # 回填過去 60 個交易日
    python -m scripts.backfill_signal_history --days 60 --skip-existing  # 跳過已有快照的日期
    python -m scripts.backfill_signal_history --days 7 --dry-run         # 只列出計畫
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date, timedelta

from app.config import Config
from app.data.db import Database
from app.scoring import radar
from app.scoring.history import snapshot_today

logger = logging.getLogger("backfill_signal_history")


def _trading_days_back(db: Database, n: int, until: date) -> list[str]:
    """從 daily_price 取最近 n 個有資料的交易日（≤ until）。"""
    until_iso = until.isoformat()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_price WHERE date <= ? ORDER BY date DESC LIMIT ?",
            (until_iso, n),
        ).fetchall()
    return [r["date"] for r in rows][::-1]  # 從舊到新跑，比較像連續更新


def _existing_snapshot_dates(db: Database) -> set[str]:
    with db.connect() as conn:
        rows = conn.execute("SELECT DISTINCT as_of FROM signal_history").fetchall()
    return {r["as_of"] for r in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 signal_history 歷史快照")
    parser.add_argument("--days", type=int, default=60, help="回填天數（預設 60 個交易日）")
    parser.add_argument("--skip-existing", action="store_true", help="跳過已有快照的日期")
    parser.add_argument("--clear", action="store_true",
                        help="DELETE FROM signal_history 後再回填。改過 scoring 規則時用，避免新舊算法混在一起。")
    parser.add_argument("--dry-run", action="store_true", help="只列出計畫不實際跑")
    parser.add_argument("--no-fundamentals", action="store_true", help="略過財報以加速（長期分數會用 per_pbr 推估）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    db = Database(Config.load().database.path)

    if args.clear and not args.dry_run:
        with db.connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM signal_history").fetchone()[0]
            conn.execute("DELETE FROM signal_history")
            conn.commit()
        logger.info("--clear: 清掉 signal_history %d 列", n)
    elif args.clear and args.dry_run:
        logger.info("[dry-run] would DELETE FROM signal_history")

    today = date.today()
    targets = _trading_days_back(db, args.days, today)
    if not targets:
        logger.error("daily_price 沒有資料 → 無法決定回填日期")
        return 2

    existing = _existing_snapshot_dates(db) if args.skip_existing else set()
    plan = [d for d in targets if d not in existing]
    skipped = [d for d in targets if d in existing]

    # 候選池預先計算一次：避免每個 as_of 都重跑 list_candidate_stocks() 全表掃描。
    # 注意：as_of 越往回，實際可用股票仍會在 score_all 內因 len(price)<min_days 被自動跳過，
    # 所以重用同一份候選池不會破壞歷史語意。
    candidate_stocks = radar.list_candidate_stocks(db, min_days=60)

    logger.info("計畫回填 %d 天（總共 %d 個交易日；已有快照跳過 %d 個）",
                len(plan), len(targets), len(skipped))
    if args.dry_run:
        for d in plan:
            print(f"[dry-run] would snapshot {d}")
        return 0

    start = time.time()
    written_total = 0
    for i, d in enumerate(plan, 1):
        t0 = time.time()
        try:
            n = snapshot_today(
                db,
                include_fundamentals=not args.no_fundamentals,
                as_of=d,
                candidate_stocks=candidate_stocks,
            )
            elapsed = time.time() - t0
            written_total += n
            logger.info("[%d/%d] %s → %d 筆，%.1fs", i, len(plan), d, n, elapsed)
        except Exception:
            logger.exception("[%d/%d] %s 失敗，繼續下一天", i, len(plan), d)

    total_elapsed = time.time() - start
    logger.info("完成：%d 天、共寫 %d 筆、總耗時 %.1f 分鐘",
                len(plan), written_total, total_elapsed / 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
