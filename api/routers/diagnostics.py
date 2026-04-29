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
    compute_factor_ic,
)

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


class FactorICRow(CamelModel):
    factor: str
    horizon: int
    ic: float | None
    ic_ir: float | None
    top_quintile_return: float | None
    bot_quintile_return: float | None
    n_dates: int
    avg_n_stocks: float


class FactorICResponse(CamelModel):
    lookback_days: int
    horizons: list[int]
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
    results = compute_factor_ic(db, lookback_days=lookback_days, horizons=horizons)
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
        )
        for r in results
    ]
    return FactorICResponse(
        lookback_days=lookback_days,
        horizons=list(horizons),
        rows=rows,
    )
