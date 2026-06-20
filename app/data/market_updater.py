"""市場級資料更新：用 TWSE + TPEx 官方 Open Data，每天一次全市場抓取。

每日流程（7 個 HTTP requests，TWSE 4 + TPEx 3）：
- TWSE：OHLCV+價格指數（共用一個 MI_INDEX 請求）、institutional、margin、PER/PBR
- TPEx：OHLCV、institutional、margin、PER/PBR（TPEx 無價格指數）

抓取分兩階段（見 fetch_one_date）：
- Phase 1（並行 I/O）：TWSE 組與 TPEx 組是獨立 host、rate-limit 互不相干，用兩條
  執行緒並行抓（各用自己的 requests.Session）。
- Phase 2（序列 DB）：合併、過濾權證、upsert，全部序列做（不並發寫 SQLite）。

也負責：
- 更新 stock_info（以 OHLCV 裡的代號/名稱為準）
- 記錄每個 dataset 最後抓到哪天
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Iterable

import pandas as pd

from app.data.clock import taipei_today
from app.data.db import Database
from app.data.tpex_fetcher import TpexFetcher, TpexError
from app.data.trading_calendar import is_trading_day
from app.data.twse_fetcher import TwseFetcher, TwseError

logger = logging.getLogger(__name__)


class MarketUpdater:
    def __init__(self, db: Database, request_delay: float = 1.0):
        self.db = db
        self.twse = TwseFetcher(request_delay=request_delay)
        self.tpex = TpexFetcher(request_delay=request_delay)

    # ======================================================================
    # 單日抓取 + 寫入
    # ======================================================================
    def fetch_one_date(self, date_ymd: str) -> dict[str, int]:
        """抓取單一日期，兩個市場共 8 個端點。回傳每個 dataset 寫入的筆數。"""
        results: dict[str, int] = {}

        # Phase 1（並行 I/O）：TWSE 與 TPEx 是各自獨立的 host、rate-limit 互不相干 → 兩組
        # HTTP 並行抓。每個 fetcher 用自己的 requests.Session、各跑在自己的執行緒、不共用
        # 連線 → thread-safe；所有 DB 寫入留到 Phase 2 序列做（不並發寫 SQLite）。
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="market") as ex:
            fut_twse = ex.submit(self._fetch_twse_bundle, date_ymd)
            fut_tpex = ex.submit(self._fetch_tpex_bundle, date_ymd)
            twse_b = fut_twse.result()
            tpex_b = fut_tpex.result()

        # Phase 2（序列 DB 寫入）：以下沿用原邏輯，只是資料改從已抓好的 bundle 取。
        twse_ohlcv, indices, tpex_ohlcv = twse_b["ohlcv"], twse_b["indices"], tpex_b["ohlcv"]

        if not indices.empty:
            results["index_daily"] = self.db.upsert_df(indices, "index_daily")

        price_frames: list[pd.DataFrame] = []
        info_frames: list[pd.DataFrame] = []
        for df, market in [(twse_ohlcv, "twse"), (tpex_ohlcv, "tpex")]:
            if df.empty:
                continue
            # 拆出 stock_info
            info = df[["stock_id", "stock_name"]].drop_duplicates("stock_id").copy()
            info["type"] = market
            info_frames.append(info)
            # daily_price 丟掉 stock_name
            price_frames.append(df.drop(columns=["stock_name"]))

        # 「可交易」白名單：TWSE/TPEX OpenAPI 的 ALLBUT0999 端點會把權證 / 牛熊證 (5 碼)
        # 一併回傳，原本只在 stock_info.is_tradable 標 0、daily_price/institutional/margin/per_pbr
        # 卻照寫不誤 → DB 變肥（權證 row 占 daily_price 82%、institutional 87%）。
        # 這裡先在 stock_info 標好 is_tradable，再拿同一份 stock_id 集合過濾下游 4 張表。
        trading_ids: set[str] | None = None

        if info_frames:
            info_all = pd.concat(info_frames, ignore_index=True).drop_duplicates("stock_id")
            # 用 UPSERT 更新 stock_name / type / is_tradable / last_seen_date
            # last_seen_date 等於這天 OHLCV 抓到的日期（YYYY-MM-DD）；
            # 已下市的股票不會再被 OHLCV 包含 → last_seen_date 不會推進 → 將來 backtest
            # 可用 `WHERE COALESCE(last_seen_date, '9999') >= test_end` 排除 survivorship 偏誤。
            from app.scoring.radar import _STOCK_PATTERN  # 共用同一份正則
            trading_date = f"{date_ymd[:4]}-{date_ymd[4:6]}-{date_ymd[6:8]}"
            with self.db.connect() as conn:
                conn.executemany(
                    """
                    INSERT INTO stock_info (stock_id, stock_name, type, is_tradable, last_seen_date)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(stock_id) DO UPDATE SET
                        stock_name = excluded.stock_name,
                        type = excluded.type,
                        is_tradable = excluded.is_tradable,
                        last_seen_date = MAX(COALESCE(stock_info.last_seen_date, '0000-00-00'), excluded.last_seen_date),
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    [
                        (
                            r["stock_id"],
                            r["stock_name"],
                            r["type"],
                            1 if _STOCK_PATTERN.match(str(r["stock_id"]) or "") else 0,
                            trading_date,
                        )
                        for _, r in info_all.iterrows()
                    ],
                )
                conn.commit()
            trading_ids = {
                sid for sid in info_all["stock_id"].astype(str)
                if _STOCK_PATTERN.match(sid)
            }

        def _filter_tradable(df: pd.DataFrame) -> pd.DataFrame:
            """把非可交易股票（權證 / 牛熊證等）的列剔除。trading_ids 為 None 時 (info 沒抓到)
            走保守路徑：直接放行，避免破壞「OHLCV 失敗但其他 dataset 還是能寫」的退化情境。"""
            if trading_ids is None or df.empty or "stock_id" not in df.columns:
                return df
            mask = df["stock_id"].astype(str).isin(trading_ids)
            dropped = len(df) - int(mask.sum())
            if dropped:
                logger.debug("filtered out %d non-tradable rows", dropped)
            return df[mask].reset_index(drop=True)

        if price_frames:
            price_all = _filter_tradable(pd.concat(price_frames, ignore_index=True))
            if not price_all.empty:
                results["daily_price"] = self.db.upsert_df(price_all, "daily_price")

        # 其他三個 dataset（HTTP 已在 Phase 1 並行抓完，這裡只合併 + 過濾 + 寫入）
        for key in ("institutional", "margin", "per_pbr"):
            frames = [b[key] for b in (twse_b, tpex_b) if not b[key].empty]
            if frames:
                combined = _filter_tradable(pd.concat(frames, ignore_index=True))
                if not combined.empty:
                    results[key] = self.db.upsert_df(combined, key)

        return results

    def _safe(self, fn, *args, label: str = "") -> pd.DataFrame:
        try:
            return fn(*args)
        except (TwseError, TpexError) as e:
            logger.warning("%s 失敗: %s", label, e)
            return pd.DataFrame()

    def _safe_pair(self, fn, *args, label: str = "") -> tuple[pd.DataFrame, pd.DataFrame]:
        """同 _safe，但對應「一次回傳兩個 DataFrame」的 fetcher（如 daily_ohlcv_and_indices）。"""
        try:
            return fn(*args)
        except (TwseError, TpexError) as e:
            logger.warning("%s 失敗: %s", label, e)
            return pd.DataFrame(), pd.DataFrame()

    # ------------------------------------------------------------------
    # 單一市場的全部端點（純 I/O，無 DB 副作用）→ 供 Phase 1 並行抓
    # ------------------------------------------------------------------
    def _fetch_twse_bundle(self, date_ymd: str) -> dict[str, pd.DataFrame]:
        ohlcv, indices = self._safe_pair(
            self.twse.daily_ohlcv_and_indices, date_ymd, label="TWSE OHLCV+指數",
        )
        return {
            "ohlcv": ohlcv,
            "indices": indices,
            "institutional": self._safe(self.twse.institutional, date_ymd, label="twse institutional"),
            "margin": self._safe(self.twse.margin, date_ymd, label="twse margin"),
            "per_pbr": self._safe(self.twse.per_pbr, date_ymd, label="twse per_pbr"),
        }

    def _fetch_tpex_bundle(self, date_ymd: str) -> dict[str, pd.DataFrame]:
        return {
            "ohlcv": self._safe(self.tpex.daily_ohlcv, date_ymd, label="TPEx OHLCV"),
            "institutional": self._safe(self.tpex.institutional, date_ymd, label="tpex institutional"),
            "margin": self._safe(self.tpex.margin, date_ymd, label="tpex margin"),
            "per_pbr": self._safe(self.tpex.per_pbr, date_ymd, label="tpex per_pbr"),
        }

    # ======================================================================
    # 日期範圍批次
    # ======================================================================
    def fetch_date_range(
        self,
        start: str | date,
        end: str | date,
        skip_weekends: bool = True,
        update_progress: bool = False,
    ) -> None:
        """抓取日期範圍（含端點）。週末與已知休市日（trading_calendar）直接跳過不發 HTTP。

        為什麼要看 trading_calendar：以前只跳週末，5/1 / 端午 / 中秋 等國定假日仍會打 8 個
        HTTP request 拿空表，且 fetch_log 不會推進（res 是空 dict）→ 隔天 incremental
        從同一個 4/30 起點重來，每次都白打 5/1。

        update_progress：是否寫 fetch_log。
            - True：給 update_incremental 用（連續增量、進度指標的真實來源）
            - False（預設）：給 --date / --from..--to / --days 這類「補洞」場景用
              這些都不是線性進度，舊行為會把 fetch_log 拖回中間某天 → 下次 daily-update 會
              從那天起重抓 N 個月。實際 case：cherry-pick 跑 --date 2025-02-05 把 fetch_log
              改寫成 2/5，隔天 daily-update 就從 2/6 開始往今天爬一年多。
        """
        if isinstance(start, str):
            start = datetime.strptime(start, "%Y-%m-%d").date()
        if isinstance(end, str):
            end = datetime.strptime(end, "%Y-%m-%d").date()

        d = start
        n_days = 0
        while d <= end:
            if skip_weekends and d.weekday() >= 5:
                d += timedelta(days=1)
                continue
            if not is_trading_day(d, self.db):
                logger.info("跳過 %s（非交易日 / 休市）", d.isoformat())
                d += timedelta(days=1)
                continue
            date_ymd = d.strftime("%Y%m%d")
            logger.info("抓取 %s", d.isoformat())
            res = self.fetch_one_date(date_ymd)
            if res:
                logger.info("  %s", ", ".join(f"{k}={v}" for k, v in res.items()))
            else:
                logger.info("  （無資料）")
            if update_progress:
                # 更新 fetch_log：每個 table 各記一次 last_date
                for table in res:
                    self.db.set_last_fetch_date("__market__", table, d.isoformat())
            n_days += 1
            d += timedelta(days=1)
        logger.info("完成 %d 個日期", n_days)

    # ======================================================================
    # 從 last_fetch_date 自動續抓至今
    # ======================================================================
    # 四個核心 dataset 的 fetch_log 進度都要看齊 — 避免「早上 OHLCV 發佈時跑一次、
    # 但三大法人/融資/PER 那時還沒出」造成 daily_price 搶跑、其他 dataset 永遠補不回來。
    _TRACKED_TABLES = ("daily_price", "institutional", "margin", "per_pbr")

    def update_incremental(self, default_start: str, today: date | None = None) -> None:
        # 雲端 server 跑 UTC 時，凌晨會把昨天當前日 → fetch_log 寫錯，後續 incremental 起點落後一天
        today = today or taipei_today()

        # 取各 dataset 的 last_date，以「最舊的那個」當整體進度指標。
        # 因為 upsert 是 idempotent（INSERT OR REPLACE），重抓已有的日期不會有副作用。
        last_pairs = [
            (t, self.db.get_last_fetch_date("__market__", t))
            for t in self._TRACKED_TABLES
        ]
        last_pairs = [(t, d) for t, d in last_pairs if d]

        if last_pairs:
            laggard_table, earliest = min(last_pairs, key=lambda x: x[1])
            start = datetime.strptime(earliest, "%Y-%m-%d").date() + timedelta(days=1)
            if laggard_table != "daily_price":
                logger.info(
                    "%s 落後（last_date=%s），將從 %s 起重抓以補齊所有 dataset",
                    laggard_table, earliest, start,
                )
        else:
            start = datetime.strptime(default_start, "%Y-%m-%d").date()

        if start > today:
            logger.info("已經是最新")
            return
        # update_progress=True：這條是線性增量，要把 fetch_log 推進當作下次的起點
        self.fetch_date_range(start, today, update_progress=True)
