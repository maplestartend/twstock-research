"""把 signal_history 快照回填到指定的歷史日期區間。

當使用者第一次跑因子有效性檢定（/api/diagnostics/factor-ic）發現 signal_history 只有近幾天的
資料時，可用本腳本批次重算 N 天份的快照，讓 IC 分析有足夠樣本。

每天會跑一次 score_all(as_of=date)，含完整 fundamentals → 約 30-60 秒/天。
**並行模式（預設 --workers 4）**：85 天 ≈ 18-25 分鐘（單核序列模式約 70 分鐘）。
建議先用 --dry-run 看看會跑哪些日期再實際執行。

用法：
    python -m scripts.backfill_signal_history --days 60               # 預設 --workers 4
    python -m scripts.backfill_signal_history --days 60 --workers 1   # 序列模式 (debug 用)
    python -m scripts.backfill_signal_history --days 60 --workers 8   # 機器核心多時加碼
    python -m scripts.backfill_signal_history --days 60 --skip-existing
    python -m scripts.backfill_signal_history --days 7 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

from app.config import Config
from app.data.db import Database
from app.scoring import radar
from app.scoring.history import snapshot_today

logger = logging.getLogger("backfill_signal_history")


def _snapshot_one_day(args: tuple[str, str, list[tuple[str, str]], bool]) -> tuple[str, int, float, str | None]:
    """Worker entry：跑單一 as_of 的 snapshot_today。每個 worker process 自己建 DB 連線
    （SQLite 在 WAL 模式下支援多 process 並行寫；snapshot_today 內部 transaction 短）。

    回傳 (as_of_iso, rows_written, elapsed_seconds, error_msg | None)。
    例外不擲出 — 用 error_msg 帶回主程序，避免 Pool 中斷其他 worker。
    """
    db_path, as_of_iso, candidate_stocks, include_fundamentals = args
    t0 = time.time()
    try:
        # Database 初始化要 Path 物件（自己呼叫 .parent.mkdir）；Pool 跨 process 傳的是 str
        # 比較好（Path 在某些情況不易 pickle），所以這裡再轉回。
        db = Database(Path(db_path))
        n = snapshot_today(
            db,
            include_fundamentals=include_fundamentals,
            as_of=as_of_iso,
            candidate_stocks=candidate_stocks,
        )
        return as_of_iso, n, time.time() - t0, None
    except Exception as e:
        return as_of_iso, 0, time.time() - t0, f"{type(e).__name__}: {e}"


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
    parser.add_argument(
        "--workers", type=int, default=4,
        help="並行 process 數（預設 4）。每個 worker 同時處理一個 as_of 日期；"
        " SQLite WAL 模式下多 process 並寫安全。設 1 = 序列模式（debug 用）。",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    db = Database(Config.load().database.path)

    if args.clear and not args.dry_run:
        with db.connect() as conn:
            n_sh = conn.execute("SELECT COUNT(*) FROM signal_history").fetchone()[0]
            n_cache = conn.execute("SELECT COUNT(*) FROM factor_ic_cache").fetchone()[0]
            conn.execute("DELETE FROM signal_history")
            # IC cache key 是 (scope, snapshot_max_as_of, lookback)；--clear 用同一個日期重寫，
            # 不清 cache 會回舊算法的 IC 值（髒讀）。
            conn.execute("DELETE FROM factor_ic_cache")
            conn.commit()
        logger.info("--clear: 清掉 signal_history %d 列 + ic_cache %d 列", n_sh, n_cache)
    elif args.clear and args.dry_run:
        logger.info("[dry-run] would DELETE FROM signal_history + ic_cache")

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
    workers = max(1, args.workers)
    db_path = str(Config.load().database.path)
    include_fundamentals = not args.no_fundamentals
    job_args = [
        (db_path, d, candidate_stocks, include_fundamentals) for d in plan
    ]

    if workers == 1:
        # 序列模式 — 跟舊行為一致，方便 debug。
        for i, args_tuple in enumerate(job_args, 1):
            d, n, elapsed, err = _snapshot_one_day(args_tuple)
            if err:
                logger.error("[%d/%d] %s 失敗: %s", i, len(plan), d, err)
            else:
                written_total += n
                logger.info("[%d/%d] %s → %d 筆，%.1fs", i, len(plan), d, n, elapsed)
    else:
        # 並行模式 — 每個 worker 跑一個 as_of。SQLite WAL 允許多 process 並寫，
        # snapshot_today 的 transaction 短（< 1s）所以 lock 爭用很低。
        # task 在主程序按 plan 順序送出，但 worker 完成順序不保證；用 as_completed 收結果。
        logger.info("並行模式 workers=%d", workers)
        completed = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_snapshot_one_day, a): a[1] for a in job_args}
            for fut in as_completed(futures):
                completed += 1
                d, n, elapsed, err = fut.result()
                if err:
                    logger.error("[%d/%d] %s 失敗: %s", completed, len(plan), d, err)
                else:
                    written_total += n
                    logger.info("[%d/%d] %s → %d 筆，%.1fs", completed, len(plan), d, n, elapsed)

    total_elapsed = time.time() - start
    logger.info("完成：%d 天、共寫 %d 筆、總耗時 %.1f 分鐘（workers=%d）",
                len(plan), written_total, total_elapsed / 60, workers)

    # 預熱 factor_ic_cache：snapshot 剛推進，第一個進 /diagnostics 的人會等 ~17s。
    # 這裡跑完算好寫進去，使用者第一次進就是秒回。失敗只 log，不影響主任務。
    #
    # 必須先 DELETE cache：backfill 期間若有 UI 請求觸發 /diagnostics，會在 signal_history
    # 還沒寫滿時就用 partial data 算出空 IC、寫進 cache（key 在 worker 寫第一筆 2026-04-29
    # 時就 lock 住）。預熱時若不清掉，會吃到那批 stale row 而不是真正算一次。
    try:
        from app.scoring.factor_diagnostics import (
            DEFAULT_LOOKBACK_DAYS,
            get_factor_ic_cached,
        )
        with db.connect() as conn:
            conn.execute("DELETE FROM factor_ic_cache")
            conn.commit()
        t_cache = time.time()
        agg = get_factor_ic_cached(db, lookback_days=DEFAULT_LOOKBACK_DAYS)
        logger.info("預熱 IC cache：aggregate %d 列，耗時 %.1fs",
                    len(agg), time.time() - t_cache)
    except Exception:
        logger.exception("預熱 IC cache 失敗（可忽略，UI 第一次進頁時會重算）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
