"""資料品質檢查：掃描各主要表的完整性，找出缺日 / 缺股票。

用法：
    python -m scripts.dq_check            # 檢查最近 20 個交易日
    python -m scripts.dq_check --days 60  # 檢查最近 60 天
    python -m scripts.dq_check --push     # 有問題時推播通知

輸出：
- stdout：每個表一行「最新日期 / 延遲天數 / 近 N 日缺值最多的 Top 10 檔」
- 若 --push 且發現 WARNING+ 等級問題，走 app.notifier 推出去
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402
from app.data.db import Database  # noqa: E402
from app.notifier import notify  # noqa: E402


# 要檢查的表 + 日期欄 + 描述
CHECKS = [
    ("daily_price", "date", "日線價量"),
    ("institutional", "date", "三大法人"),
    ("margin", "date", "融資券"),
    ("per_pbr", "date", "本益比"),
    ("monthly_revenue", "date", "月營收"),
    ("signal_history", "as_of", "訊號快照"),
]

# 月營收資料性質不同（月更新），容忍度另計
MONTHLY_TABLES = {"monthly_revenue"}


def check_table(db: Database, table: str, date_col: str, days: int) -> dict:
    """檢查單一表的資料品質。"""
    today = date.today()
    since = (today - timedelta(days=days)).isoformat()

    with db.connect() as conn:
        mx_row = conn.execute(f"SELECT MAX({date_col}) AS mx FROM {table}").fetchone()
        mx = mx_row["mx"] if mx_row and mx_row["mx"] else None

        distinct_dates = conn.execute(
            f"SELECT COUNT(DISTINCT {date_col}) AS n FROM {table} WHERE {date_col} >= ?",
            (since,),
        ).fetchone()["n"]

        # 近 N 日內每股缺值最多的 Top 10（只對 daily 表有意義）
        gaps: list[tuple[str, int]] = []
        if table not in MONTHLY_TABLES and mx:
            rows = conn.execute(
                f"""
                SELECT stock_id, COUNT(DISTINCT {date_col}) AS n
                FROM {table}
                WHERE {date_col} >= ? AND {date_col} <= ?
                GROUP BY stock_id
                ORDER BY n ASC
                LIMIT 10
                """,
                (since, mx),
            ).fetchall()
            expected = distinct_dates
            gaps = [(r["stock_id"], expected - r["n"]) for r in rows if expected - r["n"] > 0]

    lag = (today - pd_date(mx)).days if mx else 9999
    return {
        "table": table,
        "latest": mx,
        "lag_days": lag,
        "distinct_dates_in_window": distinct_dates,
        "top_gaps": gaps,
    }


def pd_date(s: str):
    from datetime import datetime
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=20, help="檢查近 N 日（預設 20）")
    parser.add_argument("--push", action="store_true", help="有嚴重問題時推播")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    log = logging.getLogger("dq_check")

    cfg = Config.load()
    db = Database(cfg.database.path)

    results = []
    critical: list[str] = []
    warnings: list[str] = []

    for table, date_col, label in CHECKS:
        try:
            r = check_table(db, table, date_col, args.days)
        except Exception as e:
            log.error("%s 檢查失敗：%s", table, e)
            critical.append(f"❌ {label}：檢查失敗 {e}")
            continue

        # 月營收容忍 35 天延遲，其他表 3 天
        lag_threshold = 35 if table in MONTHLY_TABLES else 3
        status = "✅"
        if r["lag_days"] > lag_threshold:
            status = "❌"
            critical.append(f"{label}：最新 {r['latest']} 延遲 {r['lag_days']} 天（門檻 {lag_threshold}）")
        elif r["top_gaps"]:
            worst = r["top_gaps"][0]
            if worst[1] >= args.days // 3:
                status = "⚠️"
                warnings.append(f"{label}：{worst[0]} 近 {args.days} 日缺 {worst[1]} 天")

        log.info("%s %s  最新=%s  延遲=%d 天  近%d日=%d 個交易日",
                 status, label, r["latest"], r["lag_days"], args.days, r["distinct_dates_in_window"])
        if r["top_gaps"]:
            log.info("    近期缺值最多前 5 檔: %s",
                     ", ".join(f"{s}(-{g})" for s, g in r["top_gaps"][:5]))
        results.append(r)

    # 推播
    if args.push:
        if critical:
            notify("\n".join(critical), title=f"❌ 資料品質 CRITICAL ({len(critical)})")
        elif warnings:
            notify("\n".join(warnings[:10]), title=f"⚠️ 資料品質警告 ({len(warnings)})")
        else:
            log.info("資料品質正常，無需推播")

    return 1 if critical else 0


if __name__ == "__main__":
    sys.exit(main())
