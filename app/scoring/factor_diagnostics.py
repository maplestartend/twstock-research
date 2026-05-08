"""因子有效性檢定：對 signal_history 內已寫入的分數做 forward-return Information Coefficient 分析。

回答的問題：「過去 N 天內，short / mid / long / composite / vr_macd 五個分數，對 5 / 20 / 60 日後的
報酬率到底有沒有預測力？」這是判斷分數權重該不該調整、要不要砍掉某條子線的客觀依據。

設計：
- 用 signal_history 的歷史快照 + daily_price.close 計算 forward return（trading days 概念，用 shift）
- IC 用 Spearman 等價（rank → Pearson），對極端值不敏感、不假設分佈
- IC_IR = mean(IC) / std(IC)：跨期穩定度，> 0.5 算有「持續」訊號
- Quintile spread = top 20% 的平均 forward return − bottom 20%；越大越有區分力
- 樣本守則：單日 < 30 檔（信號太稀）或全期 < 5 個 IC 點（樣本太少）→ 不算

效能：原始 compute 端每次 ~13s（74% 時間在讀 4M 列 factor_parts），所以加 `factor_ic_cache`
表：cache key = (scope, signal_history.MAX(as_of), lookback_days)；snapshot 不變時 cache hit
直接秒回，snapshot 推進就重算一次寫回 cache。對外呼叫者用 `get_factor_ic_cached` /
`get_subfactor_ic_cached`。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

from app.data.adjuster import read_close_with_adj_coalesced
from app.data.clock import taipei_today
from app.data.db import Database

logger = logging.getLogger(__name__)

FACTORS: tuple[str, ...] = ("short", "mid", "long", "composite", "vr_macd")
DEFAULT_HORIZONS: tuple[int, ...] = (5, 20, 60)
DEFAULT_LOOKBACK_DAYS = 120
MIN_SAMPLES_PER_DATE = 30
MIN_DATES_PER_FACTOR = 5

# 改 IC 計算邏輯（forward return / Spearman / Newey-West / quintile / 子因子定義）**或**
# 改 scoring 權重 (LONG/SHORT/MID/COMPOSITE_WEIGHTS、子因子公式) 時把這裡 bump，
# cache 會自動失效不再回舊版結果。bump 範例：v3 → v4-yyyy-mm-dd。
# 純資料補回（重跑 backfill_signal_history --clear）不需要 bump — `--clear` 已 DELETE cache，
# 但**沒帶 --clear** 的情境（如 daily_update 推進 MAX(as_of)）若沒 bump 就會混 v3/v4 分數。
IC_ALGO_VERSION = "v5c-2026-05-08"  # v5c: mid 砍 revenue_growth (60d -0.030 反向) + 5 因子重平衡；short KD/margin_change 翻轉映射 (mean-reversion); volume 0.10→0.20; rsi/bollinger/macd 降權; BS 歷史 backfill (t163sb05 + derive)


@dataclass(frozen=True)
class FactorICResult:
    factor: str
    horizon: int
    ic: float | None  # mean Spearman IC across dates; None = 樣本不足
    ic_ir: float | None  # mean / std；None = std 無法算或 dates < 2
    top_quintile_return: float | None  # Q5 平均 forward return（後 20%→Q5 == 因子最高）
    bot_quintile_return: float | None  # Q1 平均 forward return（前 20%→Q1 == 因子最低）
    n_dates: int  # 真正參與計算的日期數（過濾 min_samples 後）
    avg_n_stocks: float  # 各日期樣本數平均
    # 95% CI on mean IC（Newey-West HAC, lag≈horizon-1；None = n_dates < 5）。
    # 修正 forward window 重疊造成的自相關，避免 naive bootstrap 區間過窄。
    ic_ci_lo: float | None = None
    ic_ci_hi: float | None = None


def _fetch_data(db: Database, lookback_days: int, max_horizon: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """一次讀完需要的 signal_history + 價格序列，避免在 loop 裡反覆查 DB。

    forward return 優先使用還原價（daily_price_adj.close_adj），缺值才 fallback 原始 close。
    這樣除權息/分割附近不會把「機械性價差」誤當成因子預測力。
    """
    today = taipei_today()
    # 多抓一個 horizon + buffer 30 天，確保最後一日的 forward return 還有資料
    earliest = (today - timedelta(days=lookback_days + max_horizon + 30)).isoformat()
    with db.connect() as conn:
        cols = ", ".join(FACTORS)
        snap = pd.read_sql_query(
            f"SELECT as_of, stock_id, {cols} FROM signal_history WHERE as_of >= ?",
            conn, params=[earliest],
        )
        prices = read_close_with_adj_coalesced(conn, since=earliest)
    return snap, prices


def _build_price_pivot(prices: pd.DataFrame) -> pd.DataFrame:
    """把 long-format daily_price 轉成 wide：rows=date, cols=stock_id, values=close。
    用作 shift(-h) 算 forward return 的基底。

    close=0 視為缺值（NaN）：實際 daily_price 在停牌 / 暫停交易日會回 0，但 0 當分母會
    讓 forward_return = (forward / 0) - 1 變 inf，污染 Q5/Q1 spread 統計。改成 NaN
    後 valid mask 會排除這些 cell。
    """
    if prices.empty:
        return pd.DataFrame()
    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"])
    wide = prices.pivot(index="date", columns="stock_id", values="close").sort_index()
    # 0 視同停牌缺值
    wide = wide.replace(0, np.nan)
    return wide


def _spearman_ic(x: pd.Series, y: pd.Series) -> float | None:
    """rank → Pearson = Spearman。NaN 自動 align 後排除。"""
    df = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(df) < MIN_SAMPLES_PER_DATE:
        return None
    rank_x = df["x"].rank()
    rank_y = df["y"].rank()
    if rank_x.nunique() < 2 or rank_y.nunique() < 2:
        return None
    ic = float(rank_x.corr(rank_y))
    return ic if not pd.isna(ic) else None


def _quintile_spread(x: pd.Series, y: pd.Series) -> tuple[float | None, float | None]:
    """把 x 切 5 等分，回 (Q5 平均 y, Q1 平均 y)。Q5 = 因子最高那組。

    用 quantile 而非 qcut 排名，避免 tie 報錯（同分太多時 qcut 會 ValueError）。
    """
    df = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(df) < MIN_SAMPLES_PER_DATE:
        return None, None
    # 用 rank percentage 切分，比 qcut 更耐 tie
    pct = df["x"].rank(pct=True)
    q5 = df.loc[pct >= 0.8, "y"]
    q1 = df.loc[pct <= 0.2, "y"]
    if q5.empty or q1.empty:
        return None, None
    return float(q5.mean()), float(q1.mean())


def _ic_and_quintile(x: pd.Series, y: pd.Series) -> tuple[float | None, float | None, float | None, int]:
    """合併版：一次 dropna + 一次 rank，同時算 IC 與 Q5/Q1 mean。

    回傳 (ic, q5_mean, q1_mean, n)。資料不足時前三個為 None、n 仍回 dropna 後實際樣本數
    （給 avg_n_stocks 統計用）。

    為什麼合併：原本 _spearman_ic 與 _quintile_spread 各自做一次 DataFrame 構造 + dropna
    + rank，loop 的瓶頸（27ms/iter）一半在這個 redundant 上。共用後降到 ~10ms/iter。

    保留作 reference / 單元測試對照；批次計算改用 _vectorized_ic_metrics 一次處理所有日期。
    """
    df = pd.DataFrame({"x": x, "y": y}).dropna()
    n = len(df)
    if n < MIN_SAMPLES_PER_DATE:
        return None, None, None, n
    rank_x = df["x"].rank()
    rank_y = df["y"].rank()
    if rank_x.nunique() < 2 or rank_y.nunique() < 2:
        return None, None, None, n
    ic_val = rank_x.corr(rank_y)
    ic: float | None = float(ic_val) if not pd.isna(ic_val) else None
    pct = rank_x / n  # 等價 df["x"].rank(pct=True)，省掉一次 rank
    q5_y = df.loc[pct >= 0.8, "y"]
    q1_y = df.loc[pct <= 0.2, "y"]
    q5 = float(q5_y.mean()) if not q5_y.empty else None
    q1 = float(q1_y.mean()) if not q1_y.empty else None
    return ic, q5, q1, n


@dataclass(frozen=True)
class _RankedFrame:
    """Pre-cross-sectional-ranked DataFrame (rows=date)。raw + rank + valid mask 一起帶，
    避免在 IC 計算時對同樣資料重複 .rank()。"""
    dates: pd.Index
    columns: pd.Index
    raw: np.ndarray   # (n_dates, n_stocks) original values
    rank: np.ndarray  # (n_dates, n_stocks) cross-sectional rank, NaN where invalid
    valid: np.ndarray  # (n_dates, n_stocks) bool


def _rank_frame(df: pd.DataFrame) -> _RankedFrame:
    """Cross-sectional rank（每行獨立）。 NaN 不參與排序。"""
    if df.empty:
        return _RankedFrame(df.index, df.columns, np.empty((0, 0)), np.empty((0, 0)), np.empty((0, 0), dtype=bool))
    raw = df.to_numpy(dtype="float64")
    valid = ~np.isnan(raw)
    # pandas .rank(axis=1) 對 NaN 自動回 NaN
    ranks = df.rank(axis=1).to_numpy()
    return _RankedFrame(df.index, df.columns, raw, ranks, valid)


def _prepare_factor_pivots(
    snap: pd.DataFrame, prices: pd.DataFrame
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame] | None:
    """共用的 setup：snap → 各 factor 的 pivot；price_wide intersect 到 factor 涵蓋的代號集合。

    回 (factor_pivots, price_wide_aligned)；snap/prices 空 / pivot 後空 → None。
    為 compute_factor_ic 與 compute_rolling_ic_for_horizon 共用，避免兩處 step-by-step 重做。
    """
    if snap.empty or prices.empty:
        return None
    snap = snap.copy()
    snap["as_of"] = pd.to_datetime(snap["as_of"])
    price_wide = _build_price_pivot(prices)
    if price_wide.empty:
        return None
    factor_pivots: dict[str, pd.DataFrame] = {
        factor: snap.pivot_table(
            index="as_of", columns="stock_id", values=factor, aggfunc="first"
        )
        for factor in FACTORS
    }
    # daily_price 表含 22k 個代號（含權證/牛熊證/ETN），但 signal_history 只有 ~2.3k 一般股。
    # 把 price_wide 縮到 factor_pivot 的代號集合 → forward_return 計算與每日 .loc lookup 都
    # 從 22k 欄降到 2.3k 欄，本身就 ~10× 加速；後面 _ic_and_quintile 的 dropna 也少很多假 NaN。
    factor_stocks = factor_pivots[FACTORS[0]].columns
    common_cols = price_wide.columns.intersection(factor_stocks)
    if not common_cols.empty:
        price_wide = price_wide[common_cols]
    return factor_pivots, price_wide


def _newey_west_mean_ci(
    ic_per_date: list[float],
    *,
    lag: int,
) -> tuple[float | None, float | None]:
    """用 Newey-West HAC 估平均 IC 的 95% CI（處理序列自相關）。"""
    if len(ic_per_date) < 5:
        return None, None
    arr = np.asarray(ic_per_date, dtype="float64")
    n = len(arr)
    if n < 2:
        return None, None
    lag = max(1, min(int(lag), n - 1))
    mean_ic = float(arr.mean())
    resid = arr - mean_ic

    gamma0 = float(np.dot(resid, resid) / n)
    long_run_var = gamma0
    for l in range(1, lag + 1):
        cov = float(np.dot(resid[l:], resid[:-l]) / n)
        weight = 1.0 - (l / (lag + 1.0))
        long_run_var += 2.0 * weight * cov
    var_mean = long_run_var / n
    if not np.isfinite(var_mean) or var_mean <= 0:
        return None, None
    se = float(np.sqrt(var_mean))
    z = 1.96
    return mean_ic - z * se, mean_ic + z * se


def _per_date_ic_full(
    factor_ranked: _RankedFrame, fwd_ranked: _RankedFrame,
) -> tuple[pd.Index, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """共用核心：對每個 date 同時算 IC、Q5、Q1、樣本數。

    回傳 (common_dates, ic_array, q5_array, q1_array, n_array, enough_mask)。
    NaN 表示該日樣本不足 / 排不出。array 長度都等於 common_dates 長度。

    用作兩個 caller 的共用 plumbing：
    - `_vectorized_ic_metrics`（過濾 NaN 後做 list 統計）
    - `compute_rolling_ic_for_horizon`（保留 NaN，做 pandas rolling mean）
    """
    common_dates = factor_ranked.dates.intersection(fwd_ranked.dates)
    if common_dates.empty or len(factor_ranked.columns) == 0 or len(fwd_ranked.columns) == 0:
        empty = np.array([])
        return common_dates, empty, empty, empty, empty.astype(int), empty.astype(bool)

    fr_idx = factor_ranked.dates.get_indexer(common_dates)
    yr_idx = fwd_ranked.dates.get_indexer(common_dates)
    common_cols = factor_ranked.columns.intersection(fwd_ranked.columns)
    if common_cols.empty:
        empty = np.array([])
        return common_dates, empty, empty, empty, empty.astype(int), empty.astype(bool)
    fr_col_idx = factor_ranked.columns.get_indexer(common_cols)
    yr_col_idx = fwd_ranked.columns.get_indexer(common_cols)

    rx_full = factor_ranked.rank[np.ix_(fr_idx, fr_col_idx)]
    ry_full = fwd_ranked.rank[np.ix_(yr_idx, yr_col_idx)]
    valid_x = factor_ranked.valid[np.ix_(fr_idx, fr_col_idx)]
    valid_y = fwd_ranked.valid[np.ix_(yr_idx, yr_col_idx)]
    valid = valid_x & valid_y
    y_raw = fwd_ranked.raw[np.ix_(yr_idx, yr_col_idx)]
    rx = np.where(valid, rx_full, 0.0)
    ry = np.where(valid, ry_full, 0.0)

    n_per_row = valid.sum(axis=1)
    enough = n_per_row >= MIN_SAMPLES_PER_DATE
    n_safe = np.where(enough, n_per_row, 1).astype("float64")

    sum_x = rx.sum(axis=1)
    sum_y = ry.sum(axis=1)
    mean_x = sum_x / n_safe
    mean_y = sum_y / n_safe
    dx = np.where(valid, rx - mean_x[:, None], 0.0)
    dy = np.where(valid, ry - mean_y[:, None], 0.0)
    cov = (dx * dy).sum(axis=1) / n_safe
    var_x = (dx * dx).sum(axis=1) / n_safe
    var_y = (dy * dy).sum(axis=1) / n_safe
    denom = np.sqrt(var_x * var_y)
    with np.errstate(invalid="ignore", divide="ignore"):
        ic_row = np.where((denom > 1e-12) & enough, cov / denom, np.nan)

    pct = rx_full / np.where(n_per_row[:, None] > 0, n_per_row[:, None], 1)
    top_mask = (pct >= 0.8) & valid
    bot_mask = (pct <= 0.2) & valid
    y_for_top = np.where(top_mask, y_raw, 0.0)
    y_for_bot = np.where(bot_mask, y_raw, 0.0)
    top_n = top_mask.sum(axis=1)
    bot_n = bot_mask.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        q5 = np.where((top_n > 0) & enough, y_for_top.sum(axis=1) / np.maximum(top_n, 1), np.nan)
        q1 = np.where((bot_n > 0) & enough, y_for_bot.sum(axis=1) / np.maximum(bot_n, 1), np.nan)

    return common_dates, ic_row, q5, q1, n_per_row, enough


def _vectorized_ic_metrics(
    factor_pivot: pd.DataFrame, forward_returns: pd.DataFrame,
    *, factor_ranked: _RankedFrame | None = None, fwd_ranked: _RankedFrame | None = None,
) -> tuple[list[float], list[float], list[float], list[int]]:
    """全部日期一次算完 IC + Q5/Q1，避免 per-date Python loop。

    Cross-sectional：每一天（每一 row）獨立排序後算相關。NaN 自動排除。

    回傳 (ic_per_date, q5_per_date, q1_per_date, n_per_date)，list 長度等於 dates 數
    （每天可能因樣本不足被略過 → 不一定等於 factor_pivot.index 長度）。

    可預先 rank 兩個 input（factor_ranked / fwd_ranked），caller 跨多個 (factor, horizon)
    組合時只需 rank 21 次 + 3 次而非 63 次，sub-factor IC 由此再 ~3× 加速。
    """
    if factor_ranked is None:
        factor_ranked = _rank_frame(factor_pivot)
    if fwd_ranked is None:
        fwd_ranked = _rank_frame(forward_returns)

    common_dates, ic_row, q5, q1, n_per_row, enough = _per_date_ic_full(factor_ranked, fwd_ranked)
    if len(common_dates) == 0:
        return [], [], [], []

    ic_list: list[float] = []
    q5_list: list[float] = []
    q1_list: list[float] = []
    n_list: list[int] = []
    for i in range(len(common_dates)):
        if not enough[i] or np.isnan(ic_row[i]):
            continue
        ic_list.append(float(ic_row[i]))
        if not np.isnan(q5[i]) and not np.isnan(q1[i]):
            q5_list.append(float(q5[i]))
            q1_list.append(float(q1[i]))
        n_list.append(int(n_per_row[i]))
    return ic_list, q5_list, q1_list, n_list


def compute_factor_ic(
    db: Database,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> list[FactorICResult]:
    """主入口：對每個 (factor, horizon) 算一筆 FactorICResult。"""
    if not horizons:
        return []
    snap, prices = _fetch_data(db, lookback_days, max(horizons))
    prepared = _prepare_factor_pivots(snap, prices)
    if prepared is None:
        logger.info("factor_diagnostics: 資料不足 (snap=%d, prices=%d)", len(snap), len(prices))
        return []
    factor_pivots, price_wide = prepared

    # 預 rank：每個 factor + 每個 horizon 各一次（共 N + len(horizons) 次），避免內層每次都 rank。
    factor_ranked: dict[str, _RankedFrame] = {
        factor: _rank_frame(fp) for factor, fp in factor_pivots.items()
    }

    out: list[FactorICResult] = []
    for horizon in horizons:
        # 用 trading-day shift（DataFrame 沒有 weekend rows，shift(-h) 自動跳）
        forward_close = price_wide.shift(-horizon)
        forward_returns = (forward_close / price_wide) - 1.0  # rows=date, cols=stock_id
        fwd_ranked = _rank_frame(forward_returns)

        for factor in FACTORS:
            ic_per_date, q5_per_date, q1_per_date, n_per_date = _vectorized_ic_metrics(
                factor_pivots[factor], forward_returns,
                factor_ranked=factor_ranked[factor], fwd_ranked=fwd_ranked,
            )

            if len(ic_per_date) < MIN_DATES_PER_FACTOR:
                out.append(FactorICResult(
                    factor=factor, horizon=horizon,
                    ic=None, ic_ir=None,
                    top_quintile_return=None, bot_quintile_return=None,
                    n_dates=len(ic_per_date),
                    avg_n_stocks=float(np.mean(n_per_date)) if n_per_date else 0.0,
                ))
                continue

            mean_ic = float(np.mean(ic_per_date))
            std_ic = float(np.std(ic_per_date, ddof=1)) if len(ic_per_date) > 1 else 0.0
            ic_ir = mean_ic / std_ic if std_ic > 1e-9 else None
            ci_lo, ci_hi = _newey_west_mean_ci(ic_per_date, lag=max(1, horizon - 1))

            out.append(FactorICResult(
                factor=factor, horizon=horizon,
                ic=mean_ic, ic_ir=ic_ir,
                top_quintile_return=float(np.mean(q5_per_date)) if q5_per_date else None,
                bot_quintile_return=float(np.mean(q1_per_date)) if q1_per_date else None,
                n_dates=len(ic_per_date),
                avg_n_stocks=float(np.mean(n_per_date)),
                ic_ci_lo=ci_lo, ic_ci_hi=ci_hi,
            ))

    return out


def to_dict_list(results: list[FactorICResult]) -> list[dict]:
    return [asdict(r) for r in results]


# ======================================================================
# Rolling IC：每日的 cross-sectional IC 跑 N 日 rolling mean，給 regime detection 用
# ======================================================================

DEFAULT_ROLLING_WINDOW = 30  # 30 trading days ≈ 1.5 calendar months


def compute_rolling_ic_for_horizon(
    db: Database,
    *,
    horizon: int = 20,
    window: int = DEFAULT_ROLLING_WINDOW,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[dict]:
    """每個 aggregate factor 在指定 horizon 下的 per-date IC，做 N-day rolling mean。

    回傳 wide-format：[{date: 'YYYY-MM-DD', short: ..., mid: ..., long: ..., composite: ..., vr_macd: ...}, ...]
    None 代表該日 rolling window 內樣本不足或缺值。

    用途：在 /diagnostics 折線圖看 IC 跨 regime 的演進，比 mean IC 單一數字更能揭露
    「這個因子在 2024 才開始 work / 2022 一直 work / 從來沒 work」。
    """
    snap, prices = _fetch_data(db, lookback_days, horizon)
    prepared = _prepare_factor_pivots(snap, prices)
    if prepared is None:
        return []
    factor_pivots, price_wide = prepared

    forward_close = price_wide.shift(-horizon)
    forward_returns = (forward_close / price_wide) - 1.0
    fwd_ranked = _rank_frame(forward_returns)

    series_by_factor: dict[str, pd.Series] = {}
    union_dates: pd.Index | None = None
    for factor in FACTORS:
        factor_ranked = _rank_frame(factor_pivots[factor])
        dates, ic_row, _q5, _q1, _n, _enough = _per_date_ic_full(factor_ranked, fwd_ranked)
        if len(dates) == 0:
            continue
        s = pd.Series(ic_row, index=dates)
        # min_periods 半窗口起算，避免一開始全 NaN
        s_rolled = s.rolling(window=window, min_periods=max(5, window // 2)).mean()
        series_by_factor[factor] = s_rolled
        union_dates = s_rolled.index if union_dates is None else union_dates.union(s_rolled.index)

    if not series_by_factor or union_dates is None:
        return []

    union_dates = union_dates.sort_values()
    rows: list[dict] = []
    for d in union_dates:
        row: dict = {"date": d.strftime("%Y-%m-%d")}
        for factor in FACTORS:
            s = series_by_factor.get(factor)
            if s is None:
                row[factor] = None
                continue
            v = s.get(d)
            row[factor] = float(v) if v is not None and pd.notna(v) else None
        rows.append(row)
    return rows


# ======================================================================
# 子因子（sub-factor）IC：拆解 short/mid/long 內部各小項的預測力
# ======================================================================

@dataclass(frozen=True)
class SubFactorICResult:
    """單一 (horizon, factor) 子因子的 IC 結果。

    例：horizon='short', factor='rsi' → RSI 子分數對 5/20/60 日 forward return 的相關性。
    """
    horizon: str
    factor: str
    forward_horizon: int  # 該 IC 量測的 forward return 天數
    ic: float | None
    ic_ir: float | None
    top_quintile_return: float | None
    bot_quintile_return: float | None
    n_dates: int
    avg_n_stocks: float
    ic_ci_lo: float | None = None  # 95% CI（Newey-West HAC）
    ic_ci_hi: float | None = None


def _fetch_subfactor_data(db: Database, lookback_days: int, max_horizon: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """讀 signal_history_factor_parts + 對應的 daily_price。
    參考 _fetch_data 的 lookback 計算。"""
    today = taipei_today()
    earliest = (today - timedelta(days=lookback_days + max_horizon + 30)).isoformat()
    with db.connect() as conn:
        parts = pd.read_sql_query(
            "SELECT as_of, stock_id, horizon, factor, score "
            "FROM signal_history_factor_parts WHERE as_of >= ?",
            conn, params=[earliest],
        )
        prices = read_close_with_adj_coalesced(conn, since=earliest)
    return parts, prices


def compute_subfactor_ic(
    db: Database,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> list[SubFactorICResult]:
    """對 signal_history_factor_parts 內每個 (horizon, factor) 子分數算 IC。

    輸出可拿來回答「短期分數整體 IC ≈ 0，是哪個子因子（RSI / KD / MA / foreign...）拖累？」。
    """
    if not horizons:
        return []
    parts, prices = _fetch_subfactor_data(db, lookback_days, max(horizons))
    if parts.empty or prices.empty:
        logger.info("subfactor_diagnostics: 資料不足 (parts=%d, prices=%d)", len(parts), len(prices))
        return []

    parts = parts.copy()
    parts["as_of"] = pd.to_datetime(parts["as_of"])
    price_wide = _build_price_pivot(prices)
    if price_wide.empty:
        return []

    # parts 表可能 ~ 50k row/day × 90 day = 4.5M 列，pivot_table 一次太大 →
    # 改 groupby (horizon, factor) 拆成 N 個小 pivot，每個 pivot 只用該因子的 row。
    # 同時把 price_wide 縮到 parts 涵蓋的 stock 集合（剔除權證等）。
    factor_stocks = parts["stock_id"].unique()
    common_cols = price_wide.columns.intersection(factor_stocks)
    if not common_cols.empty:
        price_wide = price_wide[common_cols]

    out: list[SubFactorICResult] = []
    # 預 rank forward_returns（per horizon）。21 個 sub-factor 共用，省 21 × 3 - 3 = 60 次 rank。
    forward_returns_by_h: dict[int, pd.DataFrame] = {}
    fwd_ranked_by_h: dict[int, _RankedFrame] = {}
    for horizon in horizons:
        forward_close = price_wide.shift(-horizon)
        fr = (forward_close / price_wide) - 1.0
        forward_returns_by_h[horizon] = fr
        fwd_ranked_by_h[horizon] = _rank_frame(fr)

    for (horizon_name, factor_name), grp in parts.groupby(["horizon", "factor"], sort=False):
        # pivot 此子因子分數：rows=as_of, cols=stock_id
        try:
            factor_pivot = grp.pivot_table(
                index="as_of", columns="stock_id", values="score", aggfunc="first",
            )
        except Exception as e:
            logger.warning("subfactor pivot 失敗 (%s/%s): %s", horizon_name, factor_name, e)
            continue
        # 預 rank 此 sub-factor（3 horizon 共用，省 2 次重複）
        factor_ranked = _rank_frame(factor_pivot)

        for fwd_h, forward_returns in forward_returns_by_h.items():
            ic_per_date, q5_per_date, q1_per_date, n_per_date = _vectorized_ic_metrics(
                factor_pivot, forward_returns,
                factor_ranked=factor_ranked, fwd_ranked=fwd_ranked_by_h[fwd_h],
            )

            if len(ic_per_date) < MIN_DATES_PER_FACTOR:
                out.append(SubFactorICResult(
                    horizon=str(horizon_name), factor=str(factor_name), forward_horizon=fwd_h,
                    ic=None, ic_ir=None,
                    top_quintile_return=None, bot_quintile_return=None,
                    n_dates=len(ic_per_date),
                    avg_n_stocks=float(np.mean(n_per_date)) if n_per_date else 0.0,
                ))
                continue

            mean_ic = float(np.mean(ic_per_date))
            std_ic = float(np.std(ic_per_date, ddof=1)) if len(ic_per_date) > 1 else 0.0
            ic_ir = mean_ic / std_ic if std_ic > 1e-9 else None
            ci_lo, ci_hi = _newey_west_mean_ci(ic_per_date, lag=max(1, fwd_h - 1))

            out.append(SubFactorICResult(
                horizon=str(horizon_name), factor=str(factor_name), forward_horizon=fwd_h,
                ic=mean_ic, ic_ir=ic_ir,
                top_quintile_return=float(np.mean(q5_per_date)) if q5_per_date else None,
                bot_quintile_return=float(np.mean(q1_per_date)) if q1_per_date else None,
                n_dates=len(ic_per_date),
                avg_n_stocks=float(np.mean(n_per_date)),
                ic_ci_lo=ci_lo, ic_ci_hi=ci_hi,
            ))
    return out


def subfactor_to_dict_list(results: list[SubFactorICResult]) -> list[dict]:
    return [asdict(r) for r in results]


# ======================================================================
# Cache layer：把 compute_* 結果存進 factor_ic_cache，避免每次 reload 4M 列
# ======================================================================

def _signal_history_max_as_of(db: Database) -> str | None:
    """回傳 cache key 用的 snapshot 鍵：`{IC_ALGO_VERSION}:{MAX(as_of)}:n{COUNT(DISTINCT as_of)}`。

    為什麼 key 也要含 distinct count：並行 backfill 可能先寫最新一天再倒著補舊資料。
    若 cache 只看 MAX(as_of)，會在 backfill 中途的 /diagnostics 請求觸發 IC 算「最新日 +
    部分舊日」並 cache 起來；後面更多舊日 backfill 完，MAX 沒變、cache key 沒變、cache hit
    回舊結果 → 永遠拿不到完整資料的 IC（2026-04-30 backfill 時實際踩到）。

    把 algo 版本 prefix 進去 → 改演算法 bump IC_ALGO_VERSION 就會讓 cache 命中失敗。
    舊版 row 不會回收（極小，DB 影響可忽略）。
    """
    with db.connect() as conn:
        row = conn.execute(
            "SELECT MAX(as_of) AS mx, COUNT(DISTINCT as_of) AS n FROM signal_history"
        ).fetchone()
    raw = row[0] if row and row[0] else None
    if raw is None:
        return None
    n = int(row[1] or 0)
    return f"{IC_ALGO_VERSION}:{raw}:n{n}"


def _cache_read(
    db: Database, *, scope: str, snapshot_max_as_of: str, lookback_days: int,
) -> list[dict] | None:
    """如果 cache 對應 (scope, snapshot_max_as_of, lookback_days) 有資料 → 回 list of dict；
    否則 None（caller 應觸發 live compute + write）。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT horizon, factor, forward_horizon, ic, ic_ir, top_quintile_return, "
            "       bot_quintile_return, n_dates, avg_n_stocks, ic_ci_lo, ic_ci_hi "
            "FROM factor_ic_cache "
            "WHERE scope=? AND snapshot_max_as_of=? AND lookback_days=?",
            (scope, snapshot_max_as_of, lookback_days),
        ).fetchall()
    if not rows:
        return None
    return [
        {
            "horizon": r["horizon"],
            "factor": r["factor"],
            "forward_horizon": r["forward_horizon"],
            "ic": r["ic"],
            "ic_ir": r["ic_ir"],
            "top_quintile_return": r["top_quintile_return"],
            "bot_quintile_return": r["bot_quintile_return"],
            "n_dates": r["n_dates"],
            "avg_n_stocks": r["avg_n_stocks"],
            "ic_ci_lo": r["ic_ci_lo"],
            "ic_ci_hi": r["ic_ci_hi"],
        }
        for r in rows
    ]


def _cache_write(
    db: Database, *, scope: str, snapshot_max_as_of: str, lookback_days: int,
    rows: list[dict],
) -> None:
    """寫入 cache。snapshot 推進後舊 rows 會留著，但因為不再被 PK 命中所以變成歷史；
    DB 體積增長極小（~80 列/snapshot）。需要的話 prune 可額外做。"""
    if not rows:
        return
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO factor_ic_cache "
            "(scope, snapshot_max_as_of, lookback_days, horizon, factor, forward_horizon, "
            " ic, ic_ir, top_quintile_return, bot_quintile_return, n_dates, avg_n_stocks, "
            " ic_ci_lo, ic_ci_hi, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    scope, snapshot_max_as_of, lookback_days,
                    str(r["horizon"]), r["factor"], int(r["forward_horizon"]),
                    r.get("ic"), r.get("ic_ir"),
                    r.get("top_quintile_return"), r.get("bot_quintile_return"),
                    r.get("n_dates", 0), r.get("avg_n_stocks", 0.0),
                    r.get("ic_ci_lo"), r.get("ic_ci_hi"),
                    now,
                )
                for r in rows
            ],
        )
        conn.commit()


def get_factor_ic_cached(
    db: Database, *, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> list[FactorICResult]:
    """Cache-first wrapper for compute_factor_ic。

    snapshot_max_as_of 不變 → 直接讀 cache（~10ms）。否則 live compute + 寫 cache。
    """
    max_as_of = _signal_history_max_as_of(db)
    if max_as_of is None:
        return []
    cached = _cache_read(db, scope="aggregate", snapshot_max_as_of=max_as_of, lookback_days=lookback_days)
    if cached is not None:
        # aggregate 的 horizon 等同 forward_horizon（FactorICResult.horizon 是 int）
        return [
            FactorICResult(
                factor=r["factor"], horizon=int(r["forward_horizon"]),
                ic=r["ic"], ic_ir=r["ic_ir"],
                top_quintile_return=r["top_quintile_return"],
                bot_quintile_return=r["bot_quintile_return"],
                n_dates=r["n_dates"], avg_n_stocks=r["avg_n_stocks"],
                ic_ci_lo=r.get("ic_ci_lo"), ic_ci_hi=r.get("ic_ci_hi"),
            )
            for r in cached
        ]

    # cache miss → live compute + write
    results = compute_factor_ic(db, lookback_days=lookback_days, horizons=horizons)
    rows_for_cache = [
        {
            "horizon": str(r.horizon),  # aggregate 的 horizon 是 int，寫成 str
            "factor": r.factor,
            "forward_horizon": r.horizon,
            "ic": r.ic, "ic_ir": r.ic_ir,
            "top_quintile_return": r.top_quintile_return,
            "bot_quintile_return": r.bot_quintile_return,
            "n_dates": r.n_dates, "avg_n_stocks": r.avg_n_stocks,
            "ic_ci_lo": r.ic_ci_lo, "ic_ci_hi": r.ic_ci_hi,
        }
        for r in results
    ]
    _cache_write(db, scope="aggregate", snapshot_max_as_of=max_as_of,
                 lookback_days=lookback_days, rows=rows_for_cache)
    return results


def get_subfactor_ic_cached(
    db: Database, *, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> list[SubFactorICResult]:
    """Cache-first wrapper for compute_subfactor_ic。"""
    max_as_of = _signal_history_max_as_of(db)
    if max_as_of is None:
        return []
    cached = _cache_read(db, scope="subfactor", snapshot_max_as_of=max_as_of, lookback_days=lookback_days)
    if cached is not None:
        return [
            SubFactorICResult(
                horizon=r["horizon"], factor=r["factor"], forward_horizon=int(r["forward_horizon"]),
                ic=r["ic"], ic_ir=r["ic_ir"],
                top_quintile_return=r["top_quintile_return"],
                bot_quintile_return=r["bot_quintile_return"],
                n_dates=r["n_dates"], avg_n_stocks=r["avg_n_stocks"],
                ic_ci_lo=r.get("ic_ci_lo"), ic_ci_hi=r.get("ic_ci_hi"),
            )
            for r in cached
        ]

    results = compute_subfactor_ic(db, lookback_days=lookback_days, horizons=horizons)
    rows_for_cache = [
        {
            "horizon": r.horizon, "factor": r.factor, "forward_horizon": r.forward_horizon,
            "ic": r.ic, "ic_ir": r.ic_ir,
            "top_quintile_return": r.top_quintile_return,
            "bot_quintile_return": r.bot_quintile_return,
            "n_dates": r.n_dates, "avg_n_stocks": r.avg_n_stocks,
            "ic_ci_lo": r.ic_ci_lo, "ic_ci_hi": r.ic_ci_hi,
        }
        for r in results
    ]
    _cache_write(db, scope="subfactor", snapshot_max_as_of=max_as_of,
                 lookback_days=lookback_days, rows=rows_for_cache)
    return results
