"""更新除權息/分割還原股價。用 FinMind 免費端點抓事件，本地重算還原序列。

用法：
    python -m scripts.update_adj                 # 自選股全部還原
    python -m scripts.update_adj --all-in-db     # DB 中有價格資料的全部（耗時，2700 檔×2 api）
    python -m scripts.update_adj --stock 2330    # 單檔
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import watchlist as wl_mod  # noqa: E402
from app.config import Config  # noqa: E402
from app.data.adjuster import update_stock_adjusted  # noqa: E402
from app.data.db import Database  # noqa: E402
from app.data.fetcher import FinMindError, FinMindFetcher  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", help="只處理單一代號")
    parser.add_argument("--all-in-db", action="store_true", help="處理 DB 所有有價格資料的股票（慢）")
    args = parser.parse_args()

    cfg = Config.load()
    cfg.logging.file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(cfg.logging.file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger("update_adj")

    db = Database(cfg.database.path)
    fetcher = FinMindFetcher(cfg.finmind, request_delay=cfg.fetch.request_delay)

    if args.stock:
        targets = [args.stock]
    elif args.all_in_db:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT stock_id FROM daily_price ORDER BY stock_id"
            ).fetchall()
        targets = [r["stock_id"] for r in rows]
    else:
        targets = list(wl_mod.load().keys())

    log.info("處理 %d 檔股票", len(targets))
    t0 = time.time()
    ok, fail = 0, 0
    for i, sid in enumerate(targets, 1):
        try:
            n = update_stock_adjusted(db, fetcher, sid, force_refetch=True)
            log.info("[%d/%d] %s: adj %d 筆", i, len(targets), sid, n)
            ok += 1
        except FinMindError as e:
            log.warning("[%d/%d] %s: FinMind 錯誤 %s", i, len(targets), sid, e)
            fail += 1
        except Exception as e:
            log.warning("[%d/%d] %s: 失敗 %s", i, len(targets), sid, e)
            fail += 1

    log.info("完成：成功 %d，失敗 %d，耗時 %.0fs", ok, fail, time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
