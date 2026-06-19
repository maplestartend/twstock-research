"""雷達：掃描 DB 內所有股票，套用評分，依照不同策略篩選候選名單。

效能設計：批次載入全市場歷史資料到記憶體，而非每支股票 5 次 DB 查詢。
對 ~2300 檔（上市+上櫃，is_tradable=1，排除權證/ETN）掃描 ~20-30 秒。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import pandas as pd

from app.data.adjuster import read_ohlc_with_adj
from app.data.clock import taipei_today
from app.data.db import Database
from app.indicators import chips as chip_ind
from app.indicators import fundamentals as fund_ind
from app.indicators import market_context as mkt_ctx
from app.indicators import technical as tech
from app.scoring import engine as eng

logger = logging.getLogger(__name__)


import re

# 允許的股票代號模式：
#   4 位數字        -> 一般股票 (2330)
#   4 位數字 + 字母 -> 特別股 (2881A)
#   00xxx / 00xxxB  -> ETF / 債券 ETF
# 排除：5 位數 (權證/牛熊證)、6 位數 03/07 開頭等衍生性商品
_STOCK_PATTERN = re.compile(r"^(\d{4}[A-Z]?|00\d{2,4}[A-Z]?)$")


def _is_stock(stock_id: str) -> bool:
    return bool(_STOCK_PATTERN.match(stock_id or ""))


def list_candidate_stocks(db: Database, min_days: int = 60) -> list[tuple[str, str]]:
    """回傳 DB 中有足夠日線資料的「一般股票/ETF」列表（排除權證等衍生商品）。

    雙層過濾（belt + suspenders）：
    1. `stock_info.is_tradable=0` 的直接排除（migration 已標記、SQL 層快）；
    2. **不論 is_tradable 是什麼**，仍跑 `_is_stock` regex 二次驗證。
       為什麼：`market_updater.py` 的 UPSERT 沒帶 is_tradable 欄位，新插入的權證
       row 會吃 schema 的 `DEFAULT 1`，若 migration 沒及時 backfill 就會漏網。
       多一道 Python regex 對 ~25k row 只要幾 ms，但能保證權證永不外洩。
    """
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT p.stock_id,
                   COALESCE(i.stock_name, p.stock_id) AS stock_name,
                   i.is_tradable AS is_tradable,
                   COUNT(*) AS n
            FROM daily_price p
            LEFT JOIN stock_info i ON i.stock_id = p.stock_id
            GROUP BY p.stock_id
            HAVING n >= {int(min_days)}
            ORDER BY p.stock_id
            """
        ).fetchall()
    out: list[tuple[str, str]] = []
    for r in rows:
        sid = r["stock_id"]
        is_tradable = r["is_tradable"]
        if is_tradable == 0:
            continue  # 已明確標記為不可交易
        if not _is_stock(sid):
            continue  # 第二道防線：regex 兜底
        out.append((sid, r["stock_name"]))
    return out


def _load_industry_map(db: Database) -> dict[str, str | None]:
    """回傳 stock_id -> industry_category。沒有 industry_category 就 None。"""
    with db.connect() as conn:
        rows = conn.execute("SELECT stock_id, industry_category FROM stock_info").fetchall()
    return {r["stock_id"]: r["industry_category"] for r in rows}


def _industry_yield_z_map(per_all: pd.DataFrame, industry_by_sid: dict[str, str | None]) -> dict[str, float]:
    """預先計算 stock_id -> 同產業殖利率 z-score。

    為什麼：殖利率天花板有產業別差異（公用事業 ~ 5%、科技股 ~ 1%）。
    用絕對閾值會讓殖利率高的產業全部得高分、殖利率低的產業全部得低分，跨產業不公平。
    用同產業 z-score 評分，高出產業平均 1σ 的就算前段班，跟產業類型無關。

    Z-score 數學共用 `engine.industry_yield_z_from_yields`（避免 detail page vs radar drift）。
    產業內樣本數 < 4 / stdev≈0 → 整個產業跳過。
    """
    if per_all.empty or "dividend_yield" not in per_all.columns:
        return {}
    latest = per_all.sort_values("date").groupby("stock_id").tail(1)
    sid_to_yield: dict[str, float] = {}
    for _, r in latest.iterrows():
        sid = r["stock_id"]
        y = r["dividend_yield"]
        if y is None or pd.isna(y):
            continue
        sid_to_yield[sid] = float(y)

    by_industry: dict[str, dict[str, float]] = {}
    for sid, y in sid_to_yield.items():
        ind = industry_by_sid.get(sid)
        if not ind:
            continue
        by_industry.setdefault(ind, {})[sid] = y

    result: dict[str, float] = {}
    for yields_in_ind in by_industry.values():
        result.update(eng.industry_yield_z_from_yields(yields_in_ind))
    return result


def _load_latest_yield_all(db: Database, since: str, until: str) -> pd.DataFrame:
    """全市場每檔最新一筆殖利率（[stock_id, date, dividend_yield]）。

    給「盤中即時重算一頁」用：此時 per_pbr 只載了該頁的股票，但同產業殖利率 z-score 是跨股票
    聚合，必須看全市場才不偏。視窗與 score_all 的 per_since 對齊、取 latest-per-stock，
    與全市場快照用 `_bulk_load('per_pbr', per_since).tail(1)` 抽出的最新殖利率等價。
    """
    with db.connect() as conn:
        return pd.read_sql_query(
            """
            SELECT stock_id, date, dividend_yield FROM (
                SELECT stock_id, date, dividend_yield,
                       ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
                FROM per_pbr
                WHERE date BETWEEN ? AND ?
            ) WHERE rn = 1
            """, conn, params=[since, until],
        )


def _bulk_load(
    db: Database,
    table: str,
    since: str,
    until: str | None = None,
    date_col: str = "date",
    *,
    stock_ids: list[str] | None = None,
) -> pd.DataFrame:
    """批次載入單表的時間窗。

    stock_ids: 提供 → 額外 `WHERE stock_id IN (...)`，給「盤中即時重算一頁」只載少數股票用，
    避免全市場掃描。空 list 明確回空集合（不退化成全掃）。
    """
    where = f"{date_col} >= ?" if until is None else f"{date_col} BETWEEN ? AND ?"
    params: list = [since] if until is None else [since, until]
    if stock_ids is not None:
        if not stock_ids:
            return pd.DataFrame()
        from app.data.sql_utils import make_placeholders
        where += f" AND stock_id IN ({make_placeholders(len(stock_ids))})"
        params.extend(stock_ids)
    with db.connect() as conn:
        df = pd.read_sql_query(
            f"SELECT * FROM {table} WHERE {where}", conn, params=params,
        )
    if not df.empty and date_col in df.columns:
        df[date_col] = pd.to_datetime(df[date_col])
    return df


def score_all(
    db: Database,
    min_days: int = 60,
    limit: int | None = None,
    include_fundamentals: bool = True,
    *,
    as_of: str | date | None = None,
    candidate_stocks: list[tuple[str, str]] | None = None,
    stock_ids: list[str] | None = None,
    live_prices: dict[str, float] | None = None,
) -> pd.DataFrame:
    """對 DB 所有有足夠資料的股票打分。

    include_fundamentals=False 會略過 financials 表（大幅加速，長期分數只用 per_pbr 推估）。

    as_of: 評分基準日（YYYY-MM-DD or date）。預設 None → taipei_today()，所有資料
    上限為今日。**指定 as_of 時所有 DB 查詢都會把 date <= as_of 設為上限**，避免歷史
    回放（snapshot replay / 回測）時把未來資料當成可用（金融分析師審查 Critical #3）。

    stock_ids: 提供 → **所有批次載入都加 `WHERE stock_id IN (...)`**，只載這組股票的歷史，
    把「全市場 ~2300 檔掃 20-30s」縮成「一頁 ≤50 檔掃 ~1s」。給雷達/自選的「盤中即時
    重算當前頁」用。同產業殖利率 z-score 是跨股票聚合，仍以**全市場最新殖利率**計（見下方
    分支），避免只在這 50 檔內算 z 而讓長期分數偏離快照（CLAUDE.md #5 invariant）。

    live_prices: {stock_id: 盤中即時價}。提供 → 對每檔在 enrich 前用 `_override_last_close`
    覆寫最後一根 close（與 score_stock 的 live_price 同機制），短/中期技術分數即反映盤中價；
    長期（ROE/EPS/股利）不受影響。**只在 API 回應層使用、絕不寫入 signal_history**
    （盤中價非 final、會污染回測/IC；同 app/data/intraday.py 的職責邊界）。

    不變式：當 live_prices[sid] == 該檔快照當下的收盤、且 stock_ids 涵蓋該檔時，本函式對該檔
    產出的 short/mid/long/composite 與「stock_ids=None 的全市場快照」逐位元相同
    （override 為 no-op、z 用全市場、視窗一致）。由 tests/test_intraday_score.py 守住。
    """
    if as_of is None:
        as_of_d = taipei_today()
    elif isinstance(as_of, str):
        as_of_d = date.fromisoformat(as_of)
    else:
        as_of_d = as_of
    as_of_str = as_of_d.isoformat()

    # 回填歷史快照時每天都會呼叫 score_all；候選池（有至少 min_days 日線 + 可交易）
    # 在同一輪 backfill 內通常固定不變，允許 caller 預先算好傳入，避免每次都掃 daily_price。
    stocks = candidate_stocks if candidate_stocks is not None else list_candidate_stocks(db, min_days)
    if limit:
        stocks = stocks[:limit]
    if not stocks:
        return pd.DataFrame()

    # 分窗讀取：
    # - 技術指標只需 ma240 + slope/oscillator 暖機，300 天足夠（較原 400 天省 ~25% I/O）
    # - 籌碼只用 streak30/cum20，120 天足夠（較原 400 天省 ~70% I/O）
    # - 估值 per_percentile 維持 400 天，避免估值分位視窗被壓短而改變分數語意
    price_since = (as_of_d - timedelta(days=300)).isoformat()
    chip_since = (as_of_d - timedelta(days=120)).isoformat()
    per_since = (as_of_d - timedelta(days=400)).isoformat()
    # 用 LEFT JOIN 帶上 daily_price_adj，技術指標就能用還原價（除權息/分割不誤導）
    with db.connect() as conn:
        # extra_cols=False 省 turnover/spread（radar 不用），少 ~10% I/O
        # stock_ids 提供時只載這頁的股票（盤中即時重算），否則全市場
        price_all = read_ohlc_with_adj(
            conn, since=price_since, until=as_of_str, extra_cols=False,
            stock_ids=stock_ids,
        )
        # 在還原價覆寫前，用「原始 close」算流動性與當日漲跌（限漲跌停偵測要的是真實價格漲跌，
        # 還原價在除權息/分割日會讓 pct_change 看起來不對 → 漏掉限制鎖死訊號）
        liquidity_by_sid: dict[str, dict[str, float | None]] = {}
        if not price_all.empty:
            for sid, g in price_all.sort_values("date").groupby("stock_id"):
                tail20 = g.tail(20)
                amt_mean = tail20["amount"].mean() if "amount" in tail20.columns else None
                amount_20d = float(amt_mean) if amt_mean is not None and not pd.isna(amt_mean) else None
                pct_change: float | None = None
                if len(g) >= 2:
                    last_close = g.iloc[-1]["close"]
                    prev_close = g.iloc[-2]["close"]
                    if prev_close and not pd.isna(prev_close) and not pd.isna(last_close):
                        pct_change = float((last_close - prev_close) / prev_close)
                liquidity_by_sid[sid] = {
                    "amount_20d": amount_20d,
                    "pct_change_today": pct_change,
                }
        if not price_all.empty:
            price_all["date"] = pd.to_datetime(price_all["date"])
            # 有還原價就替換，沒還原就保留原值
            for col in ("close", "open", "high", "low"):
                adj_col = f"{col}_adj"
                if adj_col in price_all.columns:
                    price_all[col] = price_all[adj_col].fillna(price_all[col])
            price_all = price_all.drop(columns=[c for c in ("close_adj", "open_adj", "high_adj", "low_adj") if c in price_all.columns])
    inst_all = _bulk_load(db, "institutional", chip_since, until=as_of_str, stock_ids=stock_ids)
    margin_all = _bulk_load(db, "margin", chip_since, until=as_of_str, stock_ids=stock_ids)
    per_all = _bulk_load(db, "per_pbr", per_since, until=as_of_str, stock_ids=stock_ids)

    # 若需要基本面，載入所有財報。
    # 視窗 5 年：eps_cagr_3y 需 16 季（tail(4) + iloc[-16:-12]）= 4 年最低，5 年給點 buffer
    # 避免日期邊界導致 16 季差 1 筆就拉空。3 年視窗會讓 score_all 拿不到 cagr，與 score_stock
    # （load 全表）出現「同股、長期分數不同」的 invariant 違反。
    if include_fundamentals:
        fin_since = (as_of_d - timedelta(days=365 * 5)).isoformat()
        # stock_ids 提供時把財報三表也收斂到這頁的股票（盤中即時重算只需這些）
        _fin_in = ""
        _fin_extra: list = []
        if stock_ids is not None:
            from app.data.sql_utils import make_placeholders
            if stock_ids:
                _fin_in = f" AND stock_id IN ({make_placeholders(len(stock_ids))})"
                _fin_extra = list(stock_ids)
            else:
                _fin_in = " AND 1=0"
        with db.connect() as conn:
            # FinMind 單季財報：用 publish_date 過濾避免 backtest 看到尚未公告的季報。
            # COALESCE 給尚未 backfill 的舊 row 兜底（NULL → 用 quarter-end 較保守、會少看到資料）。
            fin_all = pd.read_sql_query(
                "SELECT * FROM financials "
                "WHERE date BETWEEN ? AND ? "
                "  AND COALESCE(publish_date, date) <= ?" + _fin_in,
                conn, params=[fin_since, as_of_str, as_of_str, *_fin_extra],
            )
            # 全市場累計財報（TWSE/TPEX OpenAPI），用於 fundamentals 的 fallback。
            # 用 publish_date 過濾 → 避免 backtest（as_of 在過去時）看到尚未公告的當季財報。
            # COALESCE 是給尚未 backfill / 舊版寫入的 row 兜底（NULL → 用 quarter-end，較保守）。
            fin_cum_all = pd.read_sql_query(
                "SELECT * FROM financials_cumulative "
                "WHERE date BETWEEN ? AND ? "
                "  AND COALESCE(publish_date, date) <= ?" + _fin_in,
                conn, params=[fin_since, as_of_str, as_of_str, *_fin_extra],
            )
            # 累計差分後的單季值，用於 TTM / YoY / ROE 計算（同樣 publish_date 守則）
            fin_derived_all = pd.read_sql_query(
                "SELECT * FROM financials_quarterly_derived "
                "WHERE date BETWEEN ? AND ? "
                "  AND COALESCE(publish_date, date) <= ?" + _fin_in,
                conn, params=[fin_since, as_of_str, as_of_str, *_fin_extra],
            )
        if not fin_all.empty:
            fin_all["date"] = pd.to_datetime(fin_all["date"])
        if not fin_cum_all.empty:
            fin_cum_all["date"] = pd.to_datetime(fin_cum_all["date"])
        if not fin_derived_all.empty:
            fin_derived_all["date"] = pd.to_datetime(fin_derived_all["date"])
    else:
        fin_all = pd.DataFrame(columns=["date", "stock_id", "type", "value"])
        fin_cum_all = pd.DataFrame(columns=["date", "stock_id", "type", "value", "year", "quarter"])
        fin_derived_all = pd.DataFrame(columns=["date", "stock_id", "type", "value", "year", "quarter"])

    # 加權指數（用來算 RS）
    taiex = mkt_ctx.load_taiex_series(db)
    if as_of is not None and not taiex.empty and "date" in taiex.columns:
        # 歷史回放時 TAIEX 也要切到 as_of 之前
        taiex = taiex[pd.to_datetime(taiex["date"]) <= pd.Timestamp(as_of_d)].reset_index(drop=True)

    # 月營收（最新月全市場 + 近 12 個月 YoY 用於「連續 N 月 YoY」判斷）
    rev_lower = (as_of_d - timedelta(days=400)).isoformat()
    # stock_ids 提供時收斂到這頁的股票（盤中即時重算）
    _rev_in = ""
    _rev_extra: list = []
    if stock_ids is not None:
        from app.data.sql_utils import make_placeholders
        if stock_ids:
            _rev_in = f" AND stock_id IN ({make_placeholders(len(stock_ids))})"
            _rev_extra = list(stock_ids)
        else:
            _rev_in = " AND 1=0"
    with db.connect() as conn:
        # latest 也要設上限，避免歷史回放時取到未來月份。
        # 用 ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) 取代相關子查詢，
        # SQLite 對 correlated subquery 的執行計畫是「每筆 outer row 跑一次」，2700 檔 × 100 月 = 27 萬次掃描；
        # 換成 window function 只需單次 partition scan（同 watchlist.py:210 的寫法）。
        rev_all = pd.read_sql_query(
            f"""
            SELECT stock_id, date, revenue, mom_pct, yoy_pct FROM (
                SELECT stock_id, date, revenue, mom_pct, yoy_pct,
                       ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
                FROM monthly_revenue
                WHERE date <= ?{_rev_in}
            ) WHERE rn = 1
            """, conn, params=[as_of_str, *_rev_extra],
        )
        # 近 12 個月 YoY 歷史（用於算「連續 YoY >0 或 >20% 月數」）
        rev_hist = pd.read_sql_query(
            f"""
            SELECT stock_id, date, yoy_pct FROM monthly_revenue
            WHERE date BETWEEN ? AND ?{_rev_in}
            ORDER BY stock_id, date
            """, conn, params=[rev_lower, as_of_str, *_rev_extra],
        )
    rev_latest = {r["stock_id"]: r for _, r in rev_all.iterrows()} if not rev_all.empty else {}

    # 計算每檔「最近連續 YoY > 0」「連續 YoY > 20%」月數
    # 至少要有 6 個月的有效資料才評估 streak；不足視為資料不足，streak = None
    MIN_REV_MONTHS = 6
    rev_streaks: dict[str, dict] = {}
    if not rev_hist.empty:
        for sid, g in rev_hist.groupby("stock_id"):
            vals = [v for v in g.sort_values("date")["yoy_pct"].tolist() if v is not None and not pd.isna(v)]
            if len(vals) < MIN_REV_MONTHS:
                rev_streaks[sid] = {"rev_yoy_streak_pos": None, "rev_yoy_streak_hot": None}
                continue
            streak_pos = 0
            streak_hot = 0
            for v in reversed(vals):
                if v > 0:
                    streak_pos += 1
                else:
                    break
            for v in reversed(vals):
                if v > 0.20:
                    streak_hot += 1
                else:
                    break
            rev_streaks[sid] = {"rev_yoy_streak_pos": streak_pos, "rev_yoy_streak_hot": streak_hot}

    # 同產業殖利率 z-score 預備：用 per_pbr 全市場最新一筆 + stock_info.industry_category
    industry_by_sid = _load_industry_map(db)
    if stock_ids is not None:
        # 盤中即時重算：per_all 只含這頁的股票，z 必須看全市場最新殖利率才不偏（CLAUDE.md #5）
        yield_z_map = _industry_yield_z_map(
            _load_latest_yield_all(db, per_since, as_of_str), industry_by_sid,
        )
    else:
        yield_z_map = _industry_yield_z_map(per_all, industry_by_sid)

    # v5e #1：偵測本日 regime（一次、不在每檔重算）
    from app.scoring.regime import detect_regime
    regime = detect_regime(db, as_of=as_of_str)

    # group by stock_id
    price_groups = {k: g for k, g in price_all.groupby("stock_id")} if not price_all.empty else {}
    inst_groups = {k: g for k, g in inst_all.groupby("stock_id")} if not inst_all.empty else {}
    margin_groups = {k: g for k, g in margin_all.groupby("stock_id")} if not margin_all.empty else {}
    per_groups = {k: g for k, g in per_all.groupby("stock_id")} if not per_all.empty else {}
    fin_groups = {k: g for k, g in fin_all.groupby("stock_id")} if not fin_all.empty else {}
    fin_cum_groups = {k: g for k, g in fin_cum_all.groupby("stock_id")} if not fin_cum_all.empty else {}
    fin_derived_groups = {k: g for k, g in fin_derived_all.groupby("stock_id")} if not fin_derived_all.empty else {}

    empty = pd.DataFrame()
    rows = []
    for sid, name in stocks:
        price = price_groups.get(sid, empty)
        if price.empty or len(price) < min_days:
            continue
        try:
            # 盤中即時重算：enrich 前覆寫最後一根 close（與 score_stock 的 live_price 同機制）。
            # high/low 同步擴張包住新 close，技術指標（MA/KD/RSI/Bollinger/VR）才反映盤中價。
            if live_prices is not None:
                lp = live_prices.get(sid)
                if lp is not None and lp > 0:
                    price = eng._override_last_close(price, float(lp))
            # 批次掃描用精簡技術欄位，減少全市場 DataFrame 生成成本
            price = tech.enrich_for_scoring(price.copy())
            inst = inst_groups.get(sid, empty)
            margin = margin_groups.get(sid, empty)
            per_pbr = per_groups.get(sid, empty)
            fin = fin_groups.get(sid, empty)

            chip_snap = chip_ind.latest_chip_snapshot(inst, margin)
            # 注入 20 日平均日成交量，給 score_foreign_mid / score_trust_mid 做 % of ADV 規模化評分
            # （金融分析師審查 #6：避免大型權值股與小型股共用絕對張數閾值）
            if "volume" in price.columns and len(price) >= 20:
                avg_vol_20 = float(price["volume"].tail(20).mean())
                if avg_vol_20 > 0:
                    chip_snap["avg_volume_20"] = avg_vol_20
            fin_cum = fin_cum_groups.get(sid, empty)
            fin_derived = fin_derived_groups.get(sid, empty)
            fund_snap = fund_ind.fundamental_snapshot(fin, per_pbr, fin_cum, fin_derived)
            # 注入同產業殖利率 z-score（沒有 industry / 樣本不足會是 None，rubric 自動 fallback 絕對閾值）
            if sid in yield_z_map:
                fund_snap["dividend_yield_z"] = yield_z_map[sid]
            # 注入 industry_category 給 score_revenue_growth / 金融業 cap 用（與 score_stock 對齊）
            ind = industry_by_sid.get(sid)
            if ind:
                fund_snap["industry_category"] = ind

            short = eng.score_short_term(price, chip_snap)
            mid = eng.score_mid_term(price, chip_snap, fund_snap, stock_id=sid)
            long_ = eng.score_long_term(fund_snap, stock_id=sid)
            vr_macd_val = short.parts.get("vr_macd")
            # v5c Wave 2 Phase 2：算 4 個 Style Score 一起寫入 snapshot
            from app.scoring.style import compute_style_scores
            styles = compute_style_scores(short.parts, mid.parts, long_.parts)
            # vr26 原始值給 _strat_vr_macd 做更嚴格的 filter（VR>150）
            last_row = price.iloc[-1]
            vr26_val = float(last_row["vr26"]) if pd.notna(last_row.get("vr26")) else None
            macd_hist_val = float(last_row["macd_hist"]) if pd.notna(last_row.get("macd_hist")) else None

            composite, _comp_usage = eng.composite_score(short.total, mid.total, long_.total, regime=regime)
            data_completeness = eng.overall_completeness(short, mid, long_, regime=regime)
            as_of_str = str(price.iloc[-1]["date"].date())
            is_stale = eng.check_stale(as_of_str)
            rs20 = mkt_ctx.compute_rs(price, taiex, 20)
            rs60 = mkt_ctx.compute_rs(price, taiex, 60)
            rev_mom = rev_latest.get(sid, {}).get("mom_pct") if rev_latest else None
            rev_yoy_m = rev_latest.get(sid, {}).get("yoy_pct") if rev_latest else None
            rev_streak = rev_streaks.get(sid, {})
            rows.append({
                "stock_id": sid,
                "stock_name": name,
                "close": float(price.iloc[-1]["close"]),
                "short": short.total,
                "mid": mid.total,
                "long": long_.total,
                "composite": composite,
                "vr_macd": vr_macd_val,
                "vr26": vr26_val,
                "macd_hist": macd_hist_val,
                "data_completeness": data_completeness,
                "is_stale": is_stale,
                "recommendation": eng.recommendation_label(composite),
                "foreign_streak_buy": chip_snap.get("foreign_streak_buy", 0),
                "foreign_streak_sell": chip_snap.get("foreign_streak_sell", 0),
                "foreign_cum20": chip_snap.get("foreign_cum20", 0),
                "margin_chg5": chip_snap.get("margin_chg5"),
                "per": fund_snap.get("per"),
                "pbr": fund_snap.get("pbr"),
                "dividend_yield": fund_snap.get("dividend_yield"),
                "peg": fund_snap.get("peg"),
                "roe_ttm": fund_snap.get("roe_ttm"),
                "gross_margin": fund_snap.get("gross_margin"),
                "eps_yoy": fund_snap.get("eps_yoy"),
                "revenue_yoy": fund_snap.get("revenue_yoy"),
                "rs20": rs20,
                "rs60": rs60,
                "rev_mom": rev_mom,
                "rev_yoy_month": rev_yoy_m,
                "rev_yoy_streak_pos": rev_streak.get("rev_yoy_streak_pos"),
                "rev_yoy_streak_hot": rev_streak.get("rev_yoy_streak_hot"),
                "amount_20d": liquidity_by_sid.get(sid, {}).get("amount_20d"),
                "pct_change_today": liquidity_by_sid.get(sid, {}).get("pct_change_today"),
                "as_of": as_of_str,
                # v5c Wave 2 Phase 2：4 個 Style Score 寫入 snapshot
                "style_value": styles.get("value"),
                "style_growth": styles.get("growth"),
                "style_momentum": styles.get("momentum"),
                "style_income": styles.get("income"),
            })
        except Exception as e:
            logger.debug("score %s 失敗: %s", sid, e)
    out = pd.DataFrame(rows)
    return out


def _score_to_float(v) -> float | None:
    """把 score_all 回傳的 cell（可能是 numpy float / None / NaN）轉成乾淨的 float | None。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def live_scores_for(
    db: Database,
    stocks: list[tuple[str, str]],
    *,
    as_of: str | None = None,
) -> dict[str, dict]:
    """對一小組股票（雷達/自選清單的「當前頁」，≤50 檔）抓盤中即時報價並重算分數。

    回傳 {stock_id: {close, short, mid, long, composite, vr_macd, recommendation,
    is_live, change_pct}}。is_live=True 表示該檔吃到盤中即時價；抓不到（興櫃/休市/mis 失敗）
    的檔仍回分數（用收盤算、is_live=False），與快照一致，caller 可原樣顯示。

    設計重點：
    - intraday.fetch_quotes 一次批次抓全頁（'|' 串接 → ~1 個對外請求），只把 is_live 的當即時價；
      避免一頁 50 檔對非官方 mis 端點打 50 次。
    - 即時價灌進 score_all(stock_ids=這頁, live_prices=...)：**重用同一份 score_all 數學**，與
      快照 / 個股詳情頁一致，不另開會 drift 的第二套評分路徑（CLAUDE.md #5）。
    - as_of 綁快照日 → score_all 載到該日為止的歷史、override 替換最後一根，等同個股詳情頁的
      live 行為；只是批次化到一整頁。
    - **絕不寫入 signal_history**：純 API 回應層（盤中價非 final，會污染回測/IC）。
    """
    from app.data import intraday  # lazy：避免 radar import 時就拉 requests
    from app.data.sql_utils import make_placeholders

    stocks = [(sid, name) for sid, name in stocks if sid]
    if not stocks:
        return {}
    ids = [sid for sid, _ in stocks]

    # 每檔 market_type（決定 mis 的 tse_/otc_ 前綴；批次模式不做逐檔 tse→otc fallback）
    with db.connect() as conn:
        type_rows = conn.execute(
            f"SELECT stock_id, type FROM stock_info WHERE stock_id IN ({make_placeholders(len(ids))})",
            ids,
        ).fetchall()
    type_by_sid = {r["stock_id"]: r["type"] for r in type_rows}

    quotes = intraday.fetch_quotes([(sid, type_by_sid.get(sid)) for sid in ids])
    live_prices: dict[str, float] = {}
    change_by_sid: dict[str, float] = {}
    for sid, q in quotes.items():
        if q.is_live and q.price and q.price > 0:
            live_prices[sid] = float(q.price)
        if q.prev_close and q.prev_close > 0 and q.price is not None:
            change_by_sid[sid] = (q.price - q.prev_close) / q.prev_close

    df = score_all(
        db, as_of=as_of, candidate_stocks=stocks, stock_ids=ids, live_prices=live_prices,
    )

    out: dict[str, dict] = {}
    if df.empty:
        return out
    for _, r in df.iterrows():
        sid = r["stock_id"]
        out[sid] = {
            "close": _score_to_float(r.get("close")),
            "short": _score_to_float(r.get("short")),
            "mid": _score_to_float(r.get("mid")),
            "long": _score_to_float(r.get("long")),
            "composite": _score_to_float(r.get("composite")),
            "vr_macd": _score_to_float(r.get("vr_macd")),
            "recommendation": r.get("recommendation"),
            "is_live": sid in live_prices,
            "change_pct": change_by_sid.get(sid),
        }
    return out


# ======================================================================
# 預設策略
# ======================================================================
@dataclass
class Strategy:
    name: str
    description: str
    filter_fn: Callable[[pd.DataFrame], pd.DataFrame]
    sort_by: str
    ascending: bool = False
    # 該策略是否「個股限定」(ETF 沒有 EPS / ROE / 月營收，套這些策略沒意義)
    stocks_only: bool = False


def _strat_short_strong(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["short"] >= 65) & (df["mid"] >= 50)]


def _strat_mid_trend(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["mid"] >= 65) & (df["short"] >= 50)]


def _strat_long_value(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["long"] >= 65) & (df["mid"] >= 50)]


def _strat_oversold_rebound(df: pd.DataFrame) -> pd.DataFrame:
    # 短期偏弱但中長期良好 = 可能是回檔進場機會
    return df[
        (df["short"] <= 45)
        & (df["mid"] >= 55)
        & (df["long"] >= 55)
    ]


def _strat_allround(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["short"] >= 55) & (df["mid"] >= 60) & (df["long"] >= 55)]


def _strat_revenue_growth_streak(df: pd.DataFrame) -> pd.DataFrame:
    """營收持續加速：連續 ≥3 個月 YoY > 0（成長趨勢穩定），且短期不弱。"""
    if "rev_yoy_streak_pos" not in df.columns:
        return df.head(0)
    return df[(df["rev_yoy_streak_pos"].fillna(0) >= 3) & (df["short"] >= 50)]


def _strat_revenue_hot_streak(df: pd.DataFrame) -> pd.DataFrame:
    """高速成長：連續 ≥3 個月 YoY > 20%（高度加速），中期不弱。"""
    if "rev_yoy_streak_hot" not in df.columns:
        return df.head(0)
    return df[(df["rev_yoy_streak_hot"].fillna(0) >= 3) & (df["mid"] >= 50)]


def _strat_vr_macd(df: pd.DataFrame) -> pd.DataFrame:
    """量能動能榜：vr_macd >= 60、未過時、且額外要求 VR26 > 150。

    因子已調整為純 VR 評分，因此策略硬條件也同步改為量能條件，
    避免前後語意不一致。
    """
    if "vr_macd" not in df.columns:
        return df.head(0)
    if "vr26" not in df.columns:
        # 沒有原始指標欄位（例如只 seed signal_history 跑單元測試）就退回基本篩選
        return df[(df["vr_macd"].fillna(-1) >= 60) & (df["is_stale"].fillna(0) == 0)]
    return df[
        (df["vr_macd"].fillna(-1) >= 60)
        & (df["is_stale"].fillna(0) == 0)
        & (df["vr26"].fillna(-1) > 150)
    ]


STRATEGIES: dict[str, Strategy] = {
    "短線強勢": Strategy(
        name="短線強勢",
        description="短期≥65 且中期≥50，適合 1~4 週波段",
        filter_fn=_strat_short_strong,
        sort_by="short",
    ),
    "中期波段": Strategy(
        name="中期波段",
        description="中期≥65 且短期≥50，適合 1~6 個月持有",
        filter_fn=_strat_mid_trend,
        sort_by="mid",
    ),
    "長期價值": Strategy(
        name="長期價值",
        description="長期≥65 且中期≥50，適合長期存股",
        filter_fn=_strat_long_value,
        sort_by="long",
        stocks_only=True,  # 長期分數依賴 ROE/EPS/股利，ETF 無此資料
    ),
    "回檔布局": Strategy(
        name="回檔布局",
        description="短期弱但中長期強，可能是低接機會",
        filter_fn=_strat_oversold_rebound,
        sort_by="composite",
    ),
    "三榜俱佳": Strategy(
        name="三榜俱佳",
        description="短中長三個分數都達門檻，全方位優質",
        filter_fn=_strat_allround,
        sort_by="composite",
        stocks_only=True,  # 需要長期分數才能達標
    ),
    "營收持續成長": Strategy(
        name="營收持續成長",
        description="連續 ≥3 個月 YoY > 0 的穩定成長股（訊號比單月更可靠），且短期不弱",
        filter_fn=_strat_revenue_growth_streak,
        sort_by="rev_yoy_streak_pos",
        stocks_only=True,
    ),
    "營收高速加速": Strategy(
        name="營收高速加速",
        description="連續 ≥3 個月 YoY > 20% 的高速成長（通常伴隨股價強勢），中期分數不弱",
        filter_fn=_strat_revenue_hot_streak,
        sort_by="rev_yoy_streak_hot",
        stocks_only=True,
    ),
    "量能動能": Strategy(
        name="量能動能",
        description="VR26 > 150 + 量能分數≥60，依 vr_macd 排序",
        filter_fn=_strat_vr_macd,
        sort_by="vr_macd",
        stocks_only=False,  # ETF 同樣有量能訊號
    ),
}


def scan(db: Database, strategy_name: str, top_n: int = 30) -> pd.DataFrame:
    """執行雷達掃描。"""
    strat = STRATEGIES[strategy_name]
    df = score_all(db)
    if df.empty:
        return df
    filtered = strat.filter_fn(df).sort_values(strat.sort_by, ascending=strat.ascending)
    return filtered.head(top_n).reset_index(drop=True)
