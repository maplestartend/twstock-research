"""查詢 run_log 表，看 market_update 等腳本的歷史執行狀況。

用法：
    python -m scripts.run_stats                 # 近 30 天成功率、平均耗時、警告趨勢
    python -m scripts.run_stats --days 7
    python -m scripts.run_stats --show-errors   # 列出錯誤紀錄
    python -m scripts.run_stats --tail 20       # 最近 20 次執行明細
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

# 確保中文/特殊字元在 Windows cp950 終端也能輸出
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app.config import Config  # noqa: E402
from app.data.db import Database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="統計最近 N 天（預設 30）")
    parser.add_argument("--script", help="只看特定腳本，例如 market_update")
    parser.add_argument("--show-errors", action="store_true", help="列出錯誤紀錄")
    parser.add_argument("--tail", type=int, help="列出最近 N 次執行明細")
    args = parser.parse_args()

    db = Database(Config.load().database.path)
    since = (date.today() - timedelta(days=args.days)).isoformat()

    with db.connect() as conn:
        where = "WHERE started_at >= ?"
        params: list = [since]
        if args.script:
            where += " AND script = ?"
            params.append(args.script)
        df = pd.read_sql_query(
            f"SELECT * FROM run_log {where} ORDER BY started_at DESC",
            conn, params=params,
        )

    if df.empty:
        print(f"近 {args.days} 天沒有執行紀錄。")
        return 0

    # 總統計
    by_script = df.groupby("script").agg(
        runs=("run_id", "count"),
        ok=("status", lambda s: (s == "ok").sum()),
        warn=("status", lambda s: (s == "warn").sum()),
        error=("status", lambda s: (s == "error").sum()),
        avg_sec=("duration_sec", "mean"),
        p95_sec=("duration_sec", lambda s: s.quantile(0.95)),
        max_sec=("duration_sec", "max"),
        total_warnings=("n_warnings", "sum"),
    ).round(1)
    by_script["success_rate"] = (by_script["ok"] / by_script["runs"] * 100).round(1)

    print(f"=== 近 {args.days} 天執行統計（from {since}） ===")
    print(by_script.to_string())

    # 最近 tail
    if args.tail:
        print(f"\n=== 最近 {args.tail} 次執行 ===")
        recent = df.head(args.tail)[
            ["run_id", "script", "started_at", "duration_sec", "status", "n_warnings", "note"]
        ]
        print(recent.to_string(index=False))

    # 錯誤明細
    if args.show_errors:
        errs = df[df["status"] == "error"]
        if errs.empty:
            print("\n[OK] 沒有錯誤紀錄。")
        else:
            print(f"\n=== 錯誤紀錄 ({len(errs)} 筆) ===")
            for _, r in errs.iterrows():
                print(f"[{r['started_at']}] {r['script']} (run_id={r['run_id']})")
                print(f"  note: {r['note']}")

    # 健康度簡易結論
    recent_runs = df.head(5)
    recent_errors = (recent_runs["status"] == "error").sum()
    if recent_errors >= 2:
        print(f"\n[!] 最近 5 次執行中有 {recent_errors} 次錯誤，請檢查。")
    else:
        print("\n[OK] 最近執行狀態正常。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
