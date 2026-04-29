"""評分規則。

每個 sub-score 回傳 `float | None`：
- `float` ∈ [0, 100]：資料齊全算出的分數
- `None`：該指標所需資料**真的缺失**（而不是恰好中性）

上層 (engine / radar) 會用 None-aware 的加權把缺失維度跳過並重新歸一化權重，
避免「資料缺失」被偽裝成中性 50 分、拉低或拉高真實分數。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def _is_missing(x) -> bool:
    """統一的缺失值判斷：None / NaN / Inf / pd.NA 都算缺失。"""
    if x is None:
        return True
    try:
        if isinstance(x, float) and (np.isnan(x) or np.isinf(x)):
            return True
        if pd.isna(x):
            return True
    except (TypeError, ValueError):
        pass
    return False


def _clip(x: float, lo: float = 0, hi: float = 100) -> float:
    return float(max(lo, min(hi, x)))


def _linear(x: float, lo: float, hi: float, reverse: bool = False) -> float:
    """把 x 從 [lo, hi] 線性映射到 [0, 100]；reverse=True 則反向（越小越好）。
    呼叫前需確保 x 不是缺失值。"""
    if hi == lo:
        return 50.0
    pct = (x - lo) / (hi - lo)
    score = 100 * pct
    if reverse:
        score = 100 - score
    return _clip(score)


# ======================================================================
# 短期子評分（技術 + 籌碼 + 量能）
# ======================================================================
def score_ma_alignment_short(last_row: pd.Series) -> Optional[float]:
    """多頭排列（ma5>ma10>ma20>ma60）給高分，空頭排列給低分。
    需要 close 與 ma5/ma10/ma20/ma60 全部存在；任一缺失（通常為新股 < 60 日）回傳 None。"""
    c = last_row.get("close")
    ma5, ma10, ma20, ma60 = (last_row.get(k) for k in ("ma5", "ma10", "ma20", "ma60"))
    if any(_is_missing(v) for v in (c, ma5, ma10, ma20, ma60)):
        return None
    score = 20  # 底分
    if c > ma5: score += 15
    if c > ma10: score += 10
    if c > ma20: score += 10
    if c > ma60: score += 5
    if ma5 > ma10 > ma20 > ma60: score += 30
    elif ma5 > ma10 > ma20: score += 20
    elif ma5 > ma10: score += 10
    return _clip(score)


def score_kd(last_row: pd.Series, prev_row: pd.Series | None) -> Optional[float]:
    k, d = last_row.get("k9"), last_row.get("d9")
    if _is_missing(k) or _is_missing(d):
        return None
    score = 50.0
    if k < 20: score += 15
    elif k < 50: score += 5
    elif k > 80: score -= 20
    elif k > 70: score -= 5
    if prev_row is not None:
        pk, pd_ = prev_row.get("k9"), prev_row.get("d9")
        if not _is_missing(pk) and not _is_missing(pd_):
            if pk <= pd_ and k > d: score += 20
            elif pk >= pd_ and k < d: score -= 20
    if k > d: score += 5
    else: score -= 5
    return _clip(score)


def score_macd(last_row: pd.Series, prev_row: pd.Series | None) -> Optional[float]:
    hist = last_row.get("macd_hist")
    if _is_missing(hist):
        return None
    score = 50.0
    if hist > 0: score += 15
    else: score -= 15
    if prev_row is not None:
        ph = prev_row.get("macd_hist")
        if not _is_missing(ph):
            if hist > ph: score += 15
            else: score -= 15
    return _clip(score)


def score_rsi(last_row: pd.Series) -> Optional[float]:
    """RSI 評分 + 強勢股 relief。

    一般情境下 RSI > 70 視為超買、> 80 為極度超買，扣分。
    但當均線完全多頭排列（ma5>ma10>ma20>ma60 且 close>ma5）時，
    高 RSI 多半是趨勢延續，不是反轉訊號 → 70-80 改維持中性、>80 由 15 鬆綁到 35。
    避免飆股因為 RSI 過高被系統誤判為高風險而踢出名單。
    """
    r = last_row.get("rsi14")
    if _is_missing(r):
        return None
    c = last_row.get("close")
    ma5 = last_row.get("ma5")
    ma10 = last_row.get("ma10")
    ma20 = last_row.get("ma20")
    ma60 = last_row.get("ma60")
    is_strong = (
        not any(_is_missing(v) for v in (c, ma5, ma10, ma20, ma60))
        and ma5 > ma10 > ma20 > ma60
        and c > ma5
    )
    if r < 30: return 75.0
    if r < 50: return 60.0
    if r < 60: return 55.0
    if r < 70: return 50.0
    if r < 80: return 50.0 if is_strong else 35.0
    return 35.0 if is_strong else 15.0


def score_bollinger(last_row: pd.Series) -> Optional[float]:
    pos = last_row.get("bb_pos")
    if _is_missing(pos):
        return None
    if pos < 0.2: return 70.0
    if pos < 0.4: return 60.0
    if pos < 0.6: return 55.0
    if pos < 0.8: return 45.0
    return 25.0


def _vr_zone_score(vr_val: float) -> float:
    """VR 26 分區的 baseline 分數（不看 MACD 時的中性參考）。
    台股慣例：< 40 為超低量谷底（反彈機會）、40-80 為偏低（健康整理）、
    80-150 為均衡（多空拉鋸）、150-250 為熱絡、250-450 為過熱、>= 450 為極端噴量。
    """
    if vr_val < 40: return 65.0
    if vr_val < 80: return 80.0
    if vr_val < 150: return 60.0
    if vr_val < 250: return 70.0
    if vr_val < 450: return 35.0
    return 15.0


def score_vr_macd(last_row: pd.Series, prev_row: pd.Series | None = None) -> Optional[float]:
    """成交量比率 (VR26) ✕ MACD 柱複合評分。

    透過 VR 分區判斷量能位置（低量谷底 / 健康量 / 過熱），再用 MACD 柱方向確認動能。
    rule A 最嚴格（VR 低量+rising+MACD turning up）→ 88 分（黃金底訊號）；
    rule G 最危險（VR 噴量+MACD turning down）→ 10 分（高檔反轉警示）。

    缺 prev_row 時走 zone-only fallback (rule K)：用 VR 分區基礎分 ±5（看 MACD 是否為正）。
    """
    vr_val = last_row.get("vr26")
    hist = last_row.get("macd_hist")
    if _is_missing(vr_val) or _is_missing(hist):
        return None

    # prev_row 缺失（DataFrame 第 1 筆）→ zone-only fallback ±5
    if prev_row is None:
        zone = _vr_zone_score(vr_val)
        return _clip(zone + (5 if hist > 0 else -5))

    vr_prev = prev_row.get("vr26")
    hist_prev = prev_row.get("macd_hist")
    if _is_missing(vr_prev) or _is_missing(hist_prev):
        # prev row 有但欄位缺 → 同樣走 fallback
        zone = _vr_zone_score(vr_val)
        return _clip(zone + (5 if hist > 0 else -5))

    vr_rising = vr_val > vr_prev
    hist_pos = hist > 0
    hist_growing = hist > hist_prev
    hist_turning_up = (hist > hist_prev) and (hist_prev <= 0)
    hist_turning_down = (hist < hist_prev) and (hist_prev >= 0)

    # Decision matrix — first match wins
    if vr_val < 80 and vr_rising and hist_turning_up:           # A
        return 88.0
    if vr_val < 40 and hist_turning_up:                          # I
        return 75.0
    if vr_val < 80 and vr_rising and hist_pos:                   # B
        return 78.0
    if vr_val < 40 and not hist_pos:                             # J
        return 35.0
    if 80 <= vr_val < 150 and hist_pos and hist_growing:         # C
        return 72.0
    if 150 <= vr_val < 250 and hist_pos:                         # D
        return 62.0
    if 250 <= vr_val < 450 and hist_growing:                     # E
        return 40.0
    if 250 <= vr_val < 450 and hist_turning_down:                # F
        return 20.0
    if vr_val >= 450 and hist_turning_down:                      # G
        return 10.0
    if vr_val >= 450 and hist_pos:                               # H
        return 25.0
    # K fallback：zone-only ±5
    zone = _vr_zone_score(vr_val)
    return _clip(zone + (5 if hist_pos else -5))


def score_volume(last_row: pd.Series) -> Optional[float]:
    vr = last_row.get("vol_ratio5")
    if _is_missing(vr):
        return None
    if vr < 0.5: return 35.0
    if vr < 0.8: return 45.0
    if vr < 1.2: return 55.0
    if vr < 2.0: return 70.0
    if vr < 3.0: return 60.0
    return 40.0


def score_foreign_short(chip: dict) -> Optional[float]:
    """連買/連賣次數。若 chip dict 完全沒有 foreign 資料 → None（代表 institutional 表缺資料）。"""
    if "foreign_streak_buy" not in chip and "foreign_streak_sell" not in chip:
        return None
    streak_buy = chip.get("foreign_streak_buy", 0) or 0
    streak_sell = chip.get("foreign_streak_sell", 0) or 0
    score = 50.0 + min(streak_buy, 5) * 6 - min(streak_sell, 5) * 6
    return _clip(score)


def score_trust_short(chip: dict) -> Optional[float]:
    if "trust_streak_buy" not in chip and "trust_streak_sell" not in chip:
        return None
    streak_buy = chip.get("trust_streak_buy", 0) or 0
    streak_sell = chip.get("trust_streak_sell", 0) or 0
    score = 50.0 + min(streak_buy, 5) * 5 - min(streak_sell, 5) * 5
    return _clip(score)


def score_margin_change(chip: dict) -> Optional[float]:
    if "margin_chg5" not in chip:
        return None
    chg = chip.get("margin_chg5")
    if _is_missing(chg):
        return None
    if chg > 0.20: return 20.0
    if chg > 0.10: return 35.0
    if chg > 0.03: return 45.0
    if chg > -0.03: return 55.0
    if chg > -0.10: return 60.0
    return 55.0


# ======================================================================
# 中期子評分
# ======================================================================
def score_trend_mid(last_row: pd.Series) -> Optional[float]:
    c = last_row.get("close")
    ma20, ma60 = last_row.get("ma20"), last_row.get("ma60")
    slope60 = last_row.get("ma60_slope")
    if _is_missing(c) or _is_missing(ma60):
        return None
    score = 30.0
    if c > ma60: score += 25
    if not _is_missing(ma20) and ma20 > ma60: score += 15
    if not _is_missing(slope60):
        if slope60 > 0.03: score += 20
        elif slope60 > 0: score += 10
        elif slope60 < -0.03: score -= 20
        else: score -= 5
    return _clip(score)


def _scale_by_adv(cum20: float, avg_vol_20: Optional[float]) -> Optional[float]:
    """把 20 日累計法人買超換成「占同期總成交量比例」。

    avg_vol_20 = 20 日平均日成交量（股）；20 日總成交量 = avg_vol_20 * 20。
    回傳 ratio = cum20 / (avg_vol_20 * 20)。avg_vol_20 缺值或 0 → None（讓上層走絕對 fallback）。
    """
    if avg_vol_20 is None or avg_vol_20 <= 0:
        return None
    total = avg_vol_20 * 20.0
    if total <= 0:
        return None
    return cum20 / total


def score_foreign_mid(chip: dict) -> Optional[float]:
    """外資 20 日累計買超。

    優先用「占同期總成交量比例」評分，避免大型權值股（2330 億級流通量）和小型股
    （百萬流通量）共用絕對閾值（10M 張）造成評分不公（金融分析師審查 #6）。
    chip 需含 `avg_volume_20`（由 radar 注入）；缺值時 fallback 舊版絕對閾值。
    沒有 institutional 資料 → None。
    """
    if "foreign_cum20" not in chip:
        return None
    cum20 = chip.get("foreign_cum20") or 0
    ratio = _scale_by_adv(cum20, chip.get("avg_volume_20"))
    if ratio is not None:
        if ratio > 0.50: return 85.0
        if ratio > 0.20: return 75.0
        if ratio > 0.05: return 65.0
        if ratio > 0:    return 55.0
        if ratio > -0.05: return 45.0
        if ratio > -0.20: return 35.0
        if ratio > -0.50: return 25.0
        return 15.0
    # fallback：避免無成交量資料時 score 永遠 None
    if cum20 > 10_000_000: return 85.0
    if cum20 > 5_000_000: return 75.0
    if cum20 > 1_000_000: return 65.0
    if cum20 > 0: return 55.0
    if cum20 > -1_000_000: return 45.0
    if cum20 > -5_000_000: return 35.0
    if cum20 > -10_000_000: return 25.0
    return 15.0


def score_trust_mid(chip: dict) -> Optional[float]:
    """投信 20 日累計買超；同 score_foreign_mid 採 % of ADV 規模化。"""
    if "trust_cum20" not in chip:
        return None
    cum20 = chip.get("trust_cum20") or 0
    ratio = _scale_by_adv(cum20, chip.get("avg_volume_20"))
    if ratio is not None:
        # 投信規模較小，閾值取一半
        if ratio > 0.25: return 80.0
        if ratio > 0.10: return 70.0
        if ratio > 0:    return 55.0
        if ratio > -0.10: return 45.0
        if ratio > -0.25: return 30.0
        return 20.0
    if cum20 > 2_000_000: return 80.0
    if cum20 > 500_000: return 70.0
    if cum20 > 0: return 55.0
    if cum20 > -500_000: return 45.0
    if cum20 > -2_000_000: return 30.0
    return 20.0


def score_eps_growth(fund: dict) -> Optional[float]:
    """中期 EPS 成長：用 yoy（單季同期）為主、qoq fallback。代表「最近一兩季在加速還是降溫」。"""
    yoy = fund.get("eps_yoy")
    qoq = fund.get("eps_qoq")
    main = yoy if yoy is not None else qoq
    if main is None:
        return None
    return _linear(main, -0.3, 0.5)


def score_eps_cagr_3y(fund: dict) -> Optional[float]:
    """長期 EPS 成長：3 年 CAGR（複合年成長率）。

    為什麼長期不用 yoy 跟 mid 共用？
    - mid 的 yoy 是「最新一季 vs 去年同期」，在景氣循環 / 一次性業外影響下波動很大
    - long 想衡量的是「3-5 年趨勢上有沒有持續長大」，CAGR 才是學界與 buffett 都用的口徑
    - 若兩個維度都看 yoy，等於對短週期變動 double counting，掩蓋長期體質
    Cutoff：CAGR 0% = 50 分（停滯），20% = 100 分（高速複合成長），-10% = 0 分（長期衰退）。
    """
    cagr = fund.get("eps_cagr_3y")
    if cagr is None:
        return None
    return _linear(cagr, -0.10, 0.20)


def score_revenue_growth(fund: dict) -> Optional[float]:
    yoy = fund.get("revenue_yoy")
    qoq = fund.get("revenue_qoq")
    main = yoy if yoy is not None else qoq
    if main is None:
        return None
    return _linear(main, -0.2, 0.4)


# ======================================================================
# 長期子評分
# ======================================================================
def score_roe(fund: dict) -> Optional[float]:
    roe = fund.get("roe_ttm")
    if roe is None:
        return None
    return _linear(roe, 0.0, 0.25)


def score_margin_quality(fund: dict) -> Optional[float]:
    gm = fund.get("gross_margin")
    om = fund.get("operating_margin")
    scores = []
    if gm is not None: scores.append(_linear(gm, 0.1, 0.5))
    if om is not None: scores.append(_linear(om, 0.0, 0.3))
    if not scores:
        return None
    return float(np.mean(scores))


def score_dividend(fund: dict) -> Optional[float]:
    """殖利率評分。

    優先使用 industry 內的 z-score（若 caller 已預先計算並注入 `dividend_yield_z`），
    避免「公用事業殖利率天花板高、科技股天花板低」帶來的不公平比較。
    沒有 z 時退回原本的絕對閾值。
    """
    z = fund.get("dividend_yield_z")
    if z is not None:
        # z > +1.5 同產業前段班；z < -1 同產業墊底
        # 線性映射：z=-1 → 30, z=0 → 55, z=+1 → 75, z=+1.5 → 85, 上限 90
        if z >= 1.5: return 85.0
        if z >= 1.0: return 75.0
        if z >= 0.5: return 65.0
        if z >= 0.0: return 55.0
        if z >= -0.5: return 45.0
        if z >= -1.0: return 35.0
        return 30.0
    y = fund.get("dividend_yield")
    if y is None:
        return None
    if y > 10: return 50.0
    if y > 6: return 85.0
    if y > 4: return 75.0
    if y > 3: return 65.0
    if y > 2: return 55.0
    if y > 1: return 45.0
    return 35.0


def score_valuation(fund: dict) -> Optional[float]:
    """估值評分。複合 4 個子維度：
       1. PER 絕對水位（< 10 便宜、> 50 過貴）
       2. PER 歷史分位（與自己比，越低分位越便宜）
       3. PBR 絕對水位（< 1 便宜、> 5 過貴）
       4. PEG = PER / 3 年 EPS CAGR（< 1 成長合算、> 2 成長付太多）

    每個子維度獨立 None-safe（有資料才加入平均），整體完全沒資料才回 None。
    新增 PBR / PEG 是因為單純 PER 有兩個盲點：
      - 銀行/金融股 PBR < 1 是常態，PER 可能不準（業外多）
      - 高成長股 PER 高很正常，要對照成長率才公平 → PEG
    """
    scores: list[float] = []

    per = fund.get("per")
    if per is not None and per > 0:
        if per < 10: scores.append(85.0)
        elif per < 15: scores.append(75.0)
        elif per < 20: scores.append(60.0)
        elif per < 30: scores.append(45.0)
        elif per < 50: scores.append(30.0)
        else: scores.append(15.0)

    pct = fund.get("per_percentile")
    if pct is not None:
        scores.append(_clip(100 - pct * 100))

    pbr = fund.get("pbr")
    if pbr is not None and pbr > 0:
        if pbr < 1: scores.append(80.0)
        elif pbr < 2: scores.append(70.0)
        elif pbr < 3: scores.append(55.0)
        elif pbr < 5: scores.append(40.0)
        else: scores.append(20.0)

    # PEG：PER ÷ EPS 成長率（用 3 年 CAGR 較穩，避免單季噪音）
    # 成長率必須為正才有意義；負成長股別用 PEG 評估
    cagr = fund.get("eps_cagr_3y")
    if per is not None and per > 0 and cagr is not None and cagr > 0:
        peg = per / (cagr * 100)
        if peg < 0.5: scores.append(90.0)
        elif peg < 1.0: scores.append(75.0)
        elif peg < 1.5: scores.append(60.0)
        elif peg < 2.0: scores.append(40.0)
        else: scores.append(20.0)

    if not scores:
        return None
    return float(np.mean(scores))


# ======================================================================
# 權重設定
# ======================================================================
SHORT_TERM_WEIGHTS = {
    # v3：依 5d / 20d IC 共識做小幅再平衡（保守版，不依賴小樣本 60d 結論）
    "ma_alignment": 0.18,   # +0.03 — 60d IC +0.119 唯一強訊號，5d/20d 弱故只小幅上調
    "kd": 0.10,             # -0.02 — 5d/20d 一致弱負
    "macd": 0.04,
    "rsi": 0.07,            # -0.01 — 一致弱負，小砍
    "bollinger": 0.06,      # -0.01
    "volume": 0.08,
    "vr_macd": 0.06,        # -0.02 — 全 horizon 反向，但 60d 樣本太少，保留小權重以防 regime switch
    "foreign": 0.20,        # 結構性訊號保留
    "trust": 0.13,          # +0.03 — Q5-Q1 spread 60d +11.29% 雖然 IC 弱
    "margin_change": 0.08,
}

MID_TERM_WEIGHTS = {
    # v3：trend 仍是最強信號，trust_cum 升、eps_growth/revenue_growth 微降
    "trend": 0.32,          # +0.02 — 5d/20d/60d 全 horizon 一致正
    "foreign_cum": 0.20,
    "trust_cum": 0.17,      # +0.02 — 5d/20d 一致正且 spread 大
    "eps_growth": 0.18,     # -0.02 — 仍正但讓出給更強因子
    "revenue_growth": 0.10, # -0.01
    "vr_macd": 0.03,        # -0.01 — short/mid 同方向反向，小幅修剪
}

LONG_TERM_WEIGHTS = {
    # v3：把 eps_cagr_3y 大砍到 0.05（資料品質問題：需 16 季 EPS、全市場大量缺值 → 全 null）
    # 釋出 0.20 給 roe/margin_quality/dividend；valuation 保留待 regime 換證
    "roe": 0.40,            # +0.05 — 5d IC +0.091 IR 2.05 為長期最強單因子
    "margin_quality": 0.30, # +0.05
    "eps_cagr_3y": 0.05,    # -0.20 — data quality；資料修好（補 4 年財報）後可回升
    "dividend": 0.15,       # +0.10 — 全 horizon 都穩定 +0.03，IR 1.86 為長期最穩定因子
    "valuation": 0.10,      # 不動 — 60d -0.048 看似反向，但 reviewer 認為是 2026Q1 regime artifact
}


# ======================================================================
# 綜合分數層級的維度權重
# ======================================================================
COMPOSITE_WEIGHTS = {
    # 依近期 IC：中期訊號穩定領先，短期降權避免噪音主導綜合分
    "short": 0.20,
    "mid": 0.60,
    "long": 0.20,
}

# 若維度 completeness 低於此值（用到的子指標權重佔比 < 30%），則該維度整體視為不可信，回傳 None
MIN_DIM_COMPLETENESS = 0.30


# ======================================================================
# 內建主題式權重 preset
# ======================================================================
# 給「不知道權重該怎麼調」的使用者一鍵套用的常見策略風格。
# 結構與 user 自存的 preset 一致：{"short": {...}, "mid": {...}, "long": {...}}
# 鍵集合必須完全等於 SHORT/MID/LONG_TERM_WEIGHTS 的鍵；新增子指標時這裡也要同步補。
BUILTIN_WEIGHT_PRESETS: dict[str, dict] = {
    "default": {
        "label": "預設（平衡）",
        "description": "技術 + 籌碼 + 基本面三邊兼顧；新手或不知道從哪開始時的安全選擇。",
        "weights": {
            "short": dict(SHORT_TERM_WEIGHTS),
            "mid": dict(MID_TERM_WEIGHTS),
            "long": dict(LONG_TERM_WEIGHTS),
        },
    },
    "conservative": {
        "label": "保守存股型",
        "description": "重視 ROE / 股利 / 估值，偏向找體質好且不貴的長線標的；技術面權重壓低。",
        "weights": {
            "short": {
                "ma_alignment": 0.20, "kd": 0.05, "macd": 0.03, "rsi": 0.05,
                "bollinger": 0.05, "volume": 0.07, "vr_macd": 0.05,
                "foreign": 0.30, "trust": 0.15, "margin_change": 0.05,
            },
            "mid": {
                "trend": 0.20, "foreign_cum": 0.20, "trust_cum": 0.10,
                "eps_growth": 0.20, "revenue_growth": 0.28, "vr_macd": 0.02,
            },
            "long": {
                "roe": 0.30, "margin_quality": 0.20, "eps_cagr_3y": 0.10,
                "dividend": 0.25, "valuation": 0.15,
            },
        },
    },
    "growth": {
        "label": "積極成長型",
        "description": "重押 EPS / 營收成長 + 趨勢；找會大漲的飆股，估值與股利讓位。",
        "weights": {
            "short": {
                "ma_alignment": 0.20, "kd": 0.10, "macd": 0.11, "rsi": 0.10,
                "bollinger": 0.05, "volume": 0.11, "vr_macd": 0.08,
                "foreign": 0.15, "trust": 0.05, "margin_change": 0.05,
            },
            "mid": {
                "trend": 0.33, "foreign_cum": 0.10, "trust_cum": 0.10,
                "eps_growth": 0.23, "revenue_growth": 0.20, "vr_macd": 0.04,
            },
            "long": {
                "roe": 0.25, "margin_quality": 0.20, "eps_cagr_3y": 0.35,
                "dividend": 0.05, "valuation": 0.15,
            },
        },
    },
    "technical": {
        "label": "技術派",
        "description": "短期看技術線型 (MA/KD/MACD/BB) + 量能；中長期權重壓低，著重短打。",
        "weights": {
            "short": {
                "ma_alignment": 0.20, "kd": 0.15, "macd": 0.10, "rsi": 0.10,
                "bollinger": 0.08, "volume": 0.10, "vr_macd": 0.15,
                "foreign": 0.04, "trust": 0.04, "margin_change": 0.04,
            },
            "mid": {
                "trend": 0.45, "foreign_cum": 0.10, "trust_cum": 0.10,
                "eps_growth": 0.15, "revenue_growth": 0.15, "vr_macd": 0.05,
            },
            "long": {
                "roe": 0.20, "margin_quality": 0.20, "eps_cagr_3y": 0.20,
                "dividend": 0.10, "valuation": 0.30,
            },
        },
    },
    "chip": {
        "label": "籌碼派",
        "description": "聚焦三大法人 + 融資籌碼變動；技術只看趨勢，基本面當輔助。",
        "weights": {
            "short": {
                "ma_alignment": 0.10, "kd": 0.03, "macd": 0.02, "rsi": 0.05,
                "bollinger": 0.05, "volume": 0.05, "vr_macd": 0.10,
                "foreign": 0.30, "trust": 0.20, "margin_change": 0.10,
            },
            "mid": {
                "trend": 0.20, "foreign_cum": 0.30, "trust_cum": 0.23,
                "eps_growth": 0.13, "revenue_growth": 0.10, "vr_macd": 0.04,
            },
            "long": {
                "roe": 0.20, "margin_quality": 0.15, "eps_cagr_3y": 0.15,
                "dividend": 0.20, "valuation": 0.30,
            },
        },
    },
    "fundamental": {
        "label": "基本面派",
        "description": "EPS / 營收 / ROE / 毛利率 為主，偏巴菲特式選股；技術面只當進場時機參考。",
        "weights": {
            "short": {
                "ma_alignment": 0.20, "kd": 0.10, "macd": 0.09, "rsi": 0.10,
                "bollinger": 0.05, "volume": 0.08, "vr_macd": 0.03,
                "foreign": 0.20, "trust": 0.10, "margin_change": 0.05,
            },
            "mid": {
                "trend": 0.15, "foreign_cum": 0.10, "trust_cum": 0.05,
                "eps_growth": 0.39, "revenue_growth": 0.29, "vr_macd": 0.02,
            },
            "long": {
                "roe": 0.30, "margin_quality": 0.30, "eps_cagr_3y": 0.20,
                "dividend": 0.10, "valuation": 0.10,
            },
        },
    },
}


# ======================================================================
# 「新手模式」：每個維度只顯示 4~5 個影響度最大的子指標，其餘隱藏使用預設值
# ======================================================================
# 給對 19 個指標看到頭暈的使用者用。前端會用這份白名單決定哪些 slider 顯示。
BEGINNER_VISIBLE_KEYS: dict[str, list[str]] = {
    "short": ["ma_alignment", "volume", "vr_macd", "foreign", "trust", "kd"],
    "mid": ["trend", "eps_growth", "revenue_growth", "foreign_cum"],
    "long": ["roe", "eps_cagr_3y", "margin_quality", "dividend", "valuation"],
}
