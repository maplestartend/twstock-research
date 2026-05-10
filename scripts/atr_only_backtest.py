"""ATR-only buy-and-hold backtest，給 00631L all-in 場景比參數用。

跟 app/backtest/engine.py 不同：
- engine 是 score-driven（分數 >=65 才進場），不適合 ETF
- 這支是「day 0 全押、只用 ATR 規則決定出場」的純粹版

支援三種模式：
- B&H 基準：day 0 進、最後一天出，中間不動
- atr_only：ATR 觸發出場後永遠不再進
- atr_plus_trend：用 0050 跌破/站回 200MA 當再進場 / 強制出場訊號

成交假設：訊號當日收盤成交（為了實作簡單；真實情況應隔日開盤）。
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict

import pandas as pd

from app.config import Config
from app.data.db import Database
from app.portfolio import tax_rate_for
from app.risk import compute_atr

SLIPPAGE_BPS = 5.0


@dataclass
class SimResult:
    label: str
    stop_k: float
    tp_k: float
    mode: str
    n_trades: int
    total_return: float
    max_drawdown: float
    cagr: float
    avg_hold_days: float
    pct_in_market: float
    final_in_market: bool


def _load(db: Database, stock_id: str, start: str, end: str) -> pd.DataFrame:
    """從 daily_price_adj 讀還原後 OHLC（避免拆分污染）。"""
    with db.connect() as conn:
        df = pd.read_sql_query(
            """SELECT date,
                      open_adj  AS open,
                      high_adj  AS high,
                      low_adj   AS low,
                      close_adj AS close
               FROM daily_price_adj
               WHERE stock_id=? AND date BETWEEN ? AND ?
               ORDER BY date""",
            conn, params=[stock_id, start, end],
        )
    df["date"] = pd.to_datetime(df["date"])
    return df.reset_index(drop=True)


def _trend_series(db: Database, ref_id: str, ma: int, dates: pd.Series) -> pd.Series:
    """回傳 ref_id 在每個 dates 點上「收盤是否站上 ma 日均線」。"""
    with db.connect() as conn:
        df = pd.read_sql_query(
            "SELECT date, close_adj AS close FROM daily_price_adj WHERE stock_id=? ORDER BY date",
            conn, params=[ref_id],
        )
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df["ma"] = df["close"].rolling(ma, min_periods=ma).mean()
    df["above"] = df["close"] > df["ma"]
    return df["above"].reindex(dates).ffill().fillna(False).reset_index(drop=True)


def _build_entry_exit_signals(
    db: Database,
    ref_id: str,
    dates: pd.Series,
    mode: str,
    *,
    long_ma: int = 200,
    short_ma: int = 50,
    confirm_days: int = 5,
) -> tuple[pd.Series, pd.Series]:
    """根據 mode 產生 (can_enter, force_exit) 兩個布林序列。

    - simple_200ma : close > 200MA → 可進；< 200MA → 強制出
    - dual_ma      : close > 200MA AND 50MA > 200MA → 可進；close < 200MA → 強制出
    - confirmed    : 站上 200MA 連續 confirm_days 天 → 可進；close < 200MA → 強制出
    - cooldown     : simple_200ma 但出場後 cooldown 由 simulate 側強制 ── 這裡不處理
    """
    with db.connect() as conn:
        df = pd.read_sql_query(
            "SELECT date, close_adj AS close FROM daily_price_adj WHERE stock_id=? ORDER BY date",
            conn, params=[ref_id],
        )
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df["ma_l"] = df["close"].rolling(long_ma, min_periods=long_ma).mean()
    df["ma_s"] = df["close"].rolling(short_ma, min_periods=short_ma).mean()
    df["above_l"] = df["close"] > df["ma_l"]
    df["above_s_over_l"] = df["ma_s"] > df["ma_l"]

    if mode in ("simple_200ma", "cooldown"):
        can_enter = df["above_l"]
    elif mode == "dual_ma":
        can_enter = df["above_l"] & df["above_s_over_l"]
    elif mode == "confirmed":
        # 連續 confirm_days 天 above_l 都為 True
        can_enter = df["above_l"].rolling(confirm_days).sum() >= confirm_days
    else:
        raise ValueError(f"unknown trend mode: {mode}")

    force_exit = ~df["above_l"]  # 跌破 200MA 強制出場（所有模式共用）

    can_enter = can_enter.reindex(dates).ffill().fillna(False).reset_index(drop=True)
    force_exit = force_exit.reindex(dates).ffill().fillna(False).reset_index(drop=True)
    return can_enter, force_exit


def simulate(
    prices: pd.DataFrame,
    *,
    stop_k: float,
    tp_k: float,
    arm_pnl: float = 0.08,
    arm_days: int = 5,
    period: int = 14,
    fee_rate: float,
    tax_rate: float,
    slippage_bps: float = SLIPPAGE_BPS,
    trend_above: pd.Series | None = None,
    can_enter: pd.Series | None = None,
    force_exit: pd.Series | None = None,
    cooldown_days: int = 0,
    label: str = "",
    mode: str = "atr_only",
) -> SimResult:
    df = prices.copy()
    df["atr"] = compute_atr(df[["high", "low", "close"]], period)
    n = len(df)

    first_valid = df["atr"].first_valid_index()
    if first_valid is None or first_valid >= n - 2:
        return SimResult(label, stop_k, tp_k, mode, 0, 0.0, 0.0, 0.0, 0.0, 0.0, False)

    cost_buy = fee_rate + slippage_bps / 10000.0
    cost_sell = fee_rate + tax_rate + slippage_bps / 10000.0

    state = "flat"
    entry_idx = None
    entry_price = None
    entry_equity = None       # equity 進場後當下值（已扣 buy cost）
    peak_close = None
    peak_high = None

    equity = 1.0              # 已實現的 equity（不含正在 open 的浮動）
    peak_equity_mtm = 1.0
    max_dd = 0.0
    days_in_market = 0
    days_total = 0
    trades_hold_days: list[int] = []
    last_exit_idx: int | None = None  # 最近一次出場日 index，給 cooldown 用

    for i in range(first_valid + 1, n):
        row = df.iloc[i]
        close = float(row["close"])
        high = float(row["high"])
        atr = row["atr"]
        if pd.isna(atr) or atr <= 0:
            continue

        # 進出場訊號：優先用 can_enter/force_exit；退回相容 trend_above
        if can_enter is not None:
            entry_signal = bool(can_enter.iloc[i])
            exit_signal = bool(force_exit.iloc[i]) if force_exit is not None else False
        else:
            entry_signal = bool(trend_above.iloc[i]) if trend_above is not None else True
            exit_signal = (mode == "atr_plus_trend") and trend_above is not None and not entry_signal

        days_total += 1

        if state == "long":
            days_in_market += 1
            if high > peak_high:
                peak_high = high
            if close > peak_close:
                peak_close = close

            days_held = i - entry_idx
            pnl_pct = (close - entry_price) / entry_price
            armed = (pnl_pct >= arm_pnl) and (days_held >= arm_days)

            stop_line = peak_close - stop_k * atr
            tp_line = peak_high - tp_k * atr

            trig_stop = close < stop_line
            trig_tp = armed and close <= tp_line
            trig_trend = exit_signal

            # mark-to-market for MDD（即使不出場，也要追 drawdown）
            mtm_equity = entry_equity * (close / entry_price)
            if mtm_equity > peak_equity_mtm:
                peak_equity_mtm = mtm_equity
            dd = mtm_equity / peak_equity_mtm - 1
            if dd < max_dd:
                max_dd = dd

            if trig_stop or trig_tp or trig_trend:
                exit_value = entry_equity * (close / entry_price) * (1 - cost_sell)
                equity = exit_value
                if equity > peak_equity_mtm:
                    peak_equity_mtm = equity
                dd = equity / peak_equity_mtm - 1
                if dd < max_dd:
                    max_dd = dd
                trades_hold_days.append(days_held)
                last_exit_idx = i
                state = "flat"
                entry_idx = entry_price = entry_equity = None
                peak_close = peak_high = None
                continue  # 同日不再進場

        if state == "flat":
            if mode == "atr_only" and trades_hold_days:
                # 已出場過 → 永遠不再進
                continue
            # cooldown gate
            if cooldown_days > 0 and last_exit_idx is not None:
                if i - last_exit_idx < cooldown_days:
                    continue
            should_enter = entry_signal
            if should_enter:
                state = "long"
                entry_idx = i
                entry_price = close
                entry_equity = equity * (1 - cost_buy)
                peak_close = close
                peak_high = high

    # 收盤時還在場內 → 用最後收盤價估市值（不結算成本，反映「還沒賣」）
    final_in = (state == "long")
    if final_in and entry_price is not None:
        last_close = float(df["close"].iloc[-1])
        equity_final = entry_equity * (last_close / entry_price)
    else:
        equity_final = equity

    if equity_final > peak_equity_mtm:
        peak_equity_mtm = equity_final
    dd = equity_final / peak_equity_mtm - 1
    if dd < max_dd:
        max_dd = dd

    # CAGR
    n_days = (df["date"].iloc[-1] - df["date"].iloc[first_valid]).days
    years = max(n_days / 365.25, 1e-9)
    cagr = equity_final ** (1 / years) - 1 if equity_final > 0 else -1.0

    return SimResult(
        label=label,
        stop_k=stop_k,
        tp_k=tp_k,
        mode=mode,
        n_trades=len(trades_hold_days) + (1 if final_in else 0),
        total_return=equity_final - 1,
        max_drawdown=max_dd,
        cagr=cagr,
        avg_hold_days=(sum(trades_hold_days) / len(trades_hold_days)) if trades_hold_days else 0.0,
        pct_in_market=days_in_market / days_total if days_total else 0.0,
        final_in_market=final_in,
    )


def buy_and_hold(prices: pd.DataFrame, *, fee_rate: float, tax_rate: float,
                 slippage_bps: float = SLIPPAGE_BPS) -> SimResult:
    if prices.empty:
        return SimResult("B&H", 0, 0, "buy_and_hold", 0, 0, 0, 0, 0, 0, False)
    cost_buy = fee_rate + slippage_bps / 10000.0
    p0 = float(prices["close"].iloc[0])
    p_last = float(prices["close"].iloc[-1])
    equity = (1 - cost_buy) * (p_last / p0)  # 沒賣，不扣 sell cost
    n_days = (prices["date"].iloc[-1] - prices["date"].iloc[0]).days
    years = max(n_days / 365.25, 1e-9)

    # MDD: walk through close-to-close
    peak = 0.0
    max_dd = 0.0
    for i in range(len(prices)):
        eq_i = (1 - cost_buy) * (float(prices["close"].iloc[i]) / p0)
        if eq_i > peak:
            peak = eq_i
        dd = eq_i / peak - 1 if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd

    return SimResult(
        label="B&H",
        stop_k=0.0,
        tp_k=0.0,
        mode="buy_and_hold",
        n_trades=1,
        total_return=equity - 1,
        max_drawdown=max_dd,
        cagr=equity ** (1 / years) - 1 if equity > 0 else -1.0,
        avg_hold_days=float(len(prices)),
        pct_in_market=1.0,
        final_in_market=True,
    )


def run_grid(stock_id: str = "00631L", trend_ref: str = "0050",
             start: str = "2014-10-31", end: str = "2026-05-08") -> pd.DataFrame:
    cfg = Config.load()
    db = Database(cfg.database.path)
    fee_rate = 0.001425 * cfg.broker.fee_discount
    tax_rate = tax_rate_for(stock_id)

    prices = _load(db, stock_id, start, end)
    if prices.empty:
        raise SystemExit(f"no price data for {stock_id} in {start}~{end}")

    rows: list[SimResult] = [buy_and_hold(prices, fee_rate=fee_rate, tax_rate=tax_rate)]

    # 用固定 ATR 參數（前一輪測出 k_s=2.5/k_tp=3.0 是甜蜜點），比較再進場設計
    sk, tk = 2.5, 3.0
    trend_modes: list[tuple[str, int, int, int]] = [
        # (mode_key, long_ma, confirm_days, cooldown)
        ("simple_200ma", 200, 5, 0),
        ("dual_ma",      200, 5, 0),
        ("confirmed",    200, 5, 0),
        ("cooldown",     200, 5, 30),
        # 延伸測試：更敏感的再進場
        ("simple_100ma", 100, 5, 0),   # 100MA 取代 200MA
        ("cooldown",     200, 5, 10),  # cooldown 縮短到 10 天
        ("cooldown",     200, 5, 5),
    ]
    for mode_key, lma, cdays, cd in trend_modes:
        # 處理 simple_100ma：把 long_ma 換成 100
        actual_mode = "simple_200ma" if mode_key == "simple_100ma" else mode_key
        can_enter, force_exit = _build_entry_exit_signals(
            db, trend_ref, prices["date"], mode=actual_mode,
            long_ma=lma, confirm_days=cdays,
        )
        label_ma = f"{lma}MA" if mode_key in ("simple_200ma", "simple_100ma", "cooldown", "confirmed") else ""
        label = f"{mode_key} {label_ma}".strip()
        if cd:
            label += f" cd={cd}"
        r = simulate(
            prices,
            stop_k=sk, tp_k=tk,
            fee_rate=fee_rate, tax_rate=tax_rate,
            can_enter=can_enter, force_exit=force_exit,
            cooldown_days=cd,
            mode="atr_plus_trend",
            label=label,
        )
        rows.append(r)

    out = pd.DataFrame([asdict(r) for r in rows])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", default="00631L")
    ap.add_argument("--ref", default="0050", help="趨勢過濾用的參考標的")
    ap.add_argument("--start", default="2014-10-31")
    ap.add_argument("--end", default="2026-05-08")
    args = ap.parse_args()

    df = run_grid(args.stock, args.ref, args.start, args.end)

    # 顯示用：百分比格式化
    show = df.copy()
    for col in ("total_return", "max_drawdown", "cagr", "pct_in_market"):
        show[col] = (show[col] * 100).round(2)
    show["avg_hold_days"] = show["avg_hold_days"].round(0)

    print(f"\n=== ATR-only backtest: {args.stock} ({args.start} ~ {args.end}) ===")
    print(f"     fee_rate={0.001425 * Config.load().broker.fee_discount:.5f}, "
          f"tax={tax_rate_for(args.stock):.4f}, slippage=5bps")
    print(f"     單位：報酬/MDD/CAGR/%in_mkt 為 %\n")
    cols = ["label", "n_trades", "total_return", "cagr", "max_drawdown",
            "avg_hold_days", "pct_in_market", "final_in_market"]
    print(show[cols].to_string(index=False))

    # 排序：按 CAGR 高 → 低
    print("\n--- Top 5 by CAGR ---")
    print(show.sort_values("cagr", ascending=False).head(5)[cols].to_string(index=False))


if __name__ == "__main__":
    main()
