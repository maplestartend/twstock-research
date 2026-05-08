"""投資風格分數（Style Score） — v5c Wave 2 (2026-05-08)。

動機：long score 高 ≠ 對所有用戶都是好標的。例 6165 浪凡：v5b composite 68.4
（long 77.5 高分），但實際是「直播平台 + 高殖利率」價值股；用戶要做「賺價差」（動能）
→ 引擎與用戶風格不匹配。

設計：4 個並列的 Style Score（Value / Growth / Momentum / Income），用既有 sub-factor
線性加權平均，不新增 sub-factor、不動 LONG/MID/SHORT_TERM_WEIGHTS。對既有 long/mid/short/
composite 0 影響、純粹是「重新加權的 view」。

各風格定義（cohort IC 量測過、見 docs/architecture.md）：
- Value：低 PER/PBR/PEG（valuation 主軸）+ 高殖利率 + ROE 排除便宜爛公司 + margin 排除惡化
- Growth：EPS 短中長三段成長 + ROE 排除無利潤成長 + trend 確認股價有跟上（解 6165 bug）
- Momentum：mid.trend（含 ma60_slope）+ 短期 ma_alignment + 量能/法人/VR 確認
- Income：殖利率 + margin（穩定配發能力）+ ROE 排除掏空配息 + valuation

每個風格 cutoff：> 70 強符合、50-70 中性、< 50 不符。**Style 之間不要互相比較**（語意不同）。
"""
from __future__ import annotations

from typing import Optional


# 風格 → {sub_factor_name: weight}（橫跨 short/mid/long parts）
# weights 加總須為 1.0（None-aware 歸一化會自動處理缺值）
_STYLE_WEIGHTS: dict[str, dict[str, float]] = {
    "value": {
        # valuation 內含 PER/PBR/PEG/per_pct，已是價值核心
        "long.valuation":      0.40,
        "long.dividend":       0.25,
        "long.roe":            0.20,  # 排除「便宜爛公司」
        "long.margin_quality": 0.15,
    },
    "growth": {
        # 短中長三段成長 + ROE 排除無利潤成長
        "mid.eps_growth":      0.30,
        "long.eps_cagr_3y":    0.25,
        "long.roe":            0.15,
        "mid.trend":           0.20,  # 提權重至 0.20（v5c 補丁：解 6165 Growth=85.5 異常）
        # mid.revenue_growth 已從 MID_TERM_WEIGHTS 砍除，這裡也不用
        "mid.foreign_cum":     0.10,
    },
    "momentum": {
        # mid.trend (ma60_slope) 動能引擎 + ma_alignment 多頭排列確認
        "mid.trend":           0.40,
        "short.ma_alignment":  0.25,
        "short.volume":        0.15,
        "short.foreign":       0.10,
        "short.vr_macd":       0.10,
    },
    "income": {
        # 高股利 + 穩定毛利（穩定配發能力）+ ROE（不靠掏空配息）
        "long.dividend":       0.50,
        "long.margin_quality": 0.25,
        "long.roe":            0.15,
        "long.valuation":      0.10,
    },
}


# Growth 風格 trend gate 閾值（v5c 補丁）：
# 6165 浪凡 case 暴露的 bug — recurring_earnings_warning 觸發 OP-based fallback 後仍可能讓
# eps_growth 衝高、配上既有的 high eps_cagr_3y → Growth 分數異常。要求「成長要伴隨股價反映」
# (mid.trend ≥ 60) 才能拿超過 70 分。trend < 60 時 Growth cap 在 70。
_GROWTH_TREND_GATE = 60.0
_GROWTH_TREND_FAILED_CAP = 70.0


def _flatten_parts(
    short_parts: dict, mid_parts: dict, long_parts: dict,
) -> dict[str, Optional[float]]:
    """合併三個 horizon 的 parts → flat dict with `horizon.factor` keys。"""
    flat: dict[str, Optional[float]] = {}
    for prefix, parts in (("short", short_parts), ("mid", mid_parts), ("long", long_parts)):
        for k, v in (parts or {}).items():
            flat[f"{prefix}.{k}"] = v
    return flat


def _weighted_mean(
    weights: dict[str, float], values: dict[str, Optional[float]],
) -> Optional[float]:
    """None-aware 加權平均：跳過 None values、剩下權重 re-normalize。

    回 None 若所有 sub-factor 都 None（樣本太稀）。
    """
    used_w = 0.0
    acc = 0.0
    for key, w in weights.items():
        v = values.get(key)
        if v is None:
            continue
        used_w += w
        acc += v * w
    if used_w <= 0:
        return None
    return round(acc / used_w, 1)


def compute_style_scores(
    short_parts: dict, mid_parts: dict, long_parts: dict,
) -> dict[str, Optional[float]]:
    """從 short/mid/long parts dict 算 4 個 Style Score。

    回傳 {"value": float|None, "growth": ..., "momentum": ..., "income": ...}
    每個風格分數 0-100（None-aware 歸一化後）；某風格所需 sub-factor 全 None → 該風格 None。

    Growth 加 trend gate：mid.trend < 60 時 cap 在 70（解 6165 浪凡型「帳面成長但股價不漲」誤判）。
    """
    flat = _flatten_parts(short_parts, mid_parts, long_parts)
    out: dict[str, Optional[float]] = {}
    for style, weights in _STYLE_WEIGHTS.items():
        score = _weighted_mean(weights, flat)
        if style == "growth" and score is not None:
            trend = flat.get("mid.trend")
            if trend is not None and trend < _GROWTH_TREND_GATE:
                score = min(score, _GROWTH_TREND_FAILED_CAP)
        out[style] = score
    return out


def style_label(score: Optional[float]) -> str:
    """風格分數 → 標籤（顯示用）。"""
    if score is None:
        return "—"
    if score >= 70:
        return "🟢 強符合"
    if score >= 50:
        return "🟡 中性"
    return "🔴 不符"


__all__ = ["compute_style_scores", "style_label"]
