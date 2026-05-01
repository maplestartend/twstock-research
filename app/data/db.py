from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd

from app.data.publish_dates import quarter_end_to_publish_date, quarter_publish_date

logger = logging.getLogger(__name__)


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS stock_info (
        stock_id TEXT PRIMARY KEY,
        stock_name TEXT,
        industry_category TEXT,
        type TEXT,
        is_tradable INTEGER DEFAULT 1,
        last_seen_date TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_price (
        date TEXT NOT NULL,
        stock_id TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL,
        volume REAL, amount REAL, turnover REAL, spread REAL,
        PRIMARY KEY (stock_id, date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_price_date ON daily_price(date)",
    """
    CREATE TABLE IF NOT EXISTS institutional (
        date TEXT NOT NULL,
        stock_id TEXT NOT NULL,
        foreign_net REAL,
        investment_trust_net REAL,
        dealer_net REAL,
        PRIMARY KEY (stock_id, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS margin (
        date TEXT NOT NULL,
        stock_id TEXT NOT NULL,
        margin_balance REAL,
        margin_change REAL,
        short_balance REAL,
        short_change REAL,
        PRIMARY KEY (stock_id, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS per_pbr (
        date TEXT NOT NULL,
        stock_id TEXT NOT NULL,
        per REAL,
        pbr REAL,
        dividend_yield REAL,
        PRIMARY KEY (stock_id, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS financials (
        date TEXT NOT NULL,
        stock_id TEXT NOT NULL,
        type TEXT NOT NULL,
        value REAL,
        origin_name TEXT,
        PRIMARY KEY (stock_id, date, type)
    )
    """,
    # TWSE/TPEX OpenAPI 的綜合損益表/資產負債表是「當季累計」值（Q4 = 全年累計），
    # 與 FinMind 的單季資料語意不同，因此獨立一張表保存，避免污染 financials。
    # 下游 fundamentals 層會在 financials 缺資料時 fallback 到此表。
    """
    CREATE TABLE IF NOT EXISTS financials_cumulative (
        date TEXT NOT NULL,
        stock_id TEXT NOT NULL,
        type TEXT NOT NULL,
        value REAL,
        year INTEGER,
        quarter INTEGER,
        origin_name TEXT,
        publish_date TEXT,
        PRIMARY KEY (stock_id, date, type)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fin_cum_date ON financials_cumulative(date)",
    "CREATE INDEX IF NOT EXISTS idx_fin_cum_stock ON financials_cumulative(stock_id)",
    # idx_fin_cum_publish 移到 migration（_migrate_financials_cumulative_publish_date）裡建，
    # 因為舊 DB 還沒 ALTER TABLE 加 publish_date 欄位，CREATE INDEX 會 OperationalError。
    # 累計值差分後的「單季值」，供 fundamentals 算 TTM / YoY。
    # 由 financials_cumulative 在 derive_quarterly_from_cumulative() 自動產生，不直接抓。
    """
    CREATE TABLE IF NOT EXISTS financials_quarterly_derived (
        date TEXT NOT NULL,
        stock_id TEXT NOT NULL,
        type TEXT NOT NULL,
        value REAL,
        year INTEGER,
        quarter INTEGER,
        publish_date TEXT,
        PRIMARY KEY (stock_id, date, type)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fin_qd_date ON financials_quarterly_derived(date)",
    "CREATE INDEX IF NOT EXISTS idx_fin_qd_stock ON financials_quarterly_derived(stock_id)",
    # idx_fin_qd_publish 同樣移到 migration 裡建。
    # 歷史殘留：本表目前 0 rows、無 writer。
    # 除權息資料現在統一走 `adj_event`（含 before/after price + factor），event_driven.py
    # 與 calendar API 都讀那邊。保留 schema 是為了 backward compat（舊備份還原時不會
    # 因缺表崩潰）；確認沒有任何外部 dump 依賴它後可刪。
    """
    CREATE TABLE IF NOT EXISTS dividend (
        stock_id TEXT NOT NULL,
        year TEXT NOT NULL,
        cash_dividend REAL,
        stock_dividend REAL,
        ex_dividend_date TEXT,
        PRIMARY KEY (stock_id, year)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fetch_log (
        stock_id TEXT NOT NULL,
        dataset TEXT NOT NULL,
        last_date TEXT,
        last_run TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (stock_id, dataset)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_history (
        as_of TEXT NOT NULL,
        stock_id TEXT NOT NULL,
        stock_name TEXT,
        close REAL,
        short REAL,
        mid REAL,
        long REAL,
        composite REAL,
        vr_macd REAL,
        recommendation TEXT,
        strategies TEXT,
        data_completeness REAL,
        is_stale INTEGER DEFAULT 0,
        engine_version TEXT,
        PRIMARY KEY (as_of, stock_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_signal_asof ON signal_history(as_of)",
    "CREATE INDEX IF NOT EXISTS idx_signal_stock ON signal_history(stock_id)",
    # 子因子分數歷史（給 /diagnostics 算 sub-factor IC 用）。
    # 長格式：每檔 × 每天 × 每個 horizon × 每個 sub-factor 一列。
    # short ~10 個 sub-factor + mid 6 + long 5 = 21 個 → 2300 檔 × 21 = ~48k 列/天，
    # 90 天 ≈ 4.3M 列（SQLite 單表輕鬆）。橫格式（直接擴 signal_history 21 欄）
    # 也能用，但長格式對「跑遍所有因子算 IC」是 GROUP BY factor 一行 query 解決。
    """
    CREATE TABLE IF NOT EXISTS signal_history_factor_parts (
        as_of TEXT NOT NULL,
        stock_id TEXT NOT NULL,
        horizon TEXT NOT NULL,
        factor TEXT NOT NULL,
        score REAL,
        PRIMARY KEY (stock_id, as_of, horizon, factor)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_factor_parts_asof ON signal_history_factor_parts(as_of)",
    "CREATE INDEX IF NOT EXISTS idx_factor_parts_factor ON signal_history_factor_parts(horizon, factor)",
    # IC 計算結果快取：原始 query 要讀 4M 列 factor_parts、~2M 列 daily_price，74% 時間在 I/O。
    # 把 compute_factor_ic / compute_subfactor_ic 的輸出存進這張 ~80 列的小表，UI 直接 SELECT
    # 即秒回。失效條件：snapshot_max_as_of（與 lookback_days、scope）變了 → 重算。
    """
    CREATE TABLE IF NOT EXISTS factor_ic_cache (
        scope TEXT NOT NULL,                -- 'aggregate' | 'subfactor'
        snapshot_max_as_of TEXT NOT NULL,   -- 當下 signal_history.MAX(as_of)，用來判斷新鮮度
        lookback_days INTEGER NOT NULL,
        horizon TEXT NOT NULL,              -- aggregate: forward 天數字串；subfactor: 'short'|'mid'|'long'
        factor TEXT NOT NULL,
        forward_horizon INTEGER NOT NULL,   -- aggregate 同 horizon；subfactor 是 5/20/60
        ic REAL,
        ic_ir REAL,
        top_quintile_return REAL,
        bot_quintile_return REAL,
        n_dates INTEGER,
        avg_n_stocks REAL,
        ic_ci_lo REAL,                      -- 95% bootstrap CI 下界
        ic_ci_hi REAL,                      -- 95% bootstrap CI 上界
        computed_at TEXT NOT NULL,
        PRIMARY KEY (scope, snapshot_max_as_of, lookback_days, horizon, factor, forward_horizon)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_factor_ic_cache_scope ON factor_ic_cache(scope, snapshot_max_as_of, lookback_days)",
    """
    CREATE TABLE IF NOT EXISTS adj_event (
        date TEXT NOT NULL,
        stock_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        before_price REAL,
        after_price REAL,
        factor REAL,
        PRIMARY KEY (stock_id, date, event_type)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_price_adj (
        date TEXT NOT NULL,
        stock_id TEXT NOT NULL,
        close_adj REAL,
        open_adj REAL,
        high_adj REAL,
        low_adj REAL,
        PRIMARY KEY (stock_id, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS index_daily (
        date TEXT NOT NULL,
        index_name TEXT NOT NULL,
        close REAL,
        change REAL,
        change_pct REAL,
        PRIMARY KEY (index_name, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS monthly_revenue (
        date TEXT NOT NULL,
        stock_id TEXT NOT NULL,
        revenue REAL,
        revenue_month INTEGER,
        revenue_year INTEGER,
        mom_pct REAL,
        yoy_pct REAL,
        PRIMARY KEY (stock_id, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS holdings (
        stock_id TEXT PRIMARY KEY,
        shares REAL NOT NULL,
        avg_cost REAL NOT NULL,
        entry_date TEXT,
        note TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trade_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL,
        stock_id TEXT NOT NULL,
        action TEXT NOT NULL,
        shares REAL NOT NULL,
        price REAL NOT NULL,
        fee REAL DEFAULT 0,
        tax REAL DEFAULT 0,
        note TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trade_stock ON trade_log(stock_id)",
    "CREATE INDEX IF NOT EXISTS idx_trade_date ON trade_log(trade_date)",
    # 針對 radar._bulk_load 的 WHERE date >= ? 查詢加 date 索引
    "CREATE INDEX IF NOT EXISTS idx_inst_date ON institutional(date)",
    "CREATE INDEX IF NOT EXISTS idx_margin_date ON margin(date)",
    "CREATE INDEX IF NOT EXISTS idx_per_pbr_date ON per_pbr(date)",
    "CREATE INDEX IF NOT EXISTS idx_financials_date ON financials(date)",
    "CREATE INDEX IF NOT EXISTS idx_monthly_rev_date ON monthly_revenue(date)",
    "CREATE INDEX IF NOT EXISTS idx_signal_composite ON signal_history(as_of, composite DESC)",
    "CREATE INDEX IF NOT EXISTS idx_daily_price_adj_date ON daily_price_adj(date)",
    # observability: market_update 每次執行的結果紀錄
    """
    CREATE TABLE IF NOT EXISTS run_log (
        run_id INTEGER PRIMARY KEY AUTOINCREMENT,
        script TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        duration_sec REAL,
        status TEXT NOT NULL,
        n_warnings INTEGER DEFAULT 0,
        rows_written INTEGER,
        note TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_run_log_started ON run_log(started_at DESC)",
    # 權重預設儲存：weight-tuner 頁面讓使用者把調好的 SHORT/MID/LONG 三組權重存成命名 preset。
    # weights_json 內容格式：{"short": {key: weight, ...}, "mid": {...}, "long": {...}}
    """
    CREATE TABLE IF NOT EXISTS user_weight_preset (
        name TEXT PRIMARY KEY,
        description TEXT,
        weights_json TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # LLM 敘事永久快取。
    # PK=(stock_id, as_of, kind)：同一支股票同一交易日同一種敘事永遠不重算。
    # `as_of` 來自 score_stock 的 signal_history.as_of（最新 daily_price 日期），
    # 與評分快照同步 → 分數變了 (as_of 推進) 自然對應新一筆 narrative，舊的留作歷史。
    # 模型 / token 用量記錄方便未來 audit 成本與比較模型效果。
    """
    CREATE TABLE IF NOT EXISTS narrative_cache (
        stock_id TEXT NOT NULL,
        as_of TEXT NOT NULL,
        kind TEXT NOT NULL,
        narrative TEXT NOT NULL,
        model TEXT NOT NULL,
        input_tokens INTEGER,
        output_tokens INTEGER,
        cache_read_tokens INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (stock_id, as_of, kind)
    )
    """,
    # 預警規則：使用者設定「條件成立 → 推播一次」。
    # rule_kind 列舉: price_below / price_above / score_drop / score_rise / atr_breached
    # threshold 解釋:
    #   price_below / price_above → 絕對價格
    #   score_drop / score_rise   → 分數差分閾值（例如 score_drop=10 = 短期分數 7 天內掉 10 分）
    #   atr_breached              → 0/1 旗標，無 threshold（表示「跌破 trailing-ATR 停損」）
    # last_triggered_at IS NULL = 從未觸發；非 NULL = 已推播過一次（避免每天重複推）。
    """
    CREATE TABLE IF NOT EXISTS alert_rule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_id TEXT NOT NULL,
        rule_kind TEXT NOT NULL,
        threshold REAL,
        note TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        last_triggered_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_alert_stock ON alert_rule(stock_id)",
    "CREATE INDEX IF NOT EXISTS idx_alert_active ON alert_rule(active)",
]


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            # WAL 模式：讀寫並行、崩潰恢復較好；一次設定即持久化到 DB 檔
            conn.execute("PRAGMA journal_mode=WAL")
            for stmt in SCHEMA:
                conn.execute(stmt)
            self._migrate_add_columns(conn)
            self._migrate_monthly_revenue_publish_date(conn)
            self._migrate_stock_info_is_tradable(conn)
            self._migrate_financials_cumulative_publish_date(conn)
            self._migrate_financials_publish_date(conn)
            self._migrate_stock_info_last_seen_date(conn)
            conn.commit()

    @staticmethod
    def _migrate_add_columns(conn: sqlite3.Connection) -> None:
        """對舊資料庫補上缺少的欄位；SQLite 的 ALTER TABLE ADD COLUMN 無 IF NOT EXISTS，
        只能先查 PRAGMA table_info 再決定是否 ADD。"""
        migrations: list[tuple[str, str, str]] = [
            # (table, column_name, ddl_fragment)
            ("signal_history", "data_completeness", "REAL"),
            ("signal_history", "is_stale", "INTEGER DEFAULT 0"),
            ("signal_history", "vr_macd", "REAL"),
            ("signal_history", "engine_version", "TEXT"),
            ("financials_cumulative", "publish_date", "TEXT"),
            ("financials_quarterly_derived", "publish_date", "TEXT"),
            # FinMind 單季財報原本只有 quarter-end 的 date 欄、無公告日 → backtest 用 date 過濾會
            # 在 Q4 報表「公告前 3 個月」就看到 → look-ahead bias。新增 publish_date，
            # _migrate_financials_publish_date 依日期 (Q1/Q2/Q3/Q4) 套法定下限回填。
            ("financials", "publish_date", "TEXT"),
            # last_seen_date：每次 daily_price 抓到該 sid 就更新；
            # 用於 backtest survivorship 防呆（之前 universe 是固定 yaml，回測不會排除已下市股票）
            ("stock_info", "last_seen_date", "TEXT"),
            # Trade journal：entry_reason 記「為什麼買這檔」；tags 逗號分隔（"短線強勢,法人連買"），
            # 給 /api/portfolio/journal-stats 算每個 tag 的勝率。retroactive 加最有效，預設 NULL。
            ("trade_log", "entry_reason", "TEXT"),
            ("trade_log", "tags", "TEXT"),
            # IC 95% bootstrap CI（從 2026-04-30 起加，舊 cache 會在下次計算時被填上）
            ("factor_ic_cache", "ic_ci_lo", "REAL"),
            ("factor_ic_cache", "ic_ci_hi", "REAL"),
        ]
        for table, col, ddl in migrations:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

    @staticmethod
    def _migrate_stock_info_is_tradable(conn: sqlite3.Connection) -> None:
        """補上 stock_info.is_tradable 欄位（1=可交易，0=權證/牛熊證等衍生商品），
        並依 `app/scoring/radar.py:_STOCK_PATTERN` 回填現有資料。

        為什麼放在 DB 層而不是查詢時 regex：
        - SQLite 預設沒有 REGEXP，每次掃描都要 Python callback 太慢；
        - 雷達 / 自選 / 詳情每處都要過濾衍生商品時，重複實作易漏；
        - 改成預先標記 is_tradable，list_candidate_stocks 直接 WHERE is_tradable=1。

        為什麼每次都全表掃：
        - SQLite `ALTER TABLE ADD COLUMN ... DEFAULT 1` 會把舊 row 全填 1（不是 NULL）；
        - market_updater.py 的 UPSERT 沒帶 is_tradable，新插入的權證 row 也會走 DEFAULT 1；
        - 用 `WHERE is_tradable IS NULL` 的增量法會永遠查不到任何 row、永遠不重算。
        - 全表掃對 25k row 很快（< 50ms），idempotent 安全。
        """
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='stock_info'"
            ).fetchall()}
            if "stock_info" not in tables:
                return
            cols = {row[1] for row in conn.execute("PRAGMA table_info(stock_info)").fetchall()}
            if "is_tradable" not in cols:
                conn.execute("ALTER TABLE stock_info ADD COLUMN is_tradable INTEGER DEFAULT 1")
            from app.scoring.radar import _STOCK_PATTERN  # 共用同一份正則
            # 全表掃，重算每 row 的正確值，再只 UPDATE 那些目前 column 值跟正則結果不一致的，
            # 確保 (a) ADD COLUMN DEFAULT 1 留下的舊權證 row、(b) market_updater UPSERT 新插的
            # 權證 row 都會被歸零。
            rows = conn.execute("SELECT stock_id, is_tradable FROM stock_info").fetchall()
            updates = []
            for r in rows:
                sid = r[0]
                current = r[1]
                expected = 1 if _STOCK_PATTERN.match(sid or "") else 0
                if current != expected:
                    updates.append((expected, sid))
            if updates:
                conn.executemany(
                    "UPDATE stock_info SET is_tradable=? WHERE stock_id=?",
                    updates,
                )
                logger.info("stock_info: backfilled is_tradable for %d rows", len(updates))
        except sqlite3.Error as e:
            logger.warning("stock_info.is_tradable migration skipped: %s", e)

    @staticmethod
    def _migrate_stock_info_last_seen_date(conn: sqlite3.Connection) -> None:
        """從 daily_price 回填 stock_info.last_seen_date（每檔最後一筆 OHLCV 的日期）。

        idempotent：只更新 last_seen_date 比現有 daily_price MAX(date) 舊的 row。
        新插入會由 market_updater 的 UPSERT 自動帶 last_seen_date，這個 migration 主要
        給「現有 DB 第一次升級到帶此欄位的版本」用。
        """
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('stock_info','daily_price')"
            ).fetchall()}
            if "stock_info" not in tables or "daily_price" not in tables:
                return
            cols = {row[1] for row in conn.execute("PRAGMA table_info(stock_info)").fetchall()}
            if "last_seen_date" not in cols:
                return  # ALTER TABLE 還沒跑
            cur = conn.execute("""
                UPDATE stock_info
                SET last_seen_date = (
                    SELECT MAX(date) FROM daily_price WHERE daily_price.stock_id = stock_info.stock_id
                )
                WHERE last_seen_date IS NULL
                   OR last_seen_date < (
                       SELECT MAX(date) FROM daily_price WHERE daily_price.stock_id = stock_info.stock_id
                   )
            """)
            if cur.rowcount > 0:
                logger.info("stock_info: backfilled last_seen_date for %d rows", cur.rowcount)
        except sqlite3.Error as e:
            logger.warning("stock_info.last_seen_date migration skipped: %s", e)

    @staticmethod
    def _migrate_financials_cumulative_publish_date(conn: sqlite3.Connection) -> None:
        """回填 financials_cumulative / financials_quarterly_derived 的 publish_date。

        對舊版而言，row.date = 季末日（Q1=03-31、Q2=06-30、Q3=09-30、Q4=12-31）。實際公告下限：
            Q1 → 該年 05-15
            Q2 → 該年 08-14
            Q3 → 該年 11-14
            Q4 → 次年 03-31
        舊版 SQL `WHERE date BETWEEN ? AND ?` 用 quarter-end 過濾，會讓 backtest 在公告日前 6 週
        就「看到」當季財報 → look-ahead bias。新版 reading 路徑改用 publish_date <= as_of。

        idempotent：本 migration 只填 publish_date IS NULL 的列；後續寫入 fetcher 會直接帶 publish_date。
        """
        for table in ("financials_cumulative", "financials_quarterly_derived"):
            try:
                tables = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,),
                ).fetchall()}
                if table not in tables:
                    continue
                cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if "publish_date" not in cols:
                    continue  # _migrate_add_columns 還沒跑或 schema 異常
                rows = conn.execute(
                    f"SELECT rowid, year, quarter FROM {table} "
                    f"WHERE publish_date IS NULL AND quarter IN (1,2,3,4) AND year IS NOT NULL"
                ).fetchall()
                updates: list[tuple[str, int]] = []
                for r in rows:
                    try:
                        year_ce = int(r["year"])
                        quarter = int(r["quarter"])
                    except (TypeError, ValueError):
                        continue
                    pub = quarter_publish_date(year_ce, quarter)
                    if pub:
                        updates.append((pub, int(r["rowid"])))
                if updates:
                    conn.executemany(
                        f"UPDATE {table} SET publish_date=? WHERE rowid=?",
                        updates,
                    )
                    logger.info("%s: backfilled publish_date for %d rows", table, len(updates))
                # 欄位確定存在後才能建 index（在 SCHEMA 階段建會撞舊 DB 的 OperationalError）
                idx_name = "idx_fin_cum_publish" if table == "financials_cumulative" else "idx_fin_qd_publish"
                conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}(publish_date)")
            except sqlite3.Error as e:
                logger.warning("%s.publish_date migration skipped: %s", table, e)

    @staticmethod
    def _migrate_financials_publish_date(conn: sqlite3.Connection) -> None:
        """回填 `financials`（FinMind 單季）的 publish_date。

        FinMind 表的 schema = (date, stock_id, type, value, origin_name)，date = 季末日：
            03-31 → Q1, 06-30 → Q2, 09-30 → Q3, 12-31 → Q4
        套法定下限：Q1=該年 05-15、Q2=該年 08-14、Q3=該年 11-14、Q4=次年 03-31。
        idempotent：只填 publish_date IS NULL 的列。
        """
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='financials'"
            ).fetchall()}
            if "financials" not in tables:
                return
            cols = {row[1] for row in conn.execute("PRAGMA table_info(financials)").fetchall()}
            if "publish_date" not in cols:
                return  # _migrate_add_columns 還沒跑或 schema 異常
            rows = conn.execute(
                "SELECT rowid, date FROM financials "
                "WHERE publish_date IS NULL "
                "AND substr(date, 6, 5) IN ('03-31','06-30','09-30','12-31')"
            ).fetchall()
            updates: list[tuple[str, int]] = []
            for r in rows:
                pub = quarter_end_to_publish_date(r["date"])
                if pub:
                    updates.append((pub, int(r["rowid"])))
            if updates:
                conn.executemany(
                    "UPDATE financials SET publish_date=? WHERE rowid=?",
                    updates,
                )
                logger.info("financials: backfilled publish_date for %d rows", len(updates))
            conn.execute("CREATE INDEX IF NOT EXISTS idx_financials_publish ON financials(publish_date)")
        except sqlite3.Error as e:
            logger.warning("financials.publish_date migration skipped: %s", e)

    @staticmethod
    def _migrate_monthly_revenue_publish_date(conn: sqlite3.Connection) -> None:
        """一次性：把舊版「次月 1 號」的月營收 publish date 推到「次月 10 號」（實際公告下限）。

        舊版 `_publish_date` 寫成次月 1 日，使 backtest / radar 在每月 1~10 號之間誤
        把當月還沒公告的 YoY 視為可用，造成 look-ahead bias。新版本已改成次月 10 日，
        本 migration 把舊資料一併推進。

        判斷：date 為當月 1 號（DAY=01），且 monthly_revenue 表存在 → 推 +9 天。
        idempotent：第二次跑時 DAY=10 不再符合條件，不會重複偏移。
        若有外部寫入用「真實公告日」而非月初固定值，這個推 9 天會誤動，但目前 codebase
        裡所有寫入都走 `_publish_date`，所以安全。
        """
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='monthly_revenue'"
            ).fetchall()}
            if "monthly_revenue" not in tables:
                return
            # 只動 DAY=01 的舊 row
            cur = conn.execute(
                "UPDATE monthly_revenue SET date=date(date,'+9 days') "
                "WHERE strftime('%d', date)='01'"
            )
            if cur.rowcount > 0:
                logger.info("monthly_revenue: shifted %d publish dates from DAY=01 to DAY=10", cur.rowcount)
        except sqlite3.Error as e:
            logger.warning("monthly_revenue publish_date migration skipped: %s", e)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        # synchronous=NORMAL 配合 WAL，在單機自用工具是安全且顯著快的設定
        conn.execute("PRAGMA synchronous=NORMAL")
        # busy_timeout：多 writer 並發（FastAPI + market_update + ensure_fresh）
        # 同時拿 reserved lock 時最多等 5 秒，避免立刻 OperationalError
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
        finally:
            conn.close()

    # ---------- 通用 upsert ----------
    def upsert_df(self, df: pd.DataFrame, table: str) -> int:
        """Bulk INSERT OR REPLACE via executemany — 直接寫進目標表，不繞 tmp 表。

        舊版（保留作 fallback 給特殊情境）：to_sql 寫 tmp 表 → SELECT 灌進目標 → DROP tmp。
        三段 SQL 中間有 commit 隱式發生、tmp 表的 schema 推斷可能跟目標表不一致（pandas
        把 INTEGER 推成 REAL 之類），且每次都要 CREATE/DROP tmp 表，幾千列以下 round-trip
        成本大於實際資料寫入。

        新版用 `executemany("INSERT OR REPLACE ...", rows)`：
        - 一次連線、一個 transaction（with conn 自動 commit / rollback）
        - 沒 tmp 表 → 沒 schema 推斷 → 沒 DROP TABLE 副作用
        - executemany 內部會用 prepared statement，~3000 列 < 50ms。
        """
        if df.empty:
            return 0
        df = df.copy()
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        cols = list(df.columns)
        col_list = ", ".join(cols)
        placeholders = ", ".join(["?"] * len(cols))
        sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
        # 把 NaN/NaT 換成 None（SQLite 拒絕 NaN，且 NaT 是 datetime 的 NaT，必須 None）
        rows = [
            tuple(None if (v is pd.NaT or (isinstance(v, float) and v != v)) else v
                  for v in row)
            for row in df.itertuples(index=False, name=None)
        ]
        with self.connect() as conn:
            with conn:  # context manager 自動 commit / 失敗 rollback
                conn.executemany(sql, rows)
        return len(df)

    # ---------- fetch log ----------
    def get_last_fetch_date(self, stock_id: str, dataset: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT last_date FROM fetch_log WHERE stock_id=? AND dataset=?",
                (stock_id, dataset),
            ).fetchone()
        return row["last_date"] if row else None

    def set_last_fetch_date(self, stock_id: str, dataset: str, last_date: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO fetch_log (stock_id, dataset, last_date, last_run)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(stock_id, dataset) DO UPDATE SET
                    last_date = excluded.last_date,
                    last_run = CURRENT_TIMESTAMP
                """,
                (stock_id, dataset, last_date),
            )
            conn.commit()

    # ---------- 常用查詢 ----------
    def load_daily_price(self, stock_id: str, start: str | None = None) -> pd.DataFrame:
        query = "SELECT * FROM daily_price WHERE stock_id = ?"
        params: list = [stock_id]
        if start:
            query += " AND date >= ?"
            params.append(start)
        query += " ORDER BY date"
        with self.connect() as conn:
            df = pd.read_sql_query(query, conn, params=params)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
        return df

    def load_institutional(
        self,
        stock_id: str,
        start: str | None = None,
        *,
        as_of: str | None = None,
    ) -> pd.DataFrame:
        """institutional 三大法人買賣超。
        as_of：歷史重播時提供，SQL 直接 trim 到 as_of 之前；不提供時拉全量。
        在 SQL 端 trim 比之前在 Python 端 reset_index 來得快（10 年資料 vs 1 段）。
        """
        query = "SELECT * FROM institutional WHERE stock_id = ?"
        params: list = [stock_id]
        if start:
            query += " AND date >= ?"
            params.append(start)
        if as_of:
            query += " AND date <= ?"
            params.append(as_of)
        query += " ORDER BY date"
        with self.connect() as conn:
            df = pd.read_sql_query(query, conn, params=params)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
        return df
