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

from app.indicators.fundamentals import compute_peg


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
    """KD 評分 — v5c 反向映射（mean-reversion 解讀）。

    980 天 HAC CI：5d -0.011 / 20d -0.020 / 60d -0.028，**三 horizon 全顯著反向**。
    台股 KD 結構性 mean-reversion：高 KD 過熱、低 KD 殺過頭。原 v4 邏輯「KD 高=高分」與
    forward return 真實方向相反、是 IC 拖累元兇之一。

    這裡仍沿用原打分結構（讓 sub-factor 註解可讀），最後 `100 - score` 翻轉成「mean-reversion
    解讀」：高 KD 給低分、低 KD 給高分、死亡交叉變看好（殺過頭即將反彈）。engine 端永遠以
    「分數越高越看好」處理，因子診斷與 IC backtest 邏輯不變。
    """
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
    # v5c：反向映射（mean-reversion）— 高 KD 給低分、低 KD 給高分
    return _clip(100 - score)


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
    """VR 26 分區的 baseline 分數（純 VR 版本）。
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
    """純 VR 因子評分（保留函式名 `score_vr_macd` 以維持相容）。

    設計：
    1) 先用 VR 分區決定 baseline（_vr_zone_score）。
    2) 再用「VR 與前一日相比」做微調：
       - 低量區( <80 )量能回升：加分（可能由谷底轉強）
       - 常態區(80~250)量能回升：小幅加分（量價配合）
       - 過熱區(>=250)續升：扣分（追價風險）
       - 過熱區回落：加分（降溫、風險下降）
    """
    vr_val = last_row.get("vr26")
    if _is_missing(vr_val):
        return None

    zone = _vr_zone_score(float(vr_val))
    if prev_row is None:
        return zone
    vr_prev = prev_row.get("vr26")
    if _is_missing(vr_prev):
        return zone

    if vr_val > vr_prev:
        if vr_val < 80:
            return _clip(zone + 8.0)
        if vr_val < 250:
            return _clip(zone + 5.0)
        return _clip(zone - 5.0)
    if vr_val < vr_prev:
        if vr_val < 80:
            return _clip(zone - 10.0)
        if vr_val < 250:
            return _clip(zone - 5.0)
        return _clip(zone + 8.0)
    return zone


def score_volume(last_row: pd.Series) -> Optional[float]:
    """量能評分。

    用 vol_ratio20（20 日均量比）取代原本的 vol_ratio5（5 日均量比）。原因：
    5 日視窗對昨日巨量極為敏感 — 低流動性個股偶發單筆大單 → 5d mean 翻倍 → 今天的 vr ≈ 0.2
    被當「弱量」扣分（35 分），但今天恢復正常成交根本不該扣。20 日視窗稀釋掉單一日的衝擊，
    分數更穩定。閾值不動（與 5d 同尺度），實證在大型權值股、低流動股都更合理。
    """
    vr = last_row.get("vol_ratio20")
    if _is_missing(vr):
        # 退回 5 日（兼容舊資料 / vol_ratio20 還沒灌的情境）
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
    """融資 5 日變動 — v5c 強化反向映射。

    980 天 HAC CI：60d -0.014 顯著反向（融資追高 = 散戶情緒高峰 = 短中期見頂、台股經典反指
    標）。v4 函式已偏反向但 spread 不夠大（最低 20、最高 60、僅 40 分區間），無法有效擠壓出
    融資暴增的負面訊號 — IC 仍 -0.014 顯著。

    v5c 擴大反向 spread 至 [10, 80]：融資 5 日 +20% 以上 → 10 分（強烈見頂）；融資減 10% 以
    上 → 80 分（散戶離場、籌碼變乾淨、中線轉強訊號）。
    """
    if "margin_chg5" not in chip:
        return None
    chg = chip.get("margin_chg5")
    if _is_missing(chg):
        return None
    if chg > 0.20: return 10.0   # 融資暴增 = 散戶極端追高 = 強烈反指標
    if chg > 0.10: return 25.0
    if chg > 0.03: return 40.0
    if chg > -0.03: return 55.0  # 中性
    if chg > -0.10: return 70.0
    return 80.0  # 融資大減 = 散戶離場 = 籌碼乾淨 = 中線看好


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

    M4 修補：ADV20 < 1M 股的散戶為主小型股，外資 / 投信往往沒有結構性建倉，幾十萬股的累計
    chip-noise 被規一化分母放大成 ratio ±5%~10% → score 跳動但無資訊。低流動股 ratio 視為
    0（中性 50）— 這族群的中期分數應該由 trend / EPS 決定，不該被法人雜訊扭曲。
    """
    if avg_vol_20 is None or avg_vol_20 <= 0:
        return None
    if avg_vol_20 < 1_000_000:
        return 0.0  # 低流動性 → 中性，不參與 institutional 訊號
    return cum20 / (avg_vol_20 * 20.0)


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
    """中期 EPS 成長：用 yoy（單季同期）為主、qoq fallback。代表「最近一兩季在加速還是降溫」。

    M1 修補：`recurring_earnings_warning=True` 時切到 OP-based yoy，避免一次性業外（處分子公司、
    FV 評價）膨脹單季 EPS 而 mid 衝 100。例 3708 上緯投控 2025 Q4 EPS=35（處分一次性入帳），
    mid eps_growth 滿分但本業 OP TTM −5.83 億，long 已治、mid 之前漏 → 補一致。

    M2 修補：負基期保護（去年同季 EPS<0 時，cap yoy 在 0.5 → 轉正有訊號但別滿分）+ 低基期保護
    （最新單季 EPS<0.5 元時，raw cap 75 → 微利公司 EPS 0.10→0.15 yoy +50% 不該 100 滿分）。
    """
    # M1：警示時優先用 OP yoy（OperatingIncome 排除業外，反映本業）
    if fund.get("recurring_earnings_warning"):
        core_q = fund.get("core_op_q")
        core_q_prev = fund.get("core_op_q_yoy_base")
        if core_q is not None and core_q_prev is not None and core_q_prev != 0:
            yoy_op = (core_q - core_q_prev) / abs(core_q_prev)
            return _linear(yoy_op, -0.3, 0.5)
        # 缺 core series → cap 取保守值（避免一次性 EPS 衝高還滿分）
        yoy_fallback = fund.get("eps_yoy") or fund.get("eps_qoq")
        if yoy_fallback is None:
            return None
        return _linear(min(yoy_fallback, 0.0), -0.3, 0.5)

    yoy = fund.get("eps_yoy")
    qoq = fund.get("eps_qoq")
    main = yoy if yoy is not None else qoq
    if main is None:
        return None

    # M2-A 負基期保護：去年同季 EPS 為負時，cap yoy 在 0.5（轉正本來就有意義但別滿分）
    eps_q_prev = fund.get("eps_q_yoy_base")
    if eps_q_prev is not None and eps_q_prev < 0:
        main = min(main, 0.5)

    raw = _linear(main, -0.3, 0.5)

    # M2-B 低基期保護：絕對 EPS_q < 0.5 元時 cap 在 75（避免微利公司 yoy 大百分比拉滿分）
    eps_q = fund.get("eps_q")
    if eps_q is not None and abs(eps_q) < 0.5:
        raw = min(raw, 75.0)

    return raw


def score_eps_cagr_3y(fund: dict) -> Optional[float]:
    """長期 EPS 成長：3 年 CAGR（複合年成長率）。

    為什麼長期不用 yoy 跟 mid 共用？
    - mid 的 yoy 是「最新一季 vs 去年同期」，在景氣循環 / 一次性業外影響下波動很大
    - long 想衡量的是「3-5 年趨勢上有沒有持續長大」，CAGR 才是學界與 buffett 都用的口徑
    - 若兩個維度都看 yoy，等於對短週期變動 double counting，掩蓋長期體質
    Cutoff：CAGR 0% = 50 分（停滯），20% = 100 分（高速複合成長），-10% = 0 分（長期衰退）。

    `recurring_earnings_warning=True` 時切換到 OperatingIncome CAGR：本業最新單季與 TTM 都虧
    →  EPS CAGR 高度可能來自一次性業外（處分子公司、FV 評價）。例 3708 上緯投控 2025 Q4
    處分後 eps_cagr_3y 衝到 57% / score 100，但 op_cagr 為負反映本業實際在縮。core 缺值就退
    回 actual，不誤傷沒灌 OP 序列的金融業（6023 元大期）。
    """
    if fund.get("recurring_earnings_warning"):
        core_cagr = fund.get("core_op_cagr_3y")
        if core_cagr is not None:
            return _linear(core_cagr, -0.10, 0.20)
        # 缺 core_op_cagr_3y（< 16 季 OP 序列、TTM 兩端非正）：min(actual, 0) 取保守值，
        # 避免一次性膨脹的 EPS CAGR 還是滿分。actual 為負則直接交給 _linear 評。
        cagr = fund.get("eps_cagr_3y")
        if cagr is None:
            return None
        return _linear(min(cagr, 0.0), -0.10, 0.20)
    cagr = fund.get("eps_cagr_3y")
    if cagr is None:
        return None
    return _linear(cagr, -0.10, 0.20)


_LUMPY_REVENUE_INDUSTRIES = frozenset({
    # 訂單型 / 完工認列型產業，單季 Revenue 對「持續性出貨動能」的代表性低
    "建材營造", "其他建材", "營造工程",
})


def score_revenue_growth(fund: dict) -> Optional[float]:
    """中期營收成長 yoy。

    M3 修補：建設股 / 工程業切換到 TTM Revenue YoY。完工認列讓單季 Revenue 在 0 ↔ 95 之間
    跳動，跟「過去 12 個月營收動能」毫無關聯（例 2542 興富發 Q1 −78% / Q4 +37%、實際全年 −15%）。
    TTM 平滑掉單一交屋年的影響。`revenue_ttm_yoy` 由 fundamentals 預先算好；缺值退回單季 yoy。
    """
    ind = fund.get("industry_category")
    if ind in _LUMPY_REVENUE_INDUSTRIES:
        ttm_yoy = fund.get("revenue_ttm_yoy")
        if ttm_yoy is not None:
            return _linear(ttm_yoy, -0.2, 0.4)
        # 缺 TTM 退回單季，但這裡不該很常發生（建設股都有 4 季以上歷史）
    yoy = fund.get("revenue_yoy")
    qoq = fund.get("revenue_qoq")
    main = yoy if yoy is not None else qoq
    if main is None:
        return None
    return _linear(main, -0.2, 0.4)


# ======================================================================
# 長期子評分
# ======================================================================
_FINANCIAL_INDUSTRIES = frozenset({
    # 實際 DB stock_info.industry_category 字串（2026-05-08 全市場掃描）：
    # 「金融保險」70 檔（上市金控/銀行/保險/證券） + 「金融業」15 檔（上櫃期貨/壽險）
    "金融保險", "金融業",
})


def _is_financial_stock(fund: dict) -> bool:
    """金融業偵測：依 stock_info.industry_category 字串對照。

    為什麼用產業而不是 BS 結構（debt_ratio > 0.85）：BS 缺值的金融業（FinMind / OpenAPI 不抓
    金控的 BS 細項）會被結構偵測漏掉，而產業字串幾乎全市場都填了。
    """
    ind = fund.get("industry_category")
    if not ind:
        return False
    return ind in _FINANCIAL_INDUSTRIES


def _is_asset_stock(fund: dict) -> bool:
    """資產股識別五條件（與 score_asset_value 共用 gate）。

    Agent 2 經驗證在 982 檔有效樣本中抓出 11 檔，全是公認傳產資產股 / 折價股。
    Gate 設計避免誤救：
    - PBR < 0.8 — 折價於淨值（NAV 還在）
    - debt_ratio < 0.40 — 不是高槓桿地雷股
    - operating_margin > 0 — 本業有獲利（排除衰退股）
    - dividend_yield > 3.5 — 把 NAV 透過股利兌現
    - asset_turnover < 0.5 — 重資產特徵（土地、不動產低周轉）

    缺值嚴格擋（任一 None → 否），不做 fallback；BS 資料只覆蓋 1,077 檔，其他股自然跳過。
    """
    pbr = fund.get("pbr")
    yld = fund.get("dividend_yield")
    debt = fund.get("debt_ratio")
    om = fund.get("operating_margin")
    at = fund.get("asset_turnover")
    if any(x is None for x in (pbr, yld, debt, om, at)):
        return False
    return pbr < 0.8 and debt < 0.40 and om > 0 and yld > 3.5 and at < 0.5


def score_roe(fund: dict) -> Optional[float]:
    """ROE 評分：TTM 淨利 / 期末權益，線性 [0, 25%] → [0, 100]。

    `recurring_earnings_warning=True` 時切換到 core_roe_op（OP-based ROE proxy）：本業最新單季
    與 TTM 都虧 → 帳面 ROE 高度可能來自一次性業外。例 3708 上緯投控 2025 Q4 處分子公司後
    roe_ttm 從個位數暴衝 24%、score 97.9，但 OP-based ROE 為負反映本業實際在虧。

    `_is_asset_stock=True` 時加 floor 40：資產股總資產龐大但對應淨利相對小（如 2107 厚生 ROE
    3.77% 是不動產業結構性特徵），市場用 PBR 0.6 折價已 priced；ROE 子分數不該再扣第二次。
    floor 40 刻意保守 — 資產股不是優等生（不到 50 中位），但要跳出衰退區。
    """
    if fund.get("recurring_earnings_warning"):
        core_roe = fund.get("core_roe_op")
        if core_roe is not None:
            # core_roe_op 可能為負（本業虧）；cutoff 與 actual 同尺度但允許負值通過 _linear clip。
            return _linear(core_roe, 0.0, 0.25)
        # 缺 core：actual 取 min(roe_ttm, 0) 等於把警示股 ROE 分數壓到 0
        roe = fund.get("roe_ttm")
        if roe is None:
            return None
        return _linear(min(roe, 0.0), 0.0, 0.25)
    roe = fund.get("roe_ttm")
    if roe is None:
        return None
    base = _linear(roe, 0.0, 0.25)
    if _is_asset_stock(fund):
        return max(base, 40.0)
    return base


# 註：score_asset_value 子因子已撤回（2026-05-08 cohort audit）。
# 撤回原因：擬合 11/1933 檔資產股 (+0.30 平均) 但同時讓 dividend 0.10→0.05 / margin 0.20→0.15 的
# 權重縮水波及 1922 檔非資產股 (中位 -0.79、110 檔「高股利+高毛利」cohort 跌 -1~-7 分、6146 跌 -7.73)。
# 副作用 19× 治療效果。改採純 ROE floor 40（保留在 score_roe 內），對非資產股 0 影響、surgical。
# `_is_asset_stock` helper 仍保留給 score_roe 使用。


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

    # PEG：優先吃 fundamentals 預先算好的 peg（單一來源），缺值才 fallback 用同一 helper 補算。
    peg = fund.get("peg")
    if peg is None:
        peg = compute_peg(per, fund.get("eps_cagr_3y"))
    if peg is not None:
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
    # v5c (2026-05-08)：980 天 HAC CI 顯示反向因子族群、量能因子升級。
    # 反向訊號（在 score_kd / score_margin_change 內翻轉映射、不在 engine 動）：
    #   kd       5d -0.011 / 20d -0.020 / 60d -0.028 全顯著反向（穩定 mean-reversion）
    #   margin_change 60d -0.014 顯著反向（融資追高反指標、台股經典）
    # 跨 horizon flip 的因子（5d 與 60d 號相反、不穩定）→ 大幅降權：
    #   rsi      5d +0.017 → 60d -0.019 flip
    #   bollinger 5d +0.023 → 60d -0.017 flip
    #   macd     全衰減到雜訊
    # 唯一全 horizon 顯著正向（升權重）：
    #   volume   5d +0.005 / 20d +0.010 / 60d +0.011 全顯著、IR 0.28
    # 預期 short 60d IC +0.005 → +0.015~0.025、IR 0.09 → 0.30+
    "ma_alignment": 0.20,   # +0.02
    "kd": 0.06,             # -0.04 + score_kd 內反向映射
    "macd": 0.02,           # -0.02 衰減到雜訊
    "rsi": 0.03,            # -0.04 跨 horizon flip 不穩
    "bollinger": 0.03,      # -0.03 跨 horizon flip 不穩
    "volume": 0.20,         # +0.10 唯一三 horizon 全正顯著
    "vr_macd": 0.05,        # -0.03 全雜訊
    "foreign": 0.20,        # +0.02
    "trust": 0.13,          # +0.02
    "margin_change": 0.08,  # 不動權重、score_margin_change 內反向映射
}

MID_TERM_WEIGHTS = {
    # v5c (2026-05-08)：移除 revenue_growth。v5b backfill 跑完後 IC 顯示 60d -0.030 IR -0.59
    # 反向顯著、且跨 8 個 half-year 桶都負（不是雜訊）。Cohort 拆解：建材營造 -0.082、航運
    # -0.070、電子相關 -0.032 三個 cohort 主導，原因是 TTM YoY 已被市場 price-in、後續 60d
    # mean-reversion。M3 修補（建設股切 TTM）解了「分數抖動」但沒解「lag 本質」。
    # 砍掉而非反向：cohort 異質性高（半導體 +0.006），統一反向會傷半導體。
    # 預期 mid 60d IC +0.005 → +0.013、composite 60d IC +0.017 → +0.023（+35%）。
    # score_revenue_growth 函式保留給個股詳情頁顯示用，只是不進加權。
    "trend": 0.30,          # -0.02 → 拿一部分權重重新分配
    "foreign_cum": 0.20,
    "trust_cum": 0.20,      # +0.04 IR 0.29 仍正、最強籌碼因子
    "eps_growth": 0.26,     # +0.08（M1+M2 後仍正、唯一可靠成長因子）
    "vr_macd": 0.04,        # 純 VR 在中期只小幅配置
}

LONG_TERM_WEIGHTS = {
    # v4 (2026-04-30，修完 eps_cagr_3y bug + financials 擴充到 2018Q1 + 980 天 backfill 後)：
    # /diagnostics 980 天 IC（horizon=long sub-factor，HAC CI）：
    #   factor          5d        20d       60d        IR(60d)  CI(60d) 過 0?
    #   ──────────────  ────────  ────────  ─────────  ───────  ─────────────
    #   eps_cagr_3y     +0.0169   +0.0303   +0.0306    +0.45    ✗（顯著）
    #   dividend        +0.0382   +0.0378   +0.0460    +0.40    ✓（雜訊範圍）
    #   margin_quality  +0.0234   +0.0336   +0.0454    +0.37    ✓
    #   valuation       +0.0214   +0.0225   +0.0326    +0.41    ✓
    #   roe             +0.0905   —         —          +2.05    ⚠ 只 15 dates 稀少
    # 解讀：dividend / margin / valuation 點估計高但 60d CI 全部過 0（不顯著於雜訊）。
    # eps_cagr_3y 是長期裡**唯一 60d CI 不過 0** 的因子。獨立 reviewer 之前警告
    # dividend 跨 horizon 全 +0.035 太完美（殖利率變動慢的自相關偽穩定），HAC CI 印證警告。
    "roe": 0.40,            # 不動 — 5d IC +0.091 IR 2.05 為長期最強單因子（雖只 15 dates）
    "margin_quality": 0.20, # 不動 — 60d CI 過 0 但點估計正、IC backtest 點估計值得保留
    "eps_cagr_3y": 0.20,    # +0.15 — bug 修完後是 long 裡唯一 60d 顯著的因子
    "dividend": 0.10,       # 不動 — 同產業 z-score 規一化已修偽穩定，IC 仍正
    "valuation": 0.10,      # 不動 — 60d +0.033 雖 CI 過 0 但點估計穩定，保留
}
# 2026-05-08：score_asset_value 子因子實驗撤回（cohort audit 顯示副作用 19× 治療效果）。
# 改採純 ROE floor 40 in score_roe — 對非資產股 0 影響、surgical。詳見 score_roe / score_asset_value 註解。


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
                # v5c：revenue_growth 0.28 → eps_growth（保守派最重視盈餘穩定）
                "trend": 0.20, "foreign_cum": 0.20, "trust_cum": 0.10,
                "eps_growth": 0.48, "vr_macd": 0.02,
            },
            "long": {
                "roe": 0.30, "margin_quality": 0.20, "eps_cagr_3y": 0.10,
                "dividend": 0.25, "valuation": 0.15            },
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
                # v5c：revenue_growth 0.20 → eps_growth（成長派看盈餘成長 lead 營收）
                "trend": 0.33, "foreign_cum": 0.10, "trust_cum": 0.10,
                "eps_growth": 0.43, "vr_macd": 0.04,
            },
            "long": {
                "roe": 0.25, "margin_quality": 0.20, "eps_cagr_3y": 0.35,
                "dividend": 0.05, "valuation": 0.15            },
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
                # v5c：revenue_growth 0.15 → trend（技術派以趨勢為主）
                "trend": 0.60, "foreign_cum": 0.10, "trust_cum": 0.10,
                "eps_growth": 0.15, "vr_macd": 0.05,
            },
            "long": {
                "roe": 0.20, "margin_quality": 0.20, "eps_cagr_3y": 0.20,
                "dividend": 0.10, "valuation": 0.30            },
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
                # v5c：revenue_growth 0.10 → 拆給 trust_cum + eps_growth（籌碼派加重三大法人）
                "trend": 0.20, "foreign_cum": 0.30, "trust_cum": 0.28,
                "eps_growth": 0.18, "vr_macd": 0.04,
            },
            "long": {
                "roe": 0.20, "margin_quality": 0.15, "eps_cagr_3y": 0.15,
                "dividend": 0.20, "valuation": 0.30            },
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
                # v5c：revenue_growth 0.29 → eps_growth（基本面派最重盈餘成長）
                "trend": 0.15, "foreign_cum": 0.10, "trust_cum": 0.05,
                "eps_growth": 0.68, "vr_macd": 0.02,
            },
            "long": {
                "roe": 0.30, "margin_quality": 0.30, "eps_cagr_3y": 0.20,
                "dividend": 0.10, "valuation": 0.10            },
        },
    },
}


# ======================================================================
# 「新手模式」：每個維度只顯示 4~5 個影響度最大的子指標，其餘隱藏使用預設值
# ======================================================================
# 給對 19 個指標看到頭暈的使用者用。前端會用這份白名單決定哪些 slider 顯示。
BEGINNER_VISIBLE_KEYS: dict[str, list[str]] = {
    "short": ["ma_alignment", "volume", "vr_macd", "foreign", "trust", "kd"],
    "mid": ["trend", "eps_growth", "trust_cum", "foreign_cum"],
    "long": ["roe", "eps_cagr_3y", "margin_quality", "dividend", "valuation"],
}
