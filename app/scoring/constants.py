"""評分引擎決策常數：建議標籤門檻、進出場訊號門檻、停損/停利百分比、警告門檻。

定位：
- `rubric.py` 放「子指標 → 0-100 分」的計分曲線（線性區間、權重）。
- `constants.py`（本檔）放「分數 → 動作建議」的決策閾值（例如 score≥75 才稱強力偏多、
  RSI≥75 才算超買）。

把散落在 engine.py / build_signals 裡的魔術數字集中是為了：
1. 一頁看完目前所有閾值，方便審查
2. 將來做策略 A/B、用戶風險偏好客製時，有單一切入點
3. 改一個門檻不必到 engine.py 的 70 行邏輯內 grep
"""
from __future__ import annotations

# ======================================================================
# 推薦標籤（recommendation_label）
# ======================================================================
# 綜合分數 → 中文標籤，門檻是「下限」(>=)，由高到低判斷；都不滿足 → DEFAULT。
# 改用 list-of-tuple 取代一連串 if，方便未來新增層級或本地化。
RECOMMENDATION_TIERS: list[tuple[float, str]] = [
    (75.0, "🟢 強力偏多"),
    (60.0, "🟢 偏多"),
    (45.0, "🟡 中性"),
    (30.0, "🔴 偏空"),
]
RECOMMENDATION_BEAR = "🔴 強力偏空"        # 分數 < 最低 tier 的 fallback
RECOMMENDATION_INSUFFICIENT = "⚪ 資料不足"  # composite 為 None 時


# ======================================================================
# 進場訊號（build_signals.entry）
# ======================================================================
ENTRY_SHORT_TOTAL = 65        # 短分達標才考慮 KD 進場提示
ENTRY_KD_PART = 65            # 短分子項 kd 達標
ENTRY_VOL_PART = 65           # 短分子項 volume 達標
ENTRY_MA_ALIGN_PART = 65      # 短分子項 ma_alignment 達標
ENTRY_NEAR_MA20_RATIO = 1.02  # close < ma20 × 1.02 視為「靠近月線」
ENTRY_MID_TOTAL_NEAR_MA20 = 60  # 靠近月線可佈局的中分門檻
ENTRY_MID_TOTAL = 70          # 中長期共振：中分門檻
ENTRY_LONG_TOTAL = 60         # 中長期共振：長分門檻


# ======================================================================
# 停損 / 停利 預設參考（build_signals.stop_loss / take_profit）
# ======================================================================
DEFAULT_STOP_LOSS_PCT = 0.08    # -8%
TAKE_PROFIT_TIER1_PCT = 0.15    # +15%
TAKE_PROFIT_TIER2_PCT = 0.25    # +25%
TAKE_PROFIT_RSI = 70.0          # RSI ≥ N 提示停利


# ======================================================================
# 風險警示（build_signals.warnings）
# ======================================================================
WARN_RSI_OVERBOUGHT = 75.0      # RSI ≥ N 警告超買
WARN_5D_RETURN_PCT = 0.15       # 近 5 日漲幅 > N 警告追高
WARN_MARGIN_5D_INC_PCT = 0.15   # 融資 5 日增 > N 警告散戶追高
WARN_FOREIGN_STREAK_SELL = 5    # 外資連賣 N 日警告
WARN_LOW_COMPLETENESS = 0.6     # 整體完整度 < N 警告可信度低


# ======================================================================
# 時間性判定（stale / pending）
# ======================================================================
STALE_THRESHOLD_DAYS = 3        # 最新資料距今 > N 天視為過期
MARKET_SETTLED_HOUR_TPE = 14    # 台北 14 點前 as_of=今日 → 視為盤中 pending
