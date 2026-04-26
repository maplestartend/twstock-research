"""每日盤後執行：更新 watchlist 中所有股票的資料。

用法：
    python -m scripts.daily_update
    python -m scripts.daily_update --stock 2330    # 只更新單檔
    python -m scripts.daily_update --info-only     # 只更新股票清單
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 確保可以被當成 module 執行
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Config, load_watchlist  # noqa: E402
from app.data.db import Database  # noqa: E402
from app.data.fetcher import FinMindFetcher  # noqa: E402
from app.data.updater import DataUpdater  # noqa: E402


def setup_logging(cfg: Config) -> None:
    cfg.logging.file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, cfg.logging.level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(cfg.logging.file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", help="只更新指定股票代碼")
    parser.add_argument("--info-only", action="store_true", help="只更新股票基本資訊")
    args = parser.parse_args()

    cfg = Config.load()
    setup_logging(cfg)
    log = logging.getLogger("daily_update")

    db = Database(cfg.database.path)
    fetcher = FinMindFetcher(cfg.finmind, request_delay=cfg.fetch.request_delay)
    updater = DataUpdater(fetcher, db, default_start=cfg.fetch.start_date)

    log.info("更新股票清單…")
    n_info = updater.update_stock_info()
    log.info("股票清單 %d 筆", n_info)
    if args.info_only:
        return 0

    if args.stock:
        targets = {args.stock: ""}
    else:
        targets = load_watchlist()

    log.info("開始更新 %d 檔自選股", len(targets))
    for stock_id, note in targets.items():
        log.info("── %s %s ──", stock_id, note)
        result = updater.update_stock_all(stock_id)
        summary = ", ".join(f"{k}={v}" for k, v in result.items())
        log.info("  %s", summary)

    log.info("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
