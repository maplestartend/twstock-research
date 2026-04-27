"""/api/backtest/* — 策略回測 / 投組 / 參數掃描 / Walk-forward。"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)

from api.common import (
    fmt_date as _fmt_date,
    get_stock_names as _name_map,
    safe_float as _sf,
    safe_float_or_zero as _f,
)
from api.deps import get_db
from api.schemas.backtest import (
    BacktestConfig,
    BacktestDailyPoint,
    BacktestRequest,
    BacktestResponse,
    BacktestSummary,
    BacktestTrade,
    EventBacktestRequest,
    EventBacktestResponse,
    EventBacktestSummary,
    EventTradeRow,
    GridSearchRequest,
    GridSearchResponse,
    GridSearchRow,
    PortfolioAggregate,
    PortfolioBacktestRequest,
    PortfolioBacktestResponse,
    PortfolioRow,
    StockEventStatsRow,
    WalkForwardRequest,
    WalkForwardResponse,
    WalkForwardSplitRow,
)
from app.backtest.engine import (
    StrategyConfig,
    backtest_portfolio,
    backtest_stock,
    benchmark_return,
    portfolio_summary,
    walk_forward,
    with_benchmarks,
)
from app.backtest.event_driven import EventConfig, run_event_backtest
from app.data.db import Database

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.post("/stock", response_model=BacktestResponse)
def backtest_single_stock(
    body: BacktestRequest,
    db: Database = Depends(get_db),
) -> BacktestResponse:
    """對單檔股票跑短期分數進出場策略回測。"""
    sid = body.stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="stock_id required")

    req_cfg = body.config or BacktestConfig()
    # 把 Pydantic 的 BacktestConfig 轉成 engine 的 StrategyConfig
    # engine 的 StrategyConfig.fee_rate=None 會自動套用 config.yaml 的預設
    strat = StrategyConfig(
        entry_threshold=req_cfg.entry_threshold,
        exit_threshold=req_cfg.exit_threshold,
        stop_loss_pct=req_cfg.stop_loss_pct,
        take_profit_pct=req_cfg.take_profit_pct,
        max_hold_days=req_cfg.max_hold_days,
        slippage_bps=req_cfg.slippage_bps,
        fee_rate=req_cfg.fee_rate,
        tax_rate=req_cfg.tax_rate,
        trailing_tp_mode=req_cfg.trailing_tp_mode,
        trailing_tp_atr_multiplier=req_cfg.trailing_tp_atr_multiplier,
        trailing_tp_arm_pnl=req_cfg.trailing_tp_arm_pnl,
        trailing_tp_arm_days=req_cfg.trailing_tp_arm_days,
        trailing_tp_atr_period=req_cfg.trailing_tp_atr_period,
    )

    try:
        result = backtest_stock(
            db, sid, strat,
            lookback_days=req_cfg.lookback_days,
            use_adj=req_cfg.use_adj,
        )
    except (ValueError, KeyError, IndexError) as e:
        # 已知資料缺失/邊界錯誤 → 422
        raise HTTPException(status_code=422, detail=f"回測失敗：{e}")
    except Exception as e:
        # 未預期錯誤 → log 完整 traceback 後 500
        logger.exception("backtest_stock(%s) 未預期錯誤", sid)
        raise HTTPException(status_code=500, detail=f"後端錯誤：{type(e).__name__}")

    # 股票名稱
    with db.connect() as conn:
        r = conn.execute("SELECT stock_name FROM stock_info WHERE stock_id=?", (sid,)).fetchone()
    name = (r["stock_name"] if r and r["stock_name"] else sid)

    s = result.summary()
    summary = BacktestSummary(
        stock_id=sid,
        stock_name=name,
        n_trades=int(s.get("n_trades", 0)),
        win_rate=float(s.get("win_rate", 0.0) or 0.0),
        avg_return=float(s.get("avg_return", 0.0) or 0.0),
        total_return=float(s.get("total_return", 0.0) or 0.0),
        max_drawdown=float(s.get("max_drawdown", 0.0) or 0.0),
        buy_and_hold=float(s.get("buy_and_hold", 0.0) or 0.0),
        alpha=float(s.get("alpha", 0.0) or 0.0),
        sharpe=_sf(s.get("sharpe")),
        sortino=_sf(s.get("sortino")),
        calmar=_sf(s.get("calmar")),
    )

    trades: list[BacktestTrade] = []
    for t in result.trades:
        trades.append(BacktestTrade(
            entry_date=_fmt_date(t.entry_date),
            exit_date=_fmt_date(t.exit_date),
            hold_days=int(t.hold_days),
            entry_price=float(t.entry_price),
            exit_price=float(t.exit_price),
            gross_return=float(t.gross_return),
            net_return=float(t.net_return),
            exit_reason=str(t.exit_reason),
        ))

    daily: list[BacktestDailyPoint] = []
    df = result.daily_series
    if df is not None and not df.empty:
        # 欄名可能是 date / close / short_score（或其他技術指標）
        for _, row in df.iterrows():
            d = row.get("date")
            daily.append(BacktestDailyPoint(
                date=_fmt_date(d) if d is not None else "",
                close=_sf(row.get("close")),
                short_score=_sf(row.get("short_score")),
            ))

    # 把 config 回傳（fee_rate=None 的情況已被 StrategyConfig.__post_init__ 填成實際值）
    resolved_cfg = BacktestConfig(
        entry_threshold=strat.entry_threshold,
        exit_threshold=strat.exit_threshold,
        stop_loss_pct=strat.stop_loss_pct,
        take_profit_pct=strat.take_profit_pct,
        max_hold_days=strat.max_hold_days,
        slippage_bps=strat.slippage_bps,
        fee_rate=strat.fee_rate,
        tax_rate=strat.tax_rate,
        lookback_days=req_cfg.lookback_days,
        use_adj=req_cfg.use_adj,
        trailing_tp_mode=strat.trailing_tp_mode,
        trailing_tp_atr_multiplier=strat.trailing_tp_atr_multiplier,
        trailing_tp_arm_pnl=strat.trailing_tp_arm_pnl,
        trailing_tp_arm_days=strat.trailing_tp_arm_days,
        trailing_tp_atr_period=strat.trailing_tp_atr_period,
    )

    return BacktestResponse(
        summary=summary,
        trades=trades,
        daily_series=daily,
        config=resolved_cfg,
    )


def _build_cfg(cfg: BacktestConfig | None) -> StrategyConfig:
    c = cfg or BacktestConfig()
    return StrategyConfig(
        entry_threshold=c.entry_threshold,
        exit_threshold=c.exit_threshold,
        stop_loss_pct=c.stop_loss_pct,
        take_profit_pct=c.take_profit_pct,
        max_hold_days=c.max_hold_days,
        slippage_bps=c.slippage_bps,
        fee_rate=c.fee_rate,
        tax_rate=c.tax_rate,
        trailing_tp_mode=c.trailing_tp_mode,
        trailing_tp_atr_multiplier=c.trailing_tp_atr_multiplier,
        trailing_tp_arm_pnl=c.trailing_tp_arm_pnl,
        trailing_tp_arm_days=c.trailing_tp_arm_days,
        trailing_tp_atr_period=c.trailing_tp_atr_period,
    )


@router.post("/portfolio", response_model=PortfolioBacktestResponse)
def backtest_portfolio_endpoint(
    body: PortfolioBacktestRequest,
    db: Database = Depends(get_db),
) -> PortfolioBacktestResponse:
    sids = [s.strip() for s in body.stock_ids if s and s.strip()]
    if not sids:
        raise HTTPException(status_code=400, detail="stock_ids 不可為空")
    if len(sids) > 50:
        raise HTTPException(status_code=413, detail="目前上限 50 檔，避免單次請求過久")

    cfg = body.config or BacktestConfig()
    strat = _build_cfg(cfg)
    try:
        summaries = backtest_portfolio(db, sids, strat, lookback_days=cfg.lookback_days, use_adj=cfg.use_adj)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"portfolio 回測失敗：{e}")

    if summaries is None or summaries.empty:
        return PortfolioBacktestResponse(summary=PortfolioAggregate(), rows=[], config=cfg)

    from app.data.clock import taipei_today
    today = taipei_today()
    end_iso = today.isoformat()
    start_iso = (today - timedelta(days=cfg.lookback_days)).isoformat()
    summaries_bm = with_benchmarks(db, summaries, start_iso, end_iso)
    agg = portfolio_summary(summaries_bm) or {}
    names = _name_map(db, sids)

    rows: list[PortfolioRow] = []
    for _, r in summaries_bm.iterrows():
        rows.append(PortfolioRow(
            stock_id=str(r["stock_id"]),
            stock_name=names.get(str(r["stock_id"])),
            n_trades=int(r.get("n_trades", 0) or 0),
            win_rate=_f(r.get("win_rate")),
            avg_return=_f(r.get("avg_return")),
            total_return=_f(r.get("total_return")),
            max_drawdown=_f(r.get("max_drawdown")),
            buy_and_hold=_f(r.get("buy_and_hold")),
            alpha=_f(r.get("alpha")),
            alpha_vs_0050=_sf(r.get("alpha_vs_0050")),
            alpha_vs_taiex=_sf(r.get("alpha_vs_taiex")),
            sharpe=_sf(r.get("sharpe")),
            sortino=_sf(r.get("sortino")),
            calmar=_sf(r.get("calmar")),
        ))
    # 依 alpha 降序
    rows.sort(key=lambda x: x.alpha, reverse=True)

    summary = PortfolioAggregate(
        n_stocks=int(agg.get("n_stocks", 0) or 0),
        n_with_trades=int(agg.get("n_with_trades", 0) or 0),
        avg_strategy_return=_f(agg.get("avg_strategy_return")),
        avg_buy_and_hold=_f(agg.get("avg_buy_and_hold")),
        avg_alpha=_f(agg.get("avg_alpha")),
        overall_winrate=_f(agg.get("overall_winrate")),
        bm_0050=_sf(benchmark_return(db, start_iso, end_iso, source="0050")),
        bm_taiex=_sf(benchmark_return(db, start_iso, end_iso, source="TAIEX")),
    )

    resolved = BacktestConfig(
        entry_threshold=strat.entry_threshold,
        exit_threshold=strat.exit_threshold,
        stop_loss_pct=strat.stop_loss_pct,
        take_profit_pct=strat.take_profit_pct,
        max_hold_days=strat.max_hold_days,
        slippage_bps=strat.slippage_bps,
        fee_rate=strat.fee_rate,
        tax_rate=strat.tax_rate,
        lookback_days=cfg.lookback_days,
        use_adj=cfg.use_adj,
        trailing_tp_mode=strat.trailing_tp_mode,
        trailing_tp_atr_multiplier=strat.trailing_tp_atr_multiplier,
        trailing_tp_arm_pnl=strat.trailing_tp_arm_pnl,
        trailing_tp_arm_days=strat.trailing_tp_arm_days,
        trailing_tp_atr_period=strat.trailing_tp_atr_period,
    )
    return PortfolioBacktestResponse(
        summary=summary, rows=rows, config=resolved,
        start_date=start_iso, end_date=end_iso,
    )


@router.post("/grid-search", response_model=GridSearchResponse)
def grid_search_endpoint(
    body: GridSearchRequest,
    db: Database = Depends(get_db),
) -> GridSearchResponse:
    """同步版 — 小網格用；超過 80 組會 4xx。"""
    sids = [s.strip() for s in body.stock_ids if s and s.strip()]
    if not sids:
        raise HTTPException(status_code=400, detail="stock_ids 不可為空")
    if len(sids) > 20:
        raise HTTPException(status_code=413, detail="網格掃描目前上限 20 檔")
    # 動態停利維度：空 list 代表走原本 4D 網格；非空則 5D，每個 k 都跑 trailing_tp_mode="both"
    k_list: list[float | None] = list(body.trailing_tp_k_list) if body.trailing_tp_k_list else [None]
    combos = (
        len(body.entry_list) * len(body.exit_list)
        * len(body.sl_list) * len(body.tp_list) * len(k_list)
    )
    if combos == 0:
        raise HTTPException(status_code=400, detail="網格不可為空")
    if combos > 80:
        raise HTTPException(status_code=413, detail=f"組合數 {combos} > 80，請縮小網格")

    started = time.time()
    rows: list[GridSearchRow] = []
    for e in body.entry_list:
        for x in body.exit_list:
            for sl in body.sl_list:
                for tp in body.tp_list:
                    for k in k_list:
                        cfg_kwargs = dict(
                            entry_threshold=float(e),
                            exit_threshold=float(x),
                            stop_loss_pct=float(sl),
                            take_profit_pct=float(tp),
                            max_hold_days=body.max_hold_days,
                            slippage_bps=body.slippage_bps,
                        )
                        if k is not None:
                            cfg_kwargs["trailing_tp_mode"] = "both"
                            cfg_kwargs["trailing_tp_atr_multiplier"] = float(k)
                        cfg = StrategyConfig(**cfg_kwargs)
                        try:
                            summaries = backtest_portfolio(db, sids, cfg, lookback_days=body.lookback_days)
                        except Exception as ex:
                            logger.debug(
                                "grid combo skipped entry=%s exit=%s sl=%s tp=%s k=%s: %s",
                                e, x, sl, tp, k, ex,
                            )
                            continue
                        agg = portfolio_summary(summaries) or {}
                        n_trades_total = int(summaries["n_trades"].sum()) if not summaries.empty else 0
                        rows.append(GridSearchRow(
                            entry=float(e), exit=float(x), sl=float(sl), tp=float(tp),
                            trailing_tp_k=float(k) if k is not None else None,
                            avg_alpha=_f(agg.get("avg_alpha")),
                            avg_total_return=_f(agg.get("avg_strategy_return")),
                            overall_winrate=_f(agg.get("overall_winrate")),
                            n_trades_total=n_trades_total,
                        ))
    rows.sort(key=lambda r: r.avg_alpha, reverse=True)
    best = rows[0] if rows else None
    return GridSearchResponse(
        combos=combos, rows=rows, best=best, elapsed_sec=round(time.time() - started, 2),
    )


@router.post("/walk-forward", response_model=WalkForwardResponse)
def walk_forward_endpoint(
    body: WalkForwardRequest,
    db: Database = Depends(get_db),
) -> WalkForwardResponse:
    sids = [s.strip() for s in body.stock_ids if s and s.strip()]
    if not sids:
        raise HTTPException(status_code=400, detail="stock_ids 不可為空")

    grid = []
    for e in body.entry_list:
        for x in body.exit_list:
            for sl in body.sl_list:
                for tp in body.tp_list:
                    grid.append(StrategyConfig(
                        entry_threshold=float(e),
                        exit_threshold=float(x),
                        stop_loss_pct=float(sl),
                        take_profit_pct=float(tp),
                        max_hold_days=body.max_hold_days,
                        slippage_bps=body.slippage_bps,
                    ))
    if not grid:
        raise HTTPException(status_code=400, detail="param_grid 不可為空")

    try:
        wf = walk_forward(db, sids, grid, n_splits=body.n_splits, train_ratio=body.train_ratio)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"walk-forward 失敗：{e}")

    if wf is None or wf.empty:
        return WalkForwardResponse(splits=[], note="資料不足以切分，請降低 n_splits 或縮短 train_ratio")

    splits: list[WalkForwardSplitRow] = []
    for _, r in wf.iterrows():
        splits.append(WalkForwardSplitRow(
            split=int(r.get("split", 0) or 0),
            train_period=str(r.get("train_period") or ""),
            test_period=str(r.get("test_period") or ""),
            best_entry=_sf(r.get("best_entry")),
            best_exit=_sf(r.get("best_exit")),
            train_return=_f(r.get("train_return")),
            train_sharpe=_sf(r.get("train_sharpe")),
            test_return=_f(r.get("test_return")),
            test_sharpe=_sf(r.get("test_sharpe")),
            test_alpha_0050=_sf(r.get("test_alpha_0050")),
            test_n_trades=int(r.get("test_n_trades", 0) or 0),
        ))
    avg_train = sum(s.train_return for s in splits) / len(splits) if splits else 0.0
    avg_test = sum(s.test_return for s in splits) / len(splits) if splits else 0.0
    # 過擬合：train 比 test 平均高 5 pp 以上、或 test 平均 <= 0
    overfit = (avg_train - avg_test >= 0.05) or (avg_test <= 0)
    return WalkForwardResponse(
        splits=splits,
        avg_train_return=round(avg_train, 4),
        avg_test_return=round(avg_test, 4),
        overfit_warning=overfit,
    )


@router.post("/event-driven", response_model=EventBacktestResponse)
def event_driven(
    body: EventBacktestRequest,
    db: Database = Depends(get_db),
) -> EventBacktestResponse:
    """除權息事件驅動回測。"""
    if not body.stock_ids:
        raise HTTPException(status_code=400, detail="stock_ids required")
    if len(body.stock_ids) > 100:
        raise HTTPException(status_code=400, detail="max 100 stocks per request")
    cfg = EventConfig(
        entry_offset=body.entry_offset,
        exit_offset=body.exit_offset,
        since_year=body.since_year,
        min_dividend=body.min_dividend,
    )
    result = run_event_backtest(db, body.stock_ids, cfg)

    summary = EventBacktestSummary(
        n_events=result.overall.n_events,
        n_with_data=result.overall.n_with_data,
        win_rate=_sf(result.overall.win_rate),
        avg_total_return=_sf(result.overall.avg_total_return),
        avg_price_return=_sf(result.overall.avg_price_return),
        avg_dividend_yield=_sf(result.overall.avg_dividend_yield),
        median_total_return=_sf(result.overall.median_total_return),
        best_return=_sf(result.overall.best_return),
        worst_return=_sf(result.overall.worst_return),
        total_dividend=result.overall.total_dividend,
    )
    by_stock = [
        StockEventStatsRow(
            stock_id=s.stock_id, stock_name=s.stock_name, n_events=s.n_events,
            win_rate=_sf(s.win_rate), avg_total_return=_sf(s.avg_total_return),
            avg_dividend_yield=_sf(s.avg_dividend_yield),
        ) for s in result.by_stock
    ]
    trades = [
        EventTradeRow(
            stock_id=t.stock_id, stock_name=t.stock_name, ex_date=t.ex_date, year=t.year,
            event_type=t.event_type,
            entry_date=t.entry_date, entry_price=_sf(t.entry_price),
            exit_date=t.exit_date, exit_price=_sf(t.exit_price),
            cash_dividend=t.cash_dividend, stock_dividend=t.stock_dividend,
            price_return=_sf(t.price_return), total_return=_sf(t.total_return),
        ) for t in result.trades
    ]
    return EventBacktestResponse(
        summary=summary, by_stock=by_stock, trades=trades, config_echo=body,
    )
