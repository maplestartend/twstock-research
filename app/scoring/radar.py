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

from app.data.clock import taipei_today
from app.data.db import Database
from app.indicators import chips as chip_ind
from app.indicators import fundamentals as fund_ind
from app.indicators import market_context as mkt_ctx
from app.indicators import technical as tech
from app.scoring import engine as eng
from app.scoring.engine import score_stock

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

    產業內樣本數 < 4 就跳過該產業（避免極端值主宰 z）。回傳的 dict 只包含可算 z 的股票，
    其他股票在 score_dividend 會走絕對閾值 fallback。
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

    by_industry: dict[str, list[tuple[str, float]]] = {}
    for sid, y in sid_to_yield.items():
        ind = industry_by_sid.get(sid)
        if not ind:
            continue
        by_industry.setdefault(ind, []).append((sid, y))

    result: dict[str, float] = {}
    for ind, lst in by_industry.items():
        if len(lst) < 4:
            continue
        ys = pd.Series([y for _, y in lst])
        mean = float(ys.mean())
        std = float(ys.std(ddof=1))
        if std <= 1e-9:
            continue
        for sid, y in lst:
            result[sid] = (y - mean) / std
    return result


def _bulk_load(db: Database, table: str, since: str, until: str | None = None, date_col: str = "date") -> pd.DataFrame:
    with db.connect() as conn:
        if until is None:
            df = pd.read_sql_query(
                f"SELECT * FROM {table} WHERE {date_col} >= ?",
                conn, params=[since],
            )
        else:
            df = pd.read_sql_query(
                f"SELECT * FROM {table} WHERE {date_col} BETWEEN ? AND ?",
                conn, params=[since, until],
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
) -> pd.DataFrame:
    """對 DB 所有有足夠資料的股票打分。

    include_fundamentals=False 會略過 financials 表（大幅加速，長期分數只用 per_pbr 推估）。

    as_of: 評分基準日（YYYY-MM-DD or date）。預設 None → taipei_today()，所有資料
    上限為今日。**指定 as_of 時所有 DB 查詢都會把 date <= as_of 設為上限**，避免歷史
    回放（snapshot replay / 回測）時把未來資料當成可用（金融分析師審查 Critical #3）。
    """
    if as_of is None:
        as_of_d = taipei_today()
    elif isinstance(as_of, str):
        as_of_d = date.fromisoformat(as_of)
    else:
        as_of_d = as_of
    as_of_str = as_of_d.isoformat()

    stocks = list_candidate_stocks(db, min_days)
    if limit:
        stocks = stocks[:limit]
    if not stocks:
        return pd.DataFrame()

    # 批次載入 as_of 起算往回 400 天（技術指標暖機所需）
    since = (as_of_d - timedelta(days=400)).isoformat()
    # 用 LEFT JOIN 帶上 daily_price_adj，技術指標就能用還原價（除權息/分割不誤導）
    with db.connect() as conn:
        price_all = pd.read_sql_query(
            """
            SELECT p.date, p.stock_id, p.open, p.high, p.low, p.close, p.volume,
                   a.close_adj, a.open_adj, a.high_adj, a.low_adj
            FROM daily_price p
            LEFT JOIN daily_price_adj a
              ON a.stock_id = p.stock_id AND a.date = p.date
            WHERE p.date BETWEEN ? AND ?
            """,
            conn, params=[since, as_of_str],
        )
        if not price_all.empty:
            price_all["date"] = pd.to_datetime(price_all["date"])
            # 有還原價就替換，沒還原就保留原值
            for col in ("close", "open", "high", "low"):
                adj_col = f"{col}_adj"
                if adj_col in price_all.columns:
                    price_all[col] = price_all[adj_col].fillna(price_all[col])
            price_all = price_all.drop(columns=[c for c in ("close_adj", "open_adj", "high_adj", "low_adj") if c in price_all.columns])
    inst_all = _bulk_load(db, "institutional", since, until=as_of_str)
    margin_all = _bulk_load(db, "margin", since, until=as_of_str)
    per_all = _bulk_load(db, "per_pbr", since, until=as_of_str)

    # 若需要基本面，載入所有財報（只載近 3 年）
    if include_fundamentals:
        fin_since = (as_of_d - timedelta(days=365 * 3)).isoformat()
        with db.connect() as conn:
            fin_all = pd.read_sql_query(
                "SELECT * FROM financials WHERE date BETWEEN ? AND ?",
                conn, params=[fin_since, as_of_str],
            )
            # 全市場累計財報（TWSE/TPEX OpenAPI），用於 fundamentals 的 fallback。
            # 用 publish_date 過濾 → 避免 backtest（as_of 在過去時）看到尚未公告的當季財報。
            # COALESCE 是給尚未 backfill / 舊版寫入的 row 兜底（NULL → 用 quarter-end，較保守）。
            fin_cum_all = pd.read_sql_query(
                "SELECT * FROM financials_cumulative "
                "WHERE date BETWEEN ? AND ? "
                "  AND COALESCE(publish_date, date) <= ?",
                conn, params=[fin_since, as_of_str, as_of_str],
            )
            # 累計差分後的單季值，用於 TTM / YoY / ROE 計算（同樣 publish_date 守則）
            fin_derived_all = pd.read_sql_query(
                "SELECT * FROM financials_quarterly_derived "
                "WHERE date BETWEEN ? AND ? "
                "  AND COALESCE(publish_date, date) <= ?",
                conn, params=[fin_since, as_of_str, as_of_str],
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
    with db.connect() as conn:
        # latest 也要設上限，避免歷史回放時取到未來月份。
        # 用 ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) 取代相關子查詢，
        # SQLite 對 correlated subquery 的執行計畫是「每筆 outer row 跑一次」，2700 檔 × 100 月 = 27 萬次掃描；
        # 換成 window function 只需單次 partition scan（同 watchlist.py:210 的寫法）。
        rev_all = pd.read_sql_query(
            """
            SELECT stock_id, date, revenue, mom_pct, yoy_pct FROM (
                SELECT stock_id, date, revenue, mom_pct, yoy_pct,
                       ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
                FROM monthly_revenue
                WHERE date <= ?
            ) WHERE rn = 1
            """, conn, params=[as_of_str],
        )
        # 近 12 個月 YoY 歷史（用於算「連續 YoY >0 或 >20% 月數」）
        rev_hist = pd.read_sql_query(
            """
            SELECT stock_id, date, yoy_pct FROM monthly_revenue
            WHERE date BETWEEN ? AND ?
            ORDER BY stock_id, date
            """, conn, params=[rev_lower, as_of_str],
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
    yield_z_map = _industry_yield_z_map(per_all, industry_by_sid)

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
            price = tech.enrich(price.copy())
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

            short = eng.score_short_term(price, chip_snap)
            mid = eng.score_mid_term(price, chip_snap, fund_snap)
            long_ = eng.score_long_term(fund_snap, stock_id=sid)
            vr_macd_val = short.parts.get("vr_macd")
            # vr26 / macd_hist 原始值給 _strat_vr_macd 做更嚴格的 filter（VR>150 + MACD 紅柱）
            last_row = price.iloc[-1]
            vr26_val = float(last_row["vr26"]) if pd.notna(last_row.get("vr26")) else None
            macd_hist_val = float(last_row["macd_hist"]) if pd.notna(last_row.get("macd_hist")) else None

            composite, _comp_usage = eng.composite_score(short.total, mid.total, long_.total)
            data_completeness = eng.overall_completeness(short, mid, long_)
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
                "as_of": as_of_str,
            })
        except Exception as e:
            logger.debug("score %s 失敗: %s", sid, e)
    return pd.DataFrame(rows)


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


def _strat_foreign_buy(df: pd.DataFrame) -> pd.DataFrame:
    """外資連買訊號：連續 ≥5 個交易日（一週以上）+ 20 日淨買超 + 短期不弱。
    3 日太短（一週才 5 個交易日），易反轉；改為 5 日才當訊號。"""
    return df[
        (df["foreign_streak_buy"] >= 5)
        & (df["foreign_cum20"].fillna(0) > 0)
        & (df["short"] >= 50)
    ]


def _strat_oversold_rebound(df: pd.DataFrame) -> pd.DataFrame:
    # 短期偏弱但中長期良好 = 可能是回檔進場機會
    return df[
        (df["short"] <= 45)
        & (df["mid"] >= 55)
        & (df["long"] >= 55)
    ]


def _strat_allround(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["short"] >= 55) & (df["mid"] >= 60) & (df["long"] >= 55)]


def _strat_rs_strong(df: pd.DataFrame) -> pd.DataFrame:
    """20 日跑贏大盤 >5%，且短期不弱。"""
    if "rs20" not in df.columns:
        return df.head(0)
    return df[(df["rs20"].fillna(-99) > 0.05) & (df["short"] >= 50)]


def _strat_revenue_surge(df: pd.DataFrame) -> pd.DataFrame:
    """月營收年增 > 20% 的標的（只有已抓月營收資料的股票會出現）。"""
    if "rev_yoy_month" not in df.columns:
        return df.head(0)
    return df[df["rev_yoy_month"].fillna(-99) > 0.20]


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
    """量能動能榜：vr_macd >= 60、未過時、且額外要求 VR26 > 150 + MACD 紅柱（hist > 0）。

    後兩條是使用者指定的硬篩選：要量能進入「活躍以上」區間（VR>150）且
    動能向上（MACD 柱站上 0 軸 = 紅柱）。少了任一個都不算「量能 × 動能」共振。
    score_vr_macd 內已部分覆蓋這些情況（rule D/C/B 等），但複合分數裡 K-fallback
    路徑可能讓 vr26<150 但 macd_hist 正向的 row 也得 60+，這裡再過一次硬條件。
    """
    if "vr_macd" not in df.columns:
        return df.head(0)
    if "vr26" not in df.columns or "macd_hist" not in df.columns:
        # 沒有原始指標欄位（例如只 seed signal_history 跑單元測試）就退回基本篩選
        return df[(df["vr_macd"].fillna(-1) >= 60) & (df["is_stale"].fillna(0) == 0)]
    return df[
        (df["vr_macd"].fillna(-1) >= 60)
        & (df["is_stale"].fillna(0) == 0)
        & (df["vr26"].fillna(-1) > 150)
        & (df["macd_hist"].fillna(-1) > 0)
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
    "外資連買": Strategy(
        name="外資連買",
        description="外資連買≥5日（一週以上）+ 20日淨買超，短期不弱",
        filter_fn=_strat_foreign_buy,
        sort_by="foreign_cum20",
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
    "相對強勢": Strategy(
        name="相對強勢",
        description="20 日跑贏加權指數 >5%，且短期分數≥50（不追弱勢股）",
        filter_fn=_strat_rs_strong,
        sort_by="rs20",
    ),
    "月營收爆發": Strategy(
        name="月營收爆發",
        description="月營收年增率 > 20%（最新單月，可能是淡旺季效應）",
        filter_fn=_strat_revenue_surge,
        sort_by="rev_yoy_month",
        stocks_only=True,
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
        description="VR26 > 150 + MACD 紅柱 + 複合分≥60，依 vr_macd 排序",
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
