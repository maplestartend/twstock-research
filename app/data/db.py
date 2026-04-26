from __future__ import annotations

import logging
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd

logger = logging.getLogger(__name__)


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS stock_info (
        stock_id TEXT PRIMARY KEY,
        stock_name TEXT,
        industry_category TEXT,
        type TEXT,
        is_tradable INTEGER DEFAULT 1,
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
        PRIMARY KEY (stock_id, date, type)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fin_cum_date ON financials_cumulative(date)",
    "CREATE INDEX IF NOT EXISTS idx_fin_cum_stock ON financials_cumulative(stock_id)",
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
        PRIMARY KEY (stock_id, date, type)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fin_qd_date ON financials_quarterly_derived(date)",
    "CREATE INDEX IF NOT EXISTS idx_fin_qd_stock ON financials_quarterly_derived(stock_id)",
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
        recommendation TEXT,
        strategies TEXT,
        data_completeness REAL,
        is_stale INTEGER DEFAULT 0,
        PRIMARY KEY (as_of, stock_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_signal_asof ON signal_history(as_of)",
    "CREATE INDEX IF NOT EXISTS idx_signal_stock ON signal_history(stock_id)",
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
            conn.commit()

    @staticmethod
    def _migrate_add_columns(conn: sqlite3.Connection) -> None:
        """對舊資料庫補上缺少的欄位；SQLite 的 ALTER TABLE ADD COLUMN 無 IF NOT EXISTS，
        只能先查 PRAGMA table_info 再決定是否 ADD。"""
        migrations: list[tuple[str, str, str]] = [
            # (table, column_name, ddl_fragment)
            ("signal_history", "data_completeness", "REAL"),
            ("signal_history", "is_stale", "INTEGER DEFAULT 0"),
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
        """Bulk INSERT OR REPLACE。

        舊版用固定名 `_tmp_upsert` 暫存表，多 writer 並發時（FastAPI thread + market_update script）
        會互相覆蓋對方的暫存資料，且 to_sql 隱式 commit 把整個操作切成 3 段：
          1. CREATE/REPLACE _tmp_upsert（commit 1）
          2. INSERT OR REPLACE FROM _tmp_upsert（commit 2）
          3. DROP TABLE _tmp_upsert（commit 3）
        中間任一段失敗會留下殘表干擾下一個呼叫者。
        新版改用 uuid 後綴的 tmp 表 + try/finally 清理，並把整段包進 `with conn:` 確保
        unhandled exception 自動 rollback（不留殘表）。
        """
        if df.empty:
            return 0
        df = df.copy()
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        # 8 hex 字符夠唯一，且 SQLite identifier 字元限制不踩雷
        tmp_table = f"_tmp_upsert_{uuid.uuid4().hex[:8]}"
        with self.connect() as conn:
            try:
                df.to_sql(tmp_table, conn, if_exists="replace", index=False)
                cols = list(df.columns)
                col_list = ", ".join(cols)
                # `with conn:` 會在離開 block 時 commit / 失敗則 rollback
                with conn:
                    conn.execute(
                        f"INSERT OR REPLACE INTO {table} ({col_list}) "
                        f"SELECT {col_list} FROM {tmp_table}"
                    )
            finally:
                # 清理 tmp 表（即使 INSERT 失敗也要清，避免留殘表）
                try:
                    conn.execute(f"DROP TABLE IF EXISTS {tmp_table}")
                    conn.commit()
                except Exception:
                    pass
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

    def load_institutional(self, stock_id: str, start: str | None = None) -> pd.DataFrame:
        query = "SELECT * FROM institutional WHERE stock_id = ?"
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
