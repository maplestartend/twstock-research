"""風險管理：動態停損、部位配置、集中度警告。

設計原則：
- 純函式為主，不碰 UI；DataFrame 進、dict 出。
- DB 互動只用 read-only 的既有欄位，不新建表。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.data.db import Database


# ======================================================================
# ATR 相關
# ======================================================================
def compute_atr(price_df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range。需要 high/low/close 欄位。"""
    if price_df.empty or len(price_df) < 2:
        return pd.Series(dtype=float)
    h, l, c = price_df["high"], price_df["low"], price_df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    # 用 Wilder's smoothing（RMA）與一般慣例一致
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def atr_stop_loss(
    price_df: pd.DataFrame,
    entry_price: float | None = None,
    multiplier: float = 2.0,
    period: int = 14,
) -> dict | None:
    """基於 ATR 的停損建議。

    - entry_price=None 時用最後收盤當參考
    - 回傳 None：資料不足

    回傳：{'stop_price', 'atr', 'distance_pct', 'entry_ref'}
    """
    if price_df is None or len(price_df) < period + 1:
        return None
    atr = compute_atr(price_df, period)
    last_atr = float(atr.iloc[-1]) if not atr.empty and not pd.isna(atr.iloc[-1]) else None
    if last_atr is None or last_atr <= 0:
        return None
    ref = float(entry_price if entry_price is not None else price_df["close"].iloc[-1])
    stop = ref - multiplier * last_atr
    return {
        "stop_price": round(stop, 2),
        "atr": round(last_atr, 3),
        "distance_pct": (ref - stop) / ref if ref > 0 else None,
        "entry_ref": ref,
    }


def trailing_atr_stop(
    price_df: pd.DataFrame,
    entry_date: str,
    multiplier: float = 2.0,
    period: int = 14,
) -> dict | None:
    """追蹤停損：自進場日以來最高收盤 - N×ATR。持續上抬。"""
    if price_df is None or len(price_df) < period + 1 or not entry_date:
        return None
    df = price_df.copy()
    df["date"] = pd.to_datetime(df["date"]) if "date" in df.columns else df.index
    entry_ts = pd.to_datetime(entry_date)
    # 防呆：若 entry_date 比資料最新日還新（資料源延遲、TPEX 沒更新等），
    # clamp 到資料最新日當 anchor，避免 since 為空、整個 trailing 區塊在 UI 消失。
    if "date" in df.columns:
        latest_date = df["date"].max()
        if pd.notna(latest_date) and entry_ts > latest_date:
            entry_ts = latest_date
    since = df[df["date"] >= entry_ts] if "date" in df.columns else df.loc[entry_ts:]
    if since.empty:
        return None
    atr = compute_atr(df, period)
    last_atr = float(atr.iloc[-1]) if not atr.empty and not pd.isna(atr.iloc[-1]) else None
    if last_atr is None or last_atr <= 0:
        return None
    peak = float(since["close"].max())
    stop = peak - multiplier * last_atr
    latest = float(df["close"].iloc[-1])
    return {
        "stop_price": round(stop, 2),
        "atr": round(last_atr, 3),
        "peak_since_entry": round(peak, 2),
        "latest_close": round(latest, 2),
        "below_stop": latest < stop,
    }


def trailing_atr_take_profit(
    price_df: pd.DataFrame,
    entry_date: str,
    entry_price: float,
    *,
    multiplier: float = 3.0,
    period: int = 14,
    arm_pnl: float = 0.08,
    arm_days: int = 5,
) -> dict | None:
    """Chandelier-style 動態停利：自進場日以來最高價 - K×ATR。

    與 `trailing_atr_stop` 不同處：
    - 用 `high` 而非 `close` 取 peak（LeBeau 原始 Chandelier Exit 用 highest-high；
      停利的本質是鎖住「曾經實現過的紙上獲利」，影線高點也算數）
    - 多了 armed gate：避免進場初期被隨機波動洗掉
        * 浮盈 ≥ arm_pnl（預設 8%）
        * 持有 ≥ arm_days（預設 5 日）
    - multiplier 預設 3.0（停損 2.0），給趨勢更多呼吸空間（LeBeau 1992 原始建議）

    Args:
        price_df: 含 date/high/close 欄位的日 K
        entry_date: 進場日 'YYYY-MM-DD'
        entry_price: 進場成本（armed gate 算浮盈用）
        multiplier: ATR 倍數，預設 3.0
        period: ATR 週期，預設 14
        arm_pnl: 啟動門檻浮盈，預設 0.08
        arm_days: 啟動門檻持有日，預設 5

    Returns:
        None: 資料不足 / entry_date 無對應價、entry_price <= 0
        dict: {
            'take_profit_price': peak_high - K×ATR,
            'atr', 'peak_since_entry', 'latest_close',
            'days_held', 'unrealized_pnl_pct',
            'armed': 是否已啟動 trailing,
            'triggered': armed AND latest_close <= take_profit_price,
            'multiplier', 'arm_pnl_threshold', 'arm_days_threshold',
        }
    """
    if price_df is None or len(price_df) < period + 1 or not entry_date or entry_price <= 0:
        return None
    df = price_df.copy()
    df["date"] = pd.to_datetime(df["date"]) if "date" in df.columns else df.index
    entry_ts = pd.to_datetime(entry_date)
    # 防呆：entry_date 比資料最新日還新（資料源延遲）→ clamp 到最新日，避免 since 為空。
    if "date" in df.columns:
        latest_date = df["date"].max()
        if pd.notna(latest_date) and entry_ts > latest_date:
            entry_ts = latest_date
    since = df[df["date"] >= entry_ts] if "date" in df.columns else df.loc[entry_ts:]
    if since.empty:
        return None
    atr = compute_atr(df, period)
    last_atr = float(atr.iloc[-1]) if not atr.empty and not pd.isna(atr.iloc[-1]) else None
    if last_atr is None or last_atr <= 0:
        return None
    # Chandelier 原始用 high；若資料只有 close 就退回 close（保守）
    peak_col = "high" if "high" in since.columns else "close"
    peak = float(since[peak_col].max())
    tp_price = peak - multiplier * last_atr
    latest = float(df["close"].iloc[-1])
    days_held = int(len(since))
    pnl_pct = (latest - entry_price) / entry_price
    armed = (pnl_pct >= arm_pnl) and (days_held >= arm_days)
    return {
        "take_profit_price": round(tp_price, 2),
        "atr": round(last_atr, 3),
        "peak_since_entry": round(peak, 2),
        "latest_close": round(latest, 2),
        "days_held": days_held,
        "unrealized_pnl_pct": round(pnl_pct, 4),
        "armed": bool(armed),
        "triggered": bool(armed and latest <= tp_price),
        "multiplier": multiplier,
        "arm_pnl_threshold": arm_pnl,
        "arm_days_threshold": arm_days,
    }


# ======================================================================
# 部位配置（Fixed Risk / Kelly-lite）
# ======================================================================
@dataclass
class PositionSuggestion:
    max_shares: int         # 建議張數×1000（千股）
    max_position_value: float
    risk_amount: float      # 最大可接受虧損金額
    risk_per_share: float   # 每股風險


def suggest_position_size(
    capital: float,
    entry_price: float,
    stop_price: float,
    risk_per_trade: float = 0.02,
    lot_size: int = 1000,
) -> PositionSuggestion | None:
    """固定比例風險法：單筆最多虧 capital × risk_per_trade。

    Args:
        capital: 帳戶本金
        entry_price: 計畫進場價
        stop_price: 計畫停損價（必須 < entry_price）
        risk_per_trade: 每筆最大風險比例（2% 為常見起點）
        lot_size: 一張股數（台股 1000）
    """
    if entry_price <= 0 or stop_price <= 0 or stop_price >= entry_price:
        return None
    risk_amount = capital * risk_per_trade
    risk_per_share = entry_price - stop_price
    max_shares_raw = risk_amount / risk_per_share
    # 向下取整到整張
    max_lots = int(max_shares_raw // lot_size)
    max_shares = max_lots * lot_size
    return PositionSuggestion(
        max_shares=max_shares,
        max_position_value=max_shares * entry_price,
        risk_amount=risk_amount,
        risk_per_share=risk_per_share,
    )


# ======================================================================
# 集中度警告
# ======================================================================
def concentration_warnings(
    db: Database,
    holdings_mv: dict[str, float],
    *,
    single_pct_warn: float = 0.25,
    industry_pct_warn: float = 0.40,
) -> list[str]:
    """依持股市值 dict 檢查集中度。

    Args:
        holdings_mv: {stock_id: market_value}
        single_pct_warn: 單檔占比警戒（預設 25%）
        industry_pct_warn: 單一產業占比警戒（預設 40%）
    """
    warnings: list[str] = []
    total = sum(holdings_mv.values())
    if total <= 0 or not holdings_mv:
        return warnings

    # 單檔集中度
    for sid, mv in sorted(holdings_mv.items(), key=lambda x: -x[1]):
        pct = mv / total
        if pct >= single_pct_warn:
            warnings.append(f"⚠️ 單檔集中：{sid} 占 {pct*100:.1f}%（警戒 {single_pct_warn*100:.0f}%）")

    # 產業集中度
    stock_ids = list(holdings_mv.keys())
    if stock_ids:
        placeholders = ",".join("?" * len(stock_ids))
        with db.connect() as conn:
            rows = conn.execute(
                f"SELECT stock_id, industry_category FROM stock_info WHERE stock_id IN ({placeholders})",
                stock_ids,
            ).fetchall()
        industry_map = {r["stock_id"]: r["industry_category"] or "未分類" for r in rows}
        industry_mv: dict[str, float] = {}
        for sid, mv in holdings_mv.items():
            ind = industry_map.get(sid, "未分類")
            industry_mv[ind] = industry_mv.get(ind, 0) + mv
        for ind, mv in sorted(industry_mv.items(), key=lambda x: -x[1]):
            pct = mv / total
            if pct >= industry_pct_warn:
                warnings.append(f"⚠️ 產業集中：{ind} 占 {pct*100:.1f}%（警戒 {industry_pct_warn*100:.0f}%）")

    return warnings


# ======================================================================
# 擴充版風險訊號（含 ATR 動態停損）
# ======================================================================
def enhanced_risk_signals(
    db: Database,
    stock_id: str,
    avg_cost: float,
    entry_date: str | None,
    latest_close: float,
    short_score: float | None,
    price_df: pd.DataFrame | None = None,
) -> list[str]:
    """結合原本的 pnl% / 分數邏輯，加上 ATR 動態停損。

    `price_df` 參數可由呼叫方傳入已載入的日 K 棒 DataFrame，避免重複 read_sql。
    沒帶就照舊自己撈（保留 backward compat）。
    """
    signals: list[str] = []

    # 1) 基礎 PnL % 規則：-5% / -8% 停損提示，+25% 停利提示
    pnl_pct = (latest_close - avg_cost) / avg_cost if avg_cost > 0 else 0
    if pnl_pct <= -0.08:
        signals.append(f"⚠️ 虧損已達 {pnl_pct*100:.1f}%，觸發停損參考線")
    elif pnl_pct <= -0.05:
        signals.append(f"💡 虧損 {pnl_pct*100:.1f}%，接近 8% 停損")
    if pnl_pct >= 0.25:
        signals.append(f"🎯 獲利 {pnl_pct*100:.1f}%，可考慮分批停利")

    # 2) 短期分數
    if short_score is not None:
        if short_score <= 35:
            signals.append(f"⚠️ 短期分數 {short_score:.0f}，持股轉弱")
        elif short_score <= 40:
            signals.append(f"💡 短期分數 {short_score:.0f}，留意轉折")

    # 3) ATR 動態停損（若有進場日，用 trailing；否則 static）
    if price_df is None:
        with db.connect() as conn:
            price_df = pd.read_sql_query(
                "SELECT date, open, high, low, close FROM daily_price WHERE stock_id=? ORDER BY date",
                conn, params=[stock_id],
            )
    if not price_df.empty and len(price_df) >= 15:
        if entry_date:
            trail = trailing_atr_stop(price_df, entry_date, multiplier=2.0)
            if trail and trail["below_stop"]:
                signals.append(
                    f"🚨 跌破 ATR 追蹤停損：現價 {trail['latest_close']:.2f} < 停損 {trail['stop_price']:.2f}"
                    f"（進場後高點 {trail['peak_since_entry']:.2f}）"
                )
            # ATR 動態停利（Chandelier）：armed 後若回落到 peak − 3×ATR 即觸發
            tp = trailing_atr_take_profit(price_df, entry_date, entry_price=avg_cost, multiplier=3.0)
            if tp and tp["triggered"]:
                signals.append(
                    f"🎯 觸發 ATR 動態停利：現價 {tp['latest_close']:.2f} ≤ 停利線 {tp['take_profit_price']:.2f}"
                    f"（進場後高點 {tp['peak_since_entry']:.2f}，浮盈 {tp['unrealized_pnl_pct']*100:.1f}%）"
                )
        else:
            static = atr_stop_loss(price_df, entry_price=avg_cost, multiplier=2.0)
            if static and latest_close < static["stop_price"]:
                signals.append(
                    f"🚨 跌破 ATR 停損：現價 {latest_close:.2f} < 停損 {static['stop_price']:.2f}"
                )

    return signals
