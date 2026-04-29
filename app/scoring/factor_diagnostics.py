"""因子有效性檢定：對 signal_history 內已寫入的分數做 forward-return Information Coefficient 分析。

回答的問題：「過去 N 天內，short / mid / long / composite / vr_macd 五個分數，對 5 / 20 / 60 日後的
報酬率到底有沒有預測力？」這是判斷分數權重該不該調整、要不要砍掉某條子線的客觀依據。

設計：
- 用 signal_history 的歷史快照 + daily_price.close 計算 forward return（trading days 概念，用 shift）
- IC 用 Spearman 等價（rank → Pearson），對極端值不敏感、不假設分佈
- IC_IR = mean(IC) / std(IC)：跨期穩定度，> 0.5 算有「持續」訊號
- Quintile spread = top 20% 的平均 forward return − bottom 20%；越大越有區分力
- 樣本守則：單日 < 30 檔（信號太稀）或全期 < 5 個 IC 點（樣本太少）→ 不算

複雜度：~120 dates × 5 factors × 3 horizons × pandas vectorized = 預期 < 30 秒（依 DB 大小）。
不快取結果——首次呼叫即時算，後續 Next.js 用 revalidate 60min 緩衝。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

from app.data.clock import taipei_today
from app.data.db import Database

logger = logging.getLogger(__name__)

FACTORS: tuple[str, ...] = ("short", "mid", "long", "composite", "vr_macd")
DEFAULT_HORIZONS: tuple[int, ...] = (5, 20, 60)
DEFAULT_LOOKBACK_DAYS = 120
MIN_SAMPLES_PER_DATE = 30
MIN_DATES_PER_FACTOR = 5


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


def _fetch_data(db: Database, lookback_days: int, max_horizon: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """一次讀完需要的 signal_history + daily_price，避免在 loop 裡反覆查 DB。"""
    today = taipei_today()
    # 多抓一個 horizon + buffer 30 天，確保最後一日的 forward return 還有資料
    earliest = (today - timedelta(days=lookback_days + max_horizon + 30)).isoformat()
    with db.connect() as conn:
        cols = ", ".join(FACTORS)
        snap = pd.read_sql_query(
            f"SELECT as_of, stock_id, {cols} FROM signal_history WHERE as_of >= ?",
            conn, params=[earliest],
        )
        prices = pd.read_sql_query(
            "SELECT date, stock_id, close FROM daily_price WHERE date >= ?",
            conn, params=[earliest],
        )
    return snap, prices


def _build_price_pivot(prices: pd.DataFrame) -> pd.DataFrame:
    """把 long-format daily_price 轉成 wide：rows=date, cols=stock_id, values=close。
    用作 shift(-h) 算 forward return 的基底。"""
    if prices.empty:
        return pd.DataFrame()
    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"])
    wide = prices.pivot(index="date", columns="stock_id", values="close").sort_index()
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
    if snap.empty or prices.empty:
        logger.info("factor_diagnostics: 資料不足 (snap=%d, prices=%d)", len(snap), len(prices))
        return []

    snap = snap.copy()
    snap["as_of"] = pd.to_datetime(snap["as_of"])
    price_wide = _build_price_pivot(prices)
    if price_wide.empty:
        return []

    out: list[FactorICResult] = []
    for horizon in horizons:
        # 用 trading-day shift（DataFrame 沒有 weekend rows，shift(-h) 自動跳）
        forward_close = price_wide.shift(-horizon)
        forward_returns = (forward_close / price_wide) - 1.0  # rows=date, cols=stock_id

        for factor in FACTORS:
            ic_per_date: list[float] = []
            q5_per_date: list[float] = []
            q1_per_date: list[float] = []
            n_per_date: list[int] = []

            # 對每個 as_of 日期跑 cross-sectional IC
            factor_pivot = snap.pivot_table(index="as_of", columns="stock_id", values=factor, aggfunc="first")
            common_dates = factor_pivot.index.intersection(forward_returns.index)
            for d in common_dates:
                x = factor_pivot.loc[d]
                # forward_returns 用 trading-day index；as_of 可能落在週末（snapshot_today 有寫週六/日?
                # 實務上 snapshot 都在交易日寫，d 應該在 forward_returns.index 內）
                y = forward_returns.loc[d]
                ic = _spearman_ic(x, y)
                if ic is None:
                    continue
                q5, q1 = _quintile_spread(x, y)
                ic_per_date.append(ic)
                if q5 is not None and q1 is not None:
                    q5_per_date.append(q5)
                    q1_per_date.append(q1)
                # 記錄樣本數（兩邊都非 NaN）
                n_per_date.append(int((x.notna() & y.notna()).sum()))

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

            out.append(FactorICResult(
                factor=factor, horizon=horizon,
                ic=mean_ic, ic_ir=ic_ir,
                top_quintile_return=float(np.mean(q5_per_date)) if q5_per_date else None,
                bot_quintile_return=float(np.mean(q1_per_date)) if q1_per_date else None,
                n_dates=len(ic_per_date),
                avg_n_stocks=float(np.mean(n_per_date)),
            ))

    return out


def to_dict_list(results: list[FactorICResult]) -> list[dict]:
    return [asdict(r) for r in results]
