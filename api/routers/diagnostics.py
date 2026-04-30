"""/api/diagnostics/* — 因子有效性檢定（forward-return IC、IC_IR、quintile spread）。

讀 signal_history 已有的歷史快照算 IC，不會重跑 score_all（避免 30+ 秒的計算成本）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from api.schemas.common import CamelModel
from app.data.db import Database
from app.scoring.factor_diagnostics import (
    DEFAULT_HORIZONS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_ROLLING_WINDOW,
    compute_rolling_ic_for_horizon,
    get_factor_ic_cached,
    get_subfactor_ic_cached,
)

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

FORWARD_RETURN_BASIS = "close_to_close_adj"
EXECUTION_ASSUMPTION = (
    "因子值使用 as_of 當日收盤後可得資訊；forward return 以還原價 close-to-close 計算，"
    "未扣交易成本與滑價。"
)
IC_CI_METHOD = "newey_west_hac_lag_h_minus_1"


class FactorICRow(CamelModel):
    factor: str
    horizon: int
    ic: float | None
    ic_ir: float | None
    top_quintile_return: float | None
    bot_quintile_return: float | None
    n_dates: int
    avg_n_stocks: float
    ic_ci_lo: float | None = None  # 95% CI（Newey-West HAC）
    ic_ci_hi: float | None = None


class FactorICResponse(CamelModel):
    lookback_days: int
    horizons: list[int]
    forward_return_basis: str
    execution_assumption: str
    ic_ci_method: str
    rows: list[FactorICRow]


@router.get("/factor-ic", response_model=FactorICResponse)
def factor_ic(
    lookback_days: int = Query(default=DEFAULT_LOOKBACK_DAYS, ge=30, le=365),
    db: Database = Depends(get_db),
) -> FactorICResponse:
    """每個 (factor, horizon) 的 forward-return Information Coefficient + IC_IR + quintile spread。

    解讀：
    - `ic`：cross-sectional Spearman 相關，取所有日期平均。> 0.05 算有訊號、> 0.1 算強
    - `ic_ir`：mean/std，反映訊號穩定度。> 0.5 算可信賴的因子
    - `top_quintile_return - bot_quintile_return`：實際多空組合可獲取的 spread
    - 樣本不足（單日 < 30 檔 / 全期 < 5 個 IC 點）會回 null
    """
    horizons = DEFAULT_HORIZONS
    results = get_factor_ic_cached(db, lookback_days=lookback_days, horizons=horizons)
    rows = [
        FactorICRow(
            factor=r.factor,
            horizon=r.horizon,
            ic=r.ic,
            ic_ir=r.ic_ir,
            top_quintile_return=r.top_quintile_return,
            bot_quintile_return=r.bot_quintile_return,
            n_dates=r.n_dates,
            avg_n_stocks=r.avg_n_stocks,
            ic_ci_lo=r.ic_ci_lo,
            ic_ci_hi=r.ic_ci_hi,
        )
        for r in results
    ]
    return FactorICResponse(
        lookback_days=lookback_days,
        horizons=list(horizons),
        forward_return_basis=FORWARD_RETURN_BASIS,
        execution_assumption=EXECUTION_ASSUMPTION,
        ic_ci_method=IC_CI_METHOD,
        rows=rows,
    )


class SubFactorICRow(CamelModel):
    horizon: str
    factor: str
    forward_horizon: int
    ic: float | None
    ic_ir: float | None
    top_quintile_return: float | None
    bot_quintile_return: float | None
    n_dates: int
    avg_n_stocks: float
    ic_ci_lo: float | None = None
    ic_ci_hi: float | None = None


class SubFactorICResponse(CamelModel):
    lookback_days: int
    horizons: list[int]
    forward_return_basis: str
    execution_assumption: str
    ic_ci_method: str
    rows: list[SubFactorICRow]


@router.get("/sub-factor-ic", response_model=SubFactorICResponse)
def sub_factor_ic(
    lookback_days: int = Query(default=DEFAULT_LOOKBACK_DAYS, ge=30, le=365),
    db: Database = Depends(get_db),
) -> SubFactorICResponse:
    """子因子 IC 拆解：每個 (horizon, factor, forward_horizon) 的預測力。

    回答「短期分數整體 IC ≈ 0，是哪個子因子（rsi / kd / ma_alignment / foreign...）拖累？」。
    讀 signal_history_factor_parts 表，需先用 backfill_signal_history 寫入分數歷史；
    舊 schema（沒寫 parts）的 DB 會回空 rows。
    """
    horizons = DEFAULT_HORIZONS
    results = get_subfactor_ic_cached(db, lookback_days=lookback_days, horizons=horizons)
    rows = [
        SubFactorICRow(
            horizon=r.horizon,
            factor=r.factor,
            forward_horizon=r.forward_horizon,
            ic=r.ic,
            ic_ir=r.ic_ir,
            top_quintile_return=r.top_quintile_return,
            bot_quintile_return=r.bot_quintile_return,
            n_dates=r.n_dates,
            avg_n_stocks=r.avg_n_stocks,
            ic_ci_lo=r.ic_ci_lo,
            ic_ci_hi=r.ic_ci_hi,
        )
        for r in results
    ]
    return SubFactorICResponse(
        lookback_days=lookback_days,
        horizons=list(horizons),
        forward_return_basis=FORWARD_RETURN_BASIS,
        execution_assumption=EXECUTION_ASSUMPTION,
        ic_ci_method=IC_CI_METHOD,
        rows=rows,
    )


# ======================================================================
# Rolling IC：跨 regime 的 IC 演進折線圖用
# ======================================================================

class RollingICRow(CamelModel):
    date: str  # YYYY-MM-DD
    short: float | None = None
    mid: float | None = None
    long: float | None = None
    composite: float | None = None
    vr_macd: float | None = None


class RollingICResponse(CamelModel):
    horizon: int
    window: int
    lookback_days: int
    rows: list[RollingICRow]


@router.get("/rolling-ic", response_model=RollingICResponse)
def rolling_ic(
    horizon: int = Query(default=20, ge=1, le=120),
    window: int = Query(default=DEFAULT_ROLLING_WINDOW, ge=5, le=120),
    lookback_days: int = Query(default=DEFAULT_LOOKBACK_DAYS, ge=30, le=2000),
    db: Database = Depends(get_db),
) -> RollingICResponse:
    """每個 aggregate factor 在指定 horizon 下的 N-day rolling cross-sectional IC。

    用於 /diagnostics 折線圖檢測 regime shift：例如 mid trend 在 2024 才開始 work / 持續 work / 從未 work。

    horizon 預設 20 日（5d 受微結構雜訊大、60d window 重疊嚴重），window 預設 30 日。
    """
    rows_data = compute_rolling_ic_for_horizon(
        db, horizon=horizon, window=window, lookback_days=lookback_days,
    )
    rows = [
        RollingICRow(
            date=r["date"],
            short=r.get("short"),
            mid=r.get("mid"),
            long=r.get("long"),
            composite=r.get("composite"),
            vr_macd=r.get("vr_macd"),
        )
        for r in rows_data
    ]
    return RollingICResponse(
        horizon=horizon, window=window, lookback_days=lookback_days, rows=rows,
    )
