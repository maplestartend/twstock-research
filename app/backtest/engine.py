"""簡易回測引擎：給定策略，對單檔股票逐日產生短期分數，執行進出場規則。

定位：驗證評分方法論的歷史績效；不是高頻策略回測框架。
假設：單倉位、隔日開盤成交、不計交易成本（可設手續費）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

from app.data.adjuster import load_adjusted_price
from app.data.db import Database
from app.indicators import chips as chip_ind
from app.indicators import technical as tech
from app.portfolio import tax_rate_for
from app.scoring import rubric as R

# 台股 TWSE/TPEX 漲跌停為 ±10%，以「前一交易日收盤價」為基準。
# 開盤即漲停 → 買不到；開盤即跌停 → 賣不掉。回測在這兩個 case 不應記錄成交，
# 否則績效會虛高（買到根本拿不到的標的）或虛低（強迫平掉本來不能平的部位）。
LIMIT_UP_PCT = 0.10
LIMIT_DOWN_PCT = 0.10
# 取浮點寬限避免 prev_close * 1.10 因小數誤差被判為「未到漲停」
_LIMIT_EPS = 1e-9


def _default_fee_rate() -> float:
    """讀 config 的券商折扣套用到手續費。"""
    try:
        from app.config import Config
        return 0.001425 * Config.load().broker.fee_discount
    except Exception:
        return 0.001425


@dataclass
class StrategyConfig:
    entry_threshold: float = 65.0       # 短期分數 >= 這個值才進場
    exit_threshold: float = 40.0        # 短期分數 <= 這個值時出場
    stop_loss_pct: float = 0.08         # 固定停損 %
    take_profit_pct: float = 0.20       # 固定停利 %（保底；趨勢沒回撤就衝過 20% 由它接手）
    max_hold_days: int = 60             # 最長持有天數（保險）
    fee_rate: float = None              # 手續費（單邊），None = 套用 config 折扣
    tax_rate: float = 0.003             # 證交稅（僅賣方）
    slippage_bps: float = 5.0           # 滑價（basis points，5 bps = 0.05%）；買時漲、賣時跌
    # ATR 動態停利（Chandelier-style，與 trailing_atr_take_profit() 對齊）
    # mode: "off"  = 只用固定停利 take_profit_pct
    #       "both" = 動態與固定並存，誰先觸發先出（推薦）
    #       "only" = 只用動態停利，忽略 take_profit_pct
    trailing_tp_mode: str = "off"
    trailing_tp_atr_multiplier: float = 3.0   # K，停損用 2.0；停利給趨勢更多呼吸空間
    trailing_tp_arm_pnl: float = 0.08         # 浮盈門檻，避免進場初期被洗
    trailing_tp_arm_days: int = 5             # 持有日門檻
    trailing_tp_atr_period: int = 14

    def __post_init__(self):
        if self.fee_rate is None:
            self.fee_rate = _default_fee_rate()


@dataclass
class Trade:
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    hold_days: int
    gross_return: float
    net_return: float
    exit_reason: str


@dataclass
class BacktestResult:
    stock_id: str
    config: StrategyConfig
    trades: list[Trade] = field(default_factory=list)
    daily_series: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.net_return > 0)
        return wins / len(self.trades)

    @property
    def avg_return(self) -> float:
        if not self.trades:
            return 0.0
        return float(np.mean([t.net_return for t in self.trades]))

    @property
    def total_return(self) -> float:
        """所有交易複利累積報酬率。"""
        ret = 1.0
        for t in self.trades:
            ret *= (1 + t.net_return)
        return ret - 1

    @property
    def max_drawdown(self) -> float:
        """真實最大回撤：用每日 mark-to-market 的 equity 曲線。

        舊版（trade-by-trade equity）會嚴重低估 MDD：
            一筆 trade 持有期內最低點 -30%，但因觸發 -8% 停損出場 → 舊版只記 -8%。
        新版按 daily_series 走，trade 持有期間 equity = prev_equity × (close[d] / entry_price)，
        進場日與出場日的 fee/tax/slippage 一併扣在 equity 上，反映 intraday 最低點。
        無 trade 時回 0；no daily_series（極小 fixture）退回舊版 trade-by-trade 算法。
        """
        if not self.trades:
            return 0.0
        if self.daily_series.empty:
            # fallback：和舊版一致，避免單元測試 fixture 沒 daily_series 時爆
            equity = [1.0]
            for t in self.trades:
                equity.append(equity[-1] * (1 + t.net_return))
            eq = pd.Series(equity)
            peak = eq.cummax()
            dd = (eq - peak) / peak
            return float(dd.min())

        # Build daily equity series.
        ds = self.daily_series.copy()
        ds["date"] = pd.to_datetime(ds["date"])
        equity = pd.Series(1.0, index=ds.index)

        for t in self.trades:
            entry_d = pd.to_datetime(t.entry_date)
            exit_d = pd.to_datetime(t.exit_date)
            # 找到 trade 期間在 daily_series 上的範圍
            mask = (ds["date"] >= entry_d) & (ds["date"] <= exit_d)
            if not mask.any():
                continue
            idxs = ds.index[mask]
            # 在 entry 那天 equity 立即被「滑價 + 進場手續費」拉低 → 用 entry_price 做 baseline
            # 持有期間每日 mark-to-market: equity_d / equity_pre = close[d] / entry_price
            entry_idx = idxs[0]
            base_equity = float(equity.iloc[max(entry_idx - 1, 0)])
            for ix in idxs:
                close_d = float(ds.iloc[ix]["close"])
                if t.entry_price <= 0:
                    continue
                # 在 trade 中第 i 天：mtm
                equity.iloc[ix] = base_equity * (close_d / t.entry_price)
            # exit 那天 equity 用實際 net_return（已扣 fee+tax+slip）覆蓋
            exit_idx = idxs[-1]
            equity.iloc[exit_idx] = base_equity * (1 + t.net_return)
            # trade 之後直到下一筆 trade，equity 持平在 exit equity
            if exit_idx + 1 < len(equity):
                equity.iloc[exit_idx + 1:] = equity.iloc[exit_idx]

        peak = equity.cummax()
        dd = (equity - peak) / peak
        return float(dd.min())

    @property
    def buy_and_hold_return(self) -> float:
        """B&H 報酬：扣一次 entry+exit 的手續費 + 賣方證交稅 + 雙向滑價，
        與策略採同等成本基準才能公平算 alpha。稅率已由 backtest_stock 在進入時
        透過 dataclasses.replace(cfg, tax_rate=tax_rate_for(stock_id)) 設定好。"""
        if self.daily_series.empty:
            return 0.0
        first, last = self.daily_series["close"].iloc[0], self.daily_series["close"].iloc[-1]
        if first <= 0:
            return 0.0
        gross = (last - first) / first
        fees = self.config.fee_rate * 2 + self.config.tax_rate
        slip = self.config.slippage_bps / 10_000 * 2
        return gross - fees - slip

    @property
    def sharpe_ratio(self) -> float | None:
        """Per-trade Sharpe = mean(net_return) / std(net_return)。

        簡化版（未年化）：交易筆數 n 通常 < 252 而且不等距，做標準年化反而誤導。
        對自用回測場景，此值仍可用於跨策略 / 跨參數的相對比較（高比低好）。
        n < 2 → None（樣本太小無法算波動）。"""
        if len(self.trades) < 2:
            return None
        rets = pd.Series([t.net_return for t in self.trades], dtype=float)
        sd = float(rets.std(ddof=1))
        if sd <= 1e-9:
            return None
        return round(float(rets.mean()) / sd, 4)

    @property
    def sortino_ratio(self) -> float | None:
        """Per-trade Sortino = mean(net_return) / std(只算 < 0 的 trade)。

        比 Sharpe 更貼策略人實際在意的——上行波動是好事，只有下行才是風險。
        n < 2、或沒有任何虧損 trade（std 沒意義）→ None。"""
        if len(self.trades) < 2:
            return None
        rets = [t.net_return for t in self.trades]
        downside = [r for r in rets if r < 0]
        if len(downside) < 2:
            return None
        sd_down = float(pd.Series(downside, dtype=float).std(ddof=1))
        if sd_down <= 1e-9:
            return None
        return round(float(np.mean(rets)) / sd_down, 4)

    @property
    def calmar_ratio(self) -> float | None:
        """Calmar = total_return / |max_drawdown|。

        衡量「為了賺到這個累積報酬，最壞時期賠了多少」。
        max_drawdown 太小（< 1%）視為樣本不足，回 None 避免分母小到爆數。"""
        mdd = self.max_drawdown
        if abs(mdd) < 0.01:
            return None
        return round(self.total_return / abs(mdd), 4)

    def summary(self) -> dict[str, Any]:
        return {
            "stock_id": self.stock_id,
            "n_trades": self.n_trades,
            "win_rate": round(self.win_rate, 3),
            "avg_return": round(self.avg_return, 4),
            "total_return": round(self.total_return, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "buy_and_hold": round(self.buy_and_hold_return, 4),
            "alpha": round(self.total_return - self.buy_and_hold_return, 4),
            "sharpe": self.sharpe_ratio,
            "sortino": self.sortino_ratio,
            "calmar": self.calmar_ratio,
        }


# ======================================================================
# 歷史短期評分（向量化/逐列）
# ======================================================================
def _streak_series(series: pd.Series, sign: int) -> pd.Series:
    """向量化計算「截至每一列為止的同方向連續天數」(sign=1 連續正、sign=-1 連續負)。

    取代 chip_ind.consecutive_days 的 reverse-loop 用法。每根 K 棒做一次 tail(30) +
    reverse loop 是 O(N×30)，這個版本是 O(N)：用 cumsum 找 reset 點 → groupby cumcount 一次到位。
    """
    if series.empty:
        return pd.Series(dtype=float, index=series.index)
    is_match = (series > 0) if sign > 0 else (series < 0)
    # 每次 is_match 變 False 就重設一個新 group；cumcount 在 group 內遞增 = 連續天數
    reset_groups = (~is_match).cumsum()
    streak = is_match.astype(int).groupby(reset_groups).cumsum()
    return streak


def _vectorized_short_scores(price: pd.DataFrame, inst: pd.DataFrame, margin: pd.DataFrame) -> pd.DataFrame:
    """對整段價格序列逐日計算短期分數，回傳 DataFrame(date, short_score)。

    chip 部分已 vectorize（連續天數用 groupby cumcount，cum20 用 enrich_institutional 預算），
    技術指標 rubric 仍逐列呼叫（rewrite 為向量化要動 rubric.py 與多個 test，不在此 PR 範圍內）。
    """
    if price.empty:
        return pd.DataFrame(columns=["date", "short_score"])
    price = tech.enrich(price)

    # 預先把 chip 時間序列算好
    if not inst.empty:
        inst = chip_ind.enrich_institutional(inst)
        # 連續天數一次算到底（取代過去逐日 tail(30) + reverse-loop 的 O(N²)）
        inst = inst.copy()
        inst["_foreign_streak_buy"] = _streak_series(inst["foreign_net"], 1)
        inst["_foreign_streak_sell"] = _streak_series(inst["foreign_net"], -1)
        inst["_trust_streak_buy"] = _streak_series(inst["investment_trust_net"], 1)
        inst["_trust_streak_sell"] = _streak_series(inst["investment_trust_net"], -1)
    if not margin.empty:
        margin = chip_ind.enrich_margin(margin)

    inst_by_date = inst.set_index("date") if not inst.empty else pd.DataFrame()
    margin_by_date = margin.set_index("date") if not margin.empty else pd.DataFrame()

    scores: list[float] = []
    for i, row in enumerate(price.itertuples(index=False)):
        last = pd.Series(row._asdict())
        prev = pd.Series(price.iloc[i - 1].to_dict()) if i > 0 else None
        d = last["date"]

        # chip snapshot：所有欄位都已 vectorize 預算，這裡只是 dict lookup
        chip_snap: dict = {}
        if not inst_by_date.empty and d in inst_by_date.index:
            row_d = inst_by_date.loc[d]
            chip_snap["foreign_streak_buy"] = int(row_d.get("_foreign_streak_buy", 0) or 0)
            chip_snap["foreign_streak_sell"] = int(row_d.get("_foreign_streak_sell", 0) or 0)
            chip_snap["trust_streak_buy"] = int(row_d.get("_trust_streak_buy", 0) or 0)
            chip_snap["trust_streak_sell"] = int(row_d.get("_trust_streak_sell", 0) or 0)
            chip_snap["foreign_cum20"] = float(row_d.get("foreign_cum20", 0) or 0)
            chip_snap["trust_cum20"] = float(row_d.get("trust_cum20", 0) or 0)
        if not margin_by_date.empty and d in margin_by_date.index:
            row_d = margin_by_date.loc[d]
            chip_snap["margin_chg5"] = float(row_d.get("margin_balance_chg5", 0) or 0)

        parts = {
            "ma_alignment": R.score_ma_alignment_short(last),
            "kd": R.score_kd(last, prev),
            "macd": R.score_macd(last, prev),
            "rsi": R.score_rsi(last),
            "bollinger": R.score_bollinger(last),
            "volume": R.score_volume(last),
            "foreign": R.score_foreign_short(chip_snap),
            "trust": R.score_trust_short(chip_snap),
            "margin_change": R.score_margin_change(chip_snap),
        }
        # None-aware 加權：跳過 None 子項並用剩下權重 re-normalize；
        # 若有效權重 < MIN_DIM_COMPLETENESS 視為當日資料不足、推 NaN（後續比較會被 isnan 跳過）
        used_w = 0.0
        acc = 0.0
        total_w = sum(R.SHORT_TERM_WEIGHTS.get(k, 0) for k in parts)
        for k, v in parts.items():
            if v is None:
                continue
            w = R.SHORT_TERM_WEIGHTS.get(k, 0)
            used_w += w
            acc += v * w
        if used_w > 0 and total_w > 0 and (used_w / total_w) >= R.MIN_DIM_COMPLETENESS:
            scores.append(acc / used_w)
        else:
            scores.append(float("nan"))

    return pd.DataFrame({
        "date": price["date"].values,
        "close": price["close"].values,
        "open": price["open"].values,
        # high/low 留給 ATR-based 動態停利等指標用；缺值（早期資料）退回 close
        "high": price["high"].values if "high" in price.columns else price["close"].values,
        "low": price["low"].values if "low" in price.columns else price["close"].values,
        "short_score": scores,
    })


def _load_stock_data(db: Database, stock_id: str, use_adj: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """載入股價 + 籌碼 + 融資。use_adj=True 時，open/high/low/close 會用還原價（若有）。"""
    if use_adj:
        merged = load_adjusted_price(db, stock_id)
        if not merged.empty:
            price = merged[["date", "stock_id", "open_adj", "high_adj", "low_adj", "close_adj", "volume", "amount", "turnover", "spread"]].rename(
                columns={"open_adj": "open", "high_adj": "high", "low_adj": "low", "close_adj": "close"}
            )
        else:
            price = db.load_daily_price(stock_id)
    else:
        price = db.load_daily_price(stock_id)

    inst = db.load_institutional(stock_id)
    with db.connect() as conn:
        margin = pd.read_sql_query(
            "SELECT * FROM margin WHERE stock_id=? ORDER BY date", conn, params=[stock_id]
        )
    if not margin.empty:
        margin["date"] = pd.to_datetime(margin["date"])
    return price, inst, margin


# ======================================================================
# 主回測函式
# ======================================================================
def backtest_stock(db: Database, stock_id: str, cfg: StrategyConfig | None = None, lookback_days: int = 500, use_adj: bool = True) -> BacktestResult:
    if cfg is None:
        cfg = StrategyConfig()

    # 依代號套對應稅率（ETF 0.1%、債券 ETF 0%、其他 0.3%）。原 cfg 不可變動，做一份 copy。
    cfg = replace(cfg, tax_rate=tax_rate_for(stock_id))

    price, inst, margin = _load_stock_data(db, stock_id, use_adj=use_adj)
    if price.empty or len(price) < 100:
        return BacktestResult(stock_id=stock_id, config=cfg)

    series = _vectorized_short_scores(price, inst, margin)
    series = series.tail(lookback_days + 30).reset_index(drop=True)  # 額外 buffer 讓指標穩定

    trades = _run_strategy_loop(series, cfg)
    return BacktestResult(
        stock_id=stock_id,
        config=cfg,
        trades=trades,
        daily_series=series,
    )


def _trailing_tp_triggered(
    series: pd.DataFrame,
    i: int,
    entry_idx: int,
    entry_price: float,
    hold_days: int,
    atr_series: pd.Series,
    cfg: StrategyConfig,
) -> bool:
    """Chandelier 動態停利觸發判斷（搭配 _run_strategy_loop 用）。

    armed = (浮盈 ≥ trailing_tp_arm_pnl) AND (持有 ≥ trailing_tp_arm_days)
    triggered = armed AND (close ≤ peak_high_since_entry − K×ATR)
    """
    if atr_series is None:
        return False
    last_atr = atr_series.iloc[i]
    if pd.isna(last_atr) or last_atr <= 0:
        return False
    close = float(series.iloc[i]["close"])
    pnl_pct = (close - entry_price) / entry_price
    if pnl_pct < cfg.trailing_tp_arm_pnl or hold_days < cfg.trailing_tp_arm_days:
        return False
    peak = float(series.iloc[entry_idx : i + 1]["high"].max())
    tp_line = peak - cfg.trailing_tp_atr_multiplier * float(last_atr)
    return close <= tp_line


def _run_strategy_loop(series: pd.DataFrame, cfg: StrategyConfig) -> list[Trade]:
    """純函式版策略迴圈：series 已含 open/close/short_score/date，回傳成交清單。

    抽出讓 unit test 直接灌假資料驗 (a) 漲跌停跳過、(b) walk-forward flat-reset、
    (c) 滑價/手續費/證交稅扣除、(d) 跌停連板 exit_reason 保留。
    """
    in_position = False
    entry_idx = 0
    entry_price = 0.0
    # 跌停延後出場：當期觸發但 next_open 跌停 → 把 exit_reason 暫存到下根 bar 再試。
    # 否則「連續跌停 N 天」會在第一根撞 limit_down 後 continue，下根重新算 change/score
    # 可能因價格更低而錯失原本的 stop_loss/score_exit 訊號（金融分析師審查 #7）。
    pending_exit_reason: str | None = None
    trades: list[Trade] = []

    # 動態停利所需的 ATR 序列：mode != "off" 才預算，避免吃掉預設情境的效能
    use_trailing_tp = cfg.trailing_tp_mode != "off" and "high" in series.columns and "low" in series.columns
    atr_series = None
    if use_trailing_tp:
        from app.risk import compute_atr  # 局部 import 避開循環依賴
        atr_series = compute_atr(series[["high", "low", "close"]], cfg.trailing_tp_atr_period)

    for i in range(1, len(series) - 1):  # -1 因為要用次日開盤價成交
        row = series.iloc[i]
        score = row["short_score"]
        close = row["close"]
        next_open = series.iloc[i + 1]["open"]

        if np.isnan(score):
            continue

        slip = cfg.slippage_bps / 10_000  # bps → 小數
        # 漲跌停判斷：以今日收盤為基準，次日開盤是否觸及 ±10%
        limit_up = float(next_open) >= float(close) * (1 + LIMIT_UP_PCT) - _LIMIT_EPS
        limit_down = float(next_open) <= float(close) * (1 - LIMIT_DOWN_PCT) + _LIMIT_EPS

        if not in_position:
            if score >= cfg.entry_threshold:
                # 漲停板進場無法成交（沒有對手單），跳過此訊號
                if limit_up:
                    continue
                in_position = True
                entry_idx = i + 1
                # 滑價：買方向上滑（付出比 open 略高）
                entry_price = float(next_open) * (1 + slip)
        else:
            hold_days = i - entry_idx + 1
            change = (close - entry_price) / entry_price
            # 沿用上根 bar 的 pending exit（跌停延後）；沒有就重新評估
            exit_reason: str | None = pending_exit_reason

            if exit_reason is None:
                # exit 優先序（金融分析師建議）：
                #   1. stop_loss          — 保命第一
                #   2. trailing_take_profit — 鎖獲利（armed 後才生效）
                #   3. take_profit        — 固定 % 保底
                #   4. score_exit         — rubric 跌破
                #   5. max_hold           — 兜底
                if change <= -cfg.stop_loss_pct:
                    exit_reason = "stop_loss"
                elif use_trailing_tp and _trailing_tp_triggered(
                    series, i, entry_idx, entry_price, hold_days, atr_series, cfg,
                ):
                    exit_reason = "trailing_take_profit"
                elif cfg.trailing_tp_mode != "only" and change >= cfg.take_profit_pct:
                    exit_reason = "take_profit"
                elif score <= cfg.exit_threshold:
                    exit_reason = "score_exit"
                elif hold_days >= cfg.max_hold_days:
                    exit_reason = "max_hold"

            if exit_reason:
                # 跌停板出場無法成交（沒有買盤），保留 exit_reason、下一根 bar 再試
                if limit_down:
                    pending_exit_reason = exit_reason
                    continue
                # 順利出場：清掉 pending
                pending_exit_reason = None
                # 滑價：賣方向下滑（收到比 open 略低）
                exit_price = float(next_open) * (1 - slip)
                gross = (exit_price - entry_price) / entry_price
                # 手續費：買+賣各 fee_rate；證交稅只賣方
                fees = cfg.fee_rate * 2 + cfg.tax_rate
                net = gross - fees
                trades.append(Trade(
                    entry_date=series.iloc[entry_idx]["date"],
                    exit_date=series.iloc[i + 1]["date"],
                    entry_price=entry_price,
                    exit_price=exit_price,
                    hold_days=hold_days,
                    gross_return=round(gross, 4),
                    net_return=round(net, 4),
                    exit_reason=exit_reason,
                ))
                in_position = False

    return trades


def backtest_portfolio(db: Database, stock_ids: list[str], cfg: StrategyConfig | None = None, lookback_days: int = 500, use_adj: bool = True) -> pd.DataFrame:
    """**等權獨立回測**：對每檔股票分別跑單檔回測再彙總，**不是真正的 portfolio backtest**。

    特性與限制（CFA 角度）：
    - 沒有共用資金帳戶 → 同時可以 N 檔都「滿倉」買入，現金約束被跳過
    - 沒有持倉上限 → 不會因為達到最大倉位數而拒絕進場
    - 沒有再平衡 → 各檔報酬獨立累積，最後取等權算術平均
    - 無相關性 / 風險預算管理

    這個輸出對「策略本身在多檔上的勝率與平均報酬」有意義，
    但不能用來推估「實際拿一筆資金跑多檔的組合績效」。UI 必須明示這是「多檔等權平均」而非 portfolio。
    """
    rows = []
    for sid in stock_ids:
        r = backtest_stock(db, sid, cfg, lookback_days, use_adj=use_adj)
        rows.append({**r.summary(), "n_trades": r.n_trades})
    return pd.DataFrame(rows)


# ======================================================================
# 基準 (Benchmark) 比對
# ======================================================================
def benchmark_return(
    db: Database,
    start_date: str,
    end_date: str,
    *,
    source: str = "0050",
) -> float | None:
    """指定區間的基準 Buy & Hold 報酬。

    Args:
        source: '0050' 走 daily_price、'TAIEX' 走 index_daily '發行量加權股價指數'

    回傳 None：資料不足。
    """
    with db.connect() as conn:
        if source.upper() == "TAIEX":
            row = conn.execute(
                "SELECT date, close FROM index_daily "
                "WHERE index_name='發行量加權股價指數' AND date BETWEEN ? AND ? "
                "ORDER BY date",
                (start_date, end_date),
            ).fetchall()
        else:
            # 優先用還原價（處理分割/除權息），退而求其次用原始收盤
            row = conn.execute(
                "SELECT p.date AS date, COALESCE(a.close_adj, p.close) AS close "
                "FROM daily_price p "
                "LEFT JOIN daily_price_adj a ON a.stock_id=p.stock_id AND a.date=p.date "
                "WHERE p.stock_id=? AND p.date BETWEEN ? AND ? "
                "ORDER BY p.date",
                (source, start_date, end_date),
            ).fetchall()
    if len(row) < 2:
        return None
    first = float(row[0]["close"])
    last = float(row[-1]["close"])
    if first <= 0:
        return None
    return (last - first) / first


def _backtest_on_slice(
    db: Database,
    stock_ids: list[str],
    cfg: StrategyConfig,
    start_date: str,
    end_date: str,
    use_adj: bool = True,
) -> dict:
    """對指定日期區間跑一次投組回測，回傳彙總指標。

    **Flat-reset**：每個 slice 用獨立的 `_run_strategy_loop` 從 in_position=False 起跑，
    確保 train 區末尾持倉不會「拖」進 test 區，避免 walk-forward 邊界污染。
    """
    rows = []
    per_trade_returns: list[float] = []
    for sid in stock_ids:
        price, inst, margin = _load_stock_data(db, sid, use_adj=use_adj)
        if price.empty:
            continue
        # 注意：_vectorized_short_scores 用全段歷史算技術指標（MA60、RSI 等），
        # 這是技術指標的暖機需求，不是 look-ahead；切片只切 backtest 跑的範圍。
        series = _vectorized_short_scores(price, inst, margin)
        series["date"] = pd.to_datetime(series["date"])
        sub = series[
            (series["date"] >= pd.Timestamp(start_date))
            & (series["date"] <= pd.Timestamp(end_date))
        ].reset_index(drop=True)
        if len(sub) < 60:
            continue
        # 對 sub 直接跑迴圈 → in_position 從 False 開始，slice 結束未平倉部位丟棄。
        # 依代號套對應稅率（共用 cfg 物件不能就地改，做 per-stock copy）。
        cfg_per = replace(cfg, tax_rate=tax_rate_for(sid))
        trades = _run_strategy_loop(sub, cfg_per)
        if not trades:
            continue
        win_rate = sum(1 for t in trades if t.net_return > 0) / len(trades)
        total_ret = 1.0
        for t in trades:
            total_ret *= (1 + t.net_return)
        total_ret -= 1
        per_trade_returns.extend(float(t.net_return) for t in trades)
        rows.append({
            "stock_id": sid,
            "n_trades": len(trades),
            "win_rate": win_rate,
            "total_return": total_ret,
        })
    if not rows:
        return {
            "n_trades": 0, "mean_return": 0.0, "win_rate": 0.0,
            "sharpe": None, "mean_alpha_vs_0050": None,
        }
    df = pd.DataFrame(rows)
    bm = benchmark_return(db, start_date, end_date, source="0050")
    mean_ret = float(df["total_return"].mean())
    # Per-trade Sharpe ratio（信息比率風）：mean / std of single-trade net returns。
    # 用於 walk-forward 選參數，避開「報酬高但波動更高」的純運氣組。
    sharpe: float | None = None
    if len(per_trade_returns) >= 2:
        arr = pd.Series(per_trade_returns, dtype=float)
        sd = float(arr.std(ddof=1))
        if sd > 1e-9:
            sharpe = round(float(arr.mean()) / sd, 4)
    return {
        "n_trades": int(df["n_trades"].sum()),
        "mean_return": round(mean_ret, 4),
        "win_rate": round(float(df["win_rate"].mean()), 4),
        "sharpe": sharpe,
        "bm_0050": round(bm, 4) if bm is not None else None,
        "mean_alpha_vs_0050": round(mean_ret - bm, 4) if bm is not None else None,
    }


def walk_forward(
    db: Database,
    stock_ids: list[str],
    param_grid: list[StrategyConfig],
    *,
    n_splits: int = 3,
    train_ratio: float = 0.7,
    use_adj: bool = True,
) -> pd.DataFrame:
    """簡化版 walk-forward：把時間切 N 段，每段 train_ratio 為 in-sample、其餘為 out-of-sample。

    流程：
    1. 取所有候選股票共同日期範圍。
    2. 切 N 等分（重疊式 sliding 做進階，這裡做非重疊簡化版）。
    3. 每段：在 train 區掃 param_grid，挑 mean_return 最高的；再把該組參數套到 test 區量報酬。
    4. 回傳 (split, train_period, best_entry, best_exit, train_ret, test_ret, test_alpha)。

    目的是揭露「in-sample 很漂亮但 out-of-sample 不行」的過擬合。
    """
    if not stock_ids or not param_grid:
        return pd.DataFrame()

    with db.connect() as conn:
        rng = conn.execute(
            "SELECT MIN(date) AS mn, MAX(date) AS mx FROM daily_price "
            f"WHERE stock_id IN ({','.join('?'*len(stock_ids))})",
            stock_ids,
        ).fetchone()
    if not rng or not rng["mn"]:
        return pd.DataFrame()

    full_start = pd.Timestamp(rng["mn"])
    full_end = pd.Timestamp(rng["mx"])
    total_days = (full_end - full_start).days
    if total_days < 90 * n_splits:
        return pd.DataFrame()

    slice_days = total_days // n_splits
    out_rows = []
    for i in range(n_splits):
        s_start = full_start + pd.Timedelta(days=i * slice_days)
        s_end = full_start + pd.Timedelta(days=(i + 1) * slice_days)
        # train 是前 train_ratio 段，test 是後 (1-train_ratio) 段
        split_point = s_start + pd.Timedelta(days=int(slice_days * train_ratio))

        train_start, train_end = s_start.strftime("%Y-%m-%d"), split_point.strftime("%Y-%m-%d")
        test_start, test_end = split_point.strftime("%Y-%m-%d"), s_end.strftime("%Y-%m-%d")

        # 在 train 區掃 grid。優先用 sharpe 排序（風險調整後最佳）；
        # 若無 sharpe 可比（單一 trade 或 std=0）則 fallback mean_return。
        best = None
        best_cfg = None
        for cfg in param_grid:
            r = _backtest_on_slice(db, stock_ids, cfg, train_start, train_end, use_adj)
            if best is None:
                best, best_cfg = r, cfg
                continue
            cur_key = (r["sharpe"] if r["sharpe"] is not None else -1e9, r["mean_return"])
            best_key = (best["sharpe"] if best["sharpe"] is not None else -1e9, best["mean_return"])
            if cur_key > best_key:
                best, best_cfg = r, cfg
        if best is None or best_cfg is None:
            continue

        # 把最佳 cfg 套到 test 區
        test_r = _backtest_on_slice(db, stock_ids, best_cfg, test_start, test_end, use_adj)

        out_rows.append({
            "split": i + 1,
            "train_period": f"{train_start} ~ {train_end}",
            "test_period": f"{test_start} ~ {test_end}",
            "best_entry": best_cfg.entry_threshold,
            "best_exit": best_cfg.exit_threshold,
            "train_return": best["mean_return"],
            "train_sharpe": best["sharpe"],
            "test_return": test_r["mean_return"],
            "test_sharpe": test_r["sharpe"],
            "test_alpha_0050": test_r.get("mean_alpha_vs_0050"),
            "test_n_trades": test_r["n_trades"],
        })
    return pd.DataFrame(out_rows)


def with_benchmarks(
    db: Database,
    summaries: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """幫 backtest_portfolio 的結果加上兩個基準欄位與對應 Alpha。"""
    if summaries.empty:
        return summaries
    bm_0050 = benchmark_return(db, start_date, end_date, source="0050")
    bm_taiex = benchmark_return(db, start_date, end_date, source="TAIEX")
    out = summaries.copy()
    if bm_0050 is not None:
        out["bm_0050"] = round(bm_0050, 4)
        out["alpha_vs_0050"] = out["total_return"] - bm_0050
    if bm_taiex is not None:
        out["bm_taiex"] = round(bm_taiex, 4)
        out["alpha_vs_taiex"] = out["total_return"] - bm_taiex
    return out


def portfolio_summary(summaries: pd.DataFrame) -> dict:
    """彙總多檔回測結果。"""
    if summaries.empty:
        return {}
    traded = summaries[summaries["n_trades"] > 0]
    n_stocks = len(summaries)
    n_with_trades = len(traded)
    avg_total = traded["total_return"].mean() if not traded.empty else 0
    avg_bh = summaries["buy_and_hold"].mean()
    avg_alpha = traded["alpha"].mean() if not traded.empty else 0
    overall_winrate = (traded["win_rate"] * traded["n_trades"]).sum() / traded["n_trades"].sum() if traded["n_trades"].sum() > 0 else 0
    return {
        "n_stocks": n_stocks,
        "n_with_trades": n_with_trades,
        "avg_strategy_return": avg_total,
        "avg_buy_and_hold": avg_bh,
        "avg_alpha": avg_alpha,
        "overall_winrate": overall_winrate,
    }
