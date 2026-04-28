"""市場級資料更新（TWSE + TPEx）。

用法：
    # 每日增量（從上次最後抓到日的次日到今天）
    python -m scripts.market_update

    # 回補最近 N 個交易日
    python -m scripts.market_update --days 30

    # 指定範圍
    python -m scripts.market_update --from 2024-01-01 --to 2024-12-31

    # 單日
    python -m scripts.market_update --date 2026-04-22
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import watchlist as wl_mod  # noqa: E402
from app.config import Config  # noqa: E402
from app.data.adjuster import update_stock_adjusted  # noqa: E402
from app.data.db import Database  # noqa: E402
from app.data.fetcher import FinMindError, FinMindFetcher  # noqa: E402
from app.data.market_updater import MarketUpdater  # noqa: E402
from app.backup import run_daily_backup  # noqa: E402
from app.notifier import notify  # noqa: E402
from app.report import generate_daily_report  # noqa: E402
from app.data.mops_financials_fetcher import (  # noqa: E402
    derive_quarterly_from_cumulative,
    fetch_history_income_statement,
    fetch_latest_all as fetch_latest_financials_all,
)
from app.run_log import run_context  # noqa: E402
from app.scoring.history import snapshot_today  # noqa: E402


class WarningCollector(logging.Handler):
    """收集 WARNING 以上的 log，用於 run 結束後判斷是否要推播失敗通知。"""
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.WARNING:
            self.records.append(record)


def setup_logging(cfg: Config) -> WarningCollector:
    cfg.logging.file.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # 清掉既有 handler（以免重複執行時疊加）
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_h = RotatingFileHandler(
        cfg.logging.file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_h.setFormatter(fmt)
    root.addHandler(file_h)

    stream_h = logging.StreamHandler(sys.stdout)
    stream_h.setFormatter(fmt)
    root.addHandler(stream_h)

    collector = WarningCollector()
    root.addHandler(collector)
    return collector


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="date_from", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="結束日期 YYYY-MM-DD")
    parser.add_argument("--date", help="只抓單一日期 YYYY-MM-DD")
    parser.add_argument("--days", type=int, help="回補最近 N 天")
    parser.add_argument("--delay", type=float, default=1.0, help="每個 request 間隔（秒）")
    parser.add_argument("--no-snapshot", action="store_true", help="更新完不要自動拍訊號快照")
    parser.add_argument("--snapshot-with-fundamentals", action="store_true", help="訊號快照含基本面（較慢）")
    parser.add_argument("--no-adj", action="store_true", help="更新完不要順手算自選股還原價")
    parser.add_argument("--no-financials", action="store_true", help="不要自動檢測最新季財報（MOPS bulk）")
    parser.add_argument("--no-report", action="store_true", help="不要產每日早報")
    parser.add_argument("--push", action="store_true", help="啟用推播：早報、失敗通知、警告總結（channel 由 config.yaml notify 決定）")
    parser.add_argument("--push-line", dest="push", action="store_true", help=argparse.SUPPRESS)  # 舊旗標相容
    args = parser.parse_args()

    cfg = Config.load()
    collector = setup_logging(cfg)
    log = logging.getLogger("market_update")

    db = Database(cfg.database.path)
    today = date.today()

    try:
        with run_context(db, "market_update", note=_describe_args(args)) as rec:
            updater = MarketUpdater(db, request_delay=args.delay)

            if args.date:
                d = datetime.strptime(args.date, "%Y-%m-%d").date()
                updater.fetch_date_range(d, d)
            elif args.days:
                start = today - timedelta(days=args.days)
                updater.fetch_date_range(start, today)
            elif args.date_from or args.date_to:
                start = datetime.strptime(args.date_from, "%Y-%m-%d").date() if args.date_from else today
                end = datetime.strptime(args.date_to, "%Y-%m-%d").date() if args.date_to else today
                updater.fetch_date_range(start, end)
            else:
                updater.update_incremental(cfg.fetch.start_date, today)

            # 自選股還原價（除非 --no-adj）
            if not args.no_adj:
                wl = wl_mod.load()
                if wl:
                    log.info("更新自選股還原價 (%d 檔)…", len(wl))
                    fetcher = FinMindFetcher(cfg.finmind, request_delay=cfg.fetch.request_delay)
                    t_adj = time.time()
                    for sid in wl:
                        try:
                            update_stock_adjusted(db, fetcher, sid)
                        except FinMindError as e:
                            log.warning("  %s adj 失敗: %s", sid, e)
                        except Exception as e:
                            log.warning("  %s adj 例外: %s", sid, e)
                    log.info("自選股還原價完成 (%.1fs)", time.time() - t_adj)

            # 季財報（MOPS bulk）：每次跑都抓一下最新季 + 跑差分。
            # OpenAPI 只回最新一季（10 秒），idempotent upsert 無副作用；
            # 偵測到新季發布時自動回補上一季 MOPS 歷史資料（補齊 TTM/YoY 計算所需）
            if not args.no_financials:
                try:
                    fin_df = fetch_latest_financials_all(delay=0.3)
                    if not fin_df.empty:
                        n = db.upsert_df(fin_df, "financials_cumulative")
                        dates = sorted(fin_df["date"].astype(str).unique())
                        log.info("MOPS 全市場財報：寫入 %d 筆 / %d 檔 / %s",
                                 n, fin_df["stock_id"].nunique(), dates)

                        # 偵測新季：若 cumulative 最新季比 derived 最新季新 > 1 季，啟動回補
                        _maybe_backfill_history(db, log)

                        # 重算單季差分（idempotent，幾秒內完成）
                        n_d = derive_quarterly_from_cumulative(db)
                        log.info("financials_quarterly_derived 重算：%d 筆", n_d)
                except Exception as e:
                    log.warning("MOPS 財報抓取失敗: %s", e)

            # 資料更新完成後，自動拍一張當日訊號快照（除非 --no-snapshot）
            if not args.no_snapshot:
                try:
                    log.info("計算訊號快照 (~40s)…")
                    t_snap = time.time()
                    snapshot_today(db, include_fundamentals=args.snapshot_with_fundamentals)
                    log.info("訊號快照完成 (%.1fs)", time.time() - t_snap)
                except Exception as e:
                    log.warning("訊號快照失敗: %s", e)

            # 每日早報（除非 --no-report）
            if not args.no_report:
                try:
                    path = generate_daily_report(db, push=args.push)
                    log.info("早報已輸出：%s", path)
                except Exception as e:
                    log.warning("早報失敗: %s", e)

            # 每日備份（需 config.yaml 的 backup.enabled=true 才會跑）
            backup_cfg = cfg.backup or {}
            if backup_cfg.get("enabled"):
                try:
                    summary = run_daily_backup(
                        cfg.database.path,
                        backup_cfg.get("path"),
                        keep_days=int(backup_cfg.get("keep_days", 14)),
                        keep_weeks=int(backup_cfg.get("keep_weeks", 8)),
                        keep_months=int(backup_cfg.get("keep_months", 12)),
                    )
                    tag = "fallback" if summary["is_fallback"] else "ok"
                    log.info("備份 [%s]：%s（目前保留 %d 份 / %.1f MB）",
                             tag, summary["new_file"], summary["retained_count"], summary["total_size_mb"])
                except Exception as e:
                    log.warning("備份失敗: %s", e)

            # 收尾：回寫 run_log 統計
            rec.n_warnings = len(collector.records)

    except Exception as e:
        log.exception("market_update 執行中止: %s", e)
        if args.push:
            notify(f"{type(e).__name__}: {str(e)[:400]}", title="❌ market_update 失敗")
        return 1

    # 收工時推播警告總結（僅 --push 時）
    if args.push and collector.records:
        lines = [f"• {r.getMessage()[:120]}" for r in collector.records[:8]]
        if len(collector.records) > 8:
            lines.append(f"…以及另外 {len(collector.records) - 8} 則")
        notify("\n".join(lines), title=f"⚠️ market_update 有 {len(collector.records)} 則警告")

    return 0


def _maybe_backfill_history(db, log) -> None:
    """偵測 cumulative 最新季比 derived 最新季新 → 啟動 MOPS 歷史回補補上斷層季別。

    避免每次跑都打 MOPS（上游有負載），只在「公告新季財報」當天會啟動一次。
    最多回補 4 季（足夠新一季 TTM 與 YoY）。
    """
    with db.connect() as conn:
        cum = conn.execute(
            "SELECT MAX(year)*10 + MAX(quarter) as k FROM financials_cumulative"
        ).fetchone()
        der = conn.execute(
            "SELECT MAX(year)*10 + MAX(quarter) as k FROM financials_quarterly_derived"
        ).fetchone()
    cum_k = cum["k"] if cum and cum["k"] else 0
    der_k = der["k"] if der and der["k"] else 0
    # cum_k 是用 year*10+quarter 不是真正 quarter index，但夠拿來判斷有沒有新季
    # 比較精確的判斷：cum 最新季是否已有 ≥4 季 derived 資料；若 derived 季數不足，啟動回補
    with db.connect() as conn:
        cum_quarters = {(int(r["year"]), int(r["quarter"])) for r in conn.execute(
            "SELECT DISTINCT year, quarter FROM financials_cumulative"
        ).fetchall()}
    if len(cum_quarters) >= 5:
        return  # 已經有 5+ 季，不用回補
    # 從 cum 最新季倒推 5 季，補齊缺的
    if not cum_quarters:
        return
    latest = max(cum_quarters)
    needed: list[tuple[int, int]] = []
    y, q = latest
    for _ in range(5):
        if (y, q) not in cum_quarters:
            needed.append((y, q))
        q -= 1
        if q < 1:
            q = 4
            y -= 1
    if not needed:
        return
    log.info("偵測到 cumulative 缺 %d 季歷史資料，啟動 MOPS 回補：%s",
             len(needed), ", ".join(f"{y}Q{q}" for y, q in needed))
    for y, q in reversed(needed):
        try:
            df = fetch_history_income_statement(y, q, delay=0.5)
            if not df.empty:
                db.upsert_df(df, "financials_cumulative")
        except Exception as e:
            log.warning("MOPS 歷史 %dQ%d 失敗: %s", y, q, e)


def _describe_args(args) -> str:
    """把 args 摘成一行字，方便在 run_log 查回去。"""
    parts = []
    if args.date: parts.append(f"date={args.date}")
    if args.days: parts.append(f"days={args.days}")
    if args.date_from: parts.append(f"from={args.date_from}")
    if args.date_to: parts.append(f"to={args.date_to}")
    if args.no_snapshot: parts.append("no-snapshot")
    if args.no_adj: parts.append("no-adj")
    if args.no_financials: parts.append("no-financials")
    if args.no_report: parts.append("no-report")
    if args.push: parts.append("push")
    return ",".join(parts) if parts else "default"


if __name__ == "__main__":
    sys.exit(main())
