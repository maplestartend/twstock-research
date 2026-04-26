"""從 FinMind TaiwanStockInfo 補回 stock_info.industry_category。

只需偶爾跑一次（產業分類變動極少）。
    python -m scripts.refresh_industry
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402
from app.data.db import Database  # noqa: E402
from app.data.fetcher import FinMindFetcher  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    log = logging.getLogger("refresh_industry")

    cfg = Config.load()
    db = Database(cfg.database.path)
    fetcher = FinMindFetcher(cfg.finmind, request_delay=0.3)

    df = fetcher.stock_info()
    if df.empty:
        log.error("FinMind TaiwanStockInfo 回傳空")
        return 1

    df = df[["stock_id", "stock_name", "industry_category", "type"]].drop_duplicates("stock_id")
    log.info("FinMind 回傳 %d 檔", len(df))

    with db.connect() as conn:
        conn.executemany(
            """
            INSERT INTO stock_info (stock_id, stock_name, industry_category, type)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stock_id) DO UPDATE SET
                industry_category = excluded.industry_category,
                updated_at = CURRENT_TIMESTAMP
            """,
            [(r["stock_id"], r["stock_name"], r["industry_category"], r["type"]) for _, r in df.iterrows()],
        )
        conn.commit()
    log.info("industry_category 已補回")
    return 0


if __name__ == "__main__":
    sys.exit(main())
