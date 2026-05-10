"""評分引擎：把單一股票的技術/籌碼/基本面資料彙總成短/中/長三個時間框架分數與訊號。

資料不足處理：
- 子評分回傳 None 時，`_weighted` 會跳過該維度並把剩餘權重 re-normalize
- 若維度用到的有效權重比例低於 `MIN_DIM_COMPLETENESS`，整個維度分數判為 None
- 綜合分數同樣只針對「短/中/長非 None」的維度算加權，避免缺失維度把分數拖向中性
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

import statistics
import threading

import pandas as pd

from app.data.adjuster import load_adjusted_price
from app.data.clock import taipei_today
from app.data.db import Database
from app.data.market_type import is_etf
from app.indicators import chips as chip_ind
from app.indicators import fundamentals as fund_ind
from app.indicators import technical as tech
from app.scoring import constants as C
from app.scoring import rubric as R

# 對外 re-export：歷史上有 caller 直接用 `engine.STALE_THRESHOLD_DAYS`，不打破。
STALE_THRESHOLD_DAYS = C.STALE_THRESHOLD_DAYS


@dataclass
class ScoreBreakdown:
    total: Optional[float]
    completeness: float = 1.0              # 有效子權重 / 總子權重，1.0 = 全部資料齊全
    parts: dict[str, Optional[float]] = field(default_factory=dict)


@dataclass
class StockScore:
    stock_id: str
    stock_name: str
    as_of: str
    close: float
    short: ScoreBreakdown
    mid: ScoreBreakdown
    long: ScoreBreakdown
    signals: dict[str, Any] = field(default_factory=dict)
    is_stale: bool = False                 # 最新資料是否過期
    is_pending: bool = False               # as_of 等於今日且當下 < 14:00 → 資料尚未收盤

    def to_row(self) -> dict:
        return {
            "stock_id": self.stock_id,
            "stock_name": self.stock_name,
            "as_of": self.as_of,
            "close": self.close,
            "short_score": self.short.total,
            "mid_score": self.mid.total,
            "long_score": self.long.total,
            "recommendation": self.signals.get("recommendation", ""),
        }


def _weighted(
    parts: dict[str, Optional[float]],
    weights: dict[str, float],
    min_completeness: float = R.MIN_DIM_COMPLETENESS,
) -> tuple[Optional[float], float]:
    """None-aware 加權：跳過 None parts，用剩下的權重 re-normalize。

    回傳 (total, completeness)：
    - total=None：completeness < min_completeness（有效資料太少，拒絕給分）
    - completeness：用到的權重佔總權重的比例，反映「這個分數有多可信」
    """
    total_w = sum(weights.get(k, 0) for k in parts)
    if total_w == 0:
        return None, 0.0
    used_w = 0.0
    acc = 0.0
    for k, v in parts.items():
        if v is None:
            continue
        w = weights.get(k, 0)
        used_w += w
        acc += v * w
    completeness = used_w / total_w if total_w > 0 else 0.0
    if used_w == 0 or completeness < min_completeness:
        return None, completeness
    return round(acc / used_w, 1), completeness


def _round_or_none(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v, 1)


def score_short_term(price_df: pd.DataFrame, chip_snap: dict) -> ScoreBreakdown:
    last = price_df.iloc[-1]
    prev = price_df.iloc[-2] if len(price_df) >= 2 else None
    parts: dict[str, Optional[float]] = {
        "ma_alignment": R.score_ma_alignment_short(last),
        "kd": R.score_kd(last, prev),
        "macd": R.score_macd(last, prev),
        "rsi": R.score_rsi(last),
        "bollinger": R.score_bollinger(last),
        "volume": R.score_volume(last),
        "vr_macd": R.score_vr_macd(last, prev),
        "foreign": R.score_foreign_short(chip_snap),
        "trust": R.score_trust_short(chip_snap),
        "margin_change": R.score_margin_change(chip_snap),
    }
    total, completeness = _weighted(parts, R.SHORT_TERM_WEIGHTS)
    return ScoreBreakdown(
        total=total,
        completeness=round(completeness, 3),
        parts={k: _round_or_none(v) for k, v in parts.items()},
    )


def score_mid_term(
    price_df: pd.DataFrame,
    chip_snap: dict,
    fund_snap: dict,
    stock_id: str | None = None,
) -> ScoreBreakdown:
    """中期分數。對 ETF 直接回 None（completeness=0）— ETF 沒有 EPS / 月營收概念，且機構買賣
    多反映申購/贖回需求而非「機構看好」。讓 ETF mid 跟個股 mid 在 watchlist 直接比大小是錯的；
    跟 long 對 ETF 的 None 處理一致。
    """
    if is_etf(stock_id):
        return ScoreBreakdown(total=None, completeness=0.0, parts={
            "trend": None, "foreign_cum": None, "trust_cum": None,
            "eps_growth": None, "revenue_growth": None, "vr_macd": None,
        })
    last = price_df.iloc[-1]
    prev = price_df.iloc[-2] if len(price_df) >= 2 else None
    parts: dict[str, Optional[float]] = {
        "trend": R.score_trend_mid(last),
        "foreign_cum": R.score_foreign_mid(chip_snap),
        "trust_cum": R.score_trust_mid(chip_snap),
        "eps_growth": R.score_eps_growth(fund_snap),
        "revenue_growth": R.score_revenue_growth(fund_snap),
        "vr_macd": R.score_vr_macd(last, prev),
    }
    total, completeness = _weighted(parts, R.MID_TERM_WEIGHTS)
    return ScoreBreakdown(
        total=total,
        completeness=round(completeness, 3),
        parts={k: _round_or_none(v) for k, v in parts.items()},
    )


def score_long_term(fund_snap: dict, stock_id: str | None = None) -> ScoreBreakdown:
    """長期分數。對 ETF 直接回 None（completeness=0）— ETF 沒有 EPS/ROE/月營收，
    用 re-normalize 出來的代用值會誤導與個股的對比，所以乾脆不算。

    EPS 維度從 `score_eps_growth` (yoy) 換成 `score_eps_cagr_3y` (3 年 CAGR)，
    避免 mid 與 long 都吃同一筆 yoy 造成 double counting。"""
    if is_etf(stock_id):
        return ScoreBreakdown(total=None, completeness=0.0, parts={
            "roe": None, "margin_quality": None, "eps_cagr_3y": None,
            "dividend": None, "valuation": None,
        })
    parts: dict[str, Optional[float]] = {
        "roe": R.score_roe(fund_snap),
        "margin_quality": R.score_margin_quality(fund_snap),
        "eps_cagr_3y": R.score_eps_cagr_3y(fund_snap),
        "dividend": R.score_dividend(fund_snap),
        "valuation": R.score_valuation(fund_snap),
    }
    total, completeness = _weighted(parts, R.LONG_TERM_WEIGHTS)
    # 金融業 completeness cap：金控/銀行/保險的 IFRS 報表沒有 Revenue/GrossProfit/OperatingIncome
    # 欄位 → roe_ttm + margin_quality 都 None；剩 eps_cagr_3y + dividend + valuation 共 0.40 權重
    # 撐起 long score。eps_cagr_3y 在金融業常滿分 100 (EPS 穩定成長) → long 80+ top 1%。
    # cap 在 75 避免 top 排行被假完整污染。完整度 ≥ 0.50 表示 ROE 或 margin 有資料、不需 cap。
    if (
        total is not None
        and R._is_financial_stock(fund_snap)
        and completeness < 0.50
    ):
        total = min(total, 75.0)
    return ScoreBreakdown(
        total=total,
        completeness=round(completeness, 3),
        parts={k: _round_or_none(v) for k, v in parts.items()},
    )


def composite_score(
    short: Optional[float], mid: Optional[float], long_: Optional[float],
    regime: str | None = None,
) -> tuple[Optional[float], float]:
    """綜合分數：短/中/長跳過 None 維度、re-normalize 權重。

    回傳 (composite, completeness)
    - completeness = 用到的維度權重 / 三維總權重 (1.0 = 三維都有分)

    `regime` 參數（v5e #1）：傳入「bull / bear / neutral」改用 regime-aware 權重表。
    None → fallback 到固定 R.COMPOSITE_WEIGHTS（向後相容、單元測試用）。
    """
    dims: dict[str, Optional[float]] = {"short": short, "mid": mid, "long": long_}
    if regime:
        from app.scoring.regime import composite_weights_for_regime
        weights = composite_weights_for_regime(regime)
    else:
        weights = R.COMPOSITE_WEIGHTS
    return _weighted(dims, weights, min_completeness=R.MIN_DIM_COMPLETENESS)


def overall_completeness(
    short: ScoreBreakdown,
    mid: ScoreBreakdown,
    long_: ScoreBreakdown,
    regime: str | None = None,
) -> float:
    """把三個維度的子指標 completeness 依 composite 權重加權平均，作為整體評分可信度。

    `regime` 對齊 `composite_score`：給定時走 regime-aware 權重，否則 fallback 固定 COMPOSITE_WEIGHTS。
    若 data_completeness 仍用固定權重、composite 卻用 regime-aware，雷達警告與實際評分加權會在多/空頭不一致。
    """
    if regime:
        from app.scoring.regime import composite_weights_for_regime
        w = composite_weights_for_regime(regime)
    else:
        w = R.COMPOSITE_WEIGHTS
    total_w = w["short"] + w["mid"] + w["long"]
    return round(
        (short.completeness * w["short"] + mid.completeness * w["mid"] + long_.completeness * w["long"]) / total_w,
        3,
    )


def check_stale(as_of: str, threshold_days: int = STALE_THRESHOLD_DAYS) -> bool:
    """as_of 格式 'YYYY-MM-DD'，若距今超過 threshold 天則視為過期。"""
    try:
        d = date.fromisoformat(as_of)
    except (ValueError, TypeError):
        return False
    return (taipei_today() - d).days > threshold_days


def is_pending_intraday(as_of: str) -> bool:
    """as_of 等於台北今日且當下 < MARKET_SETTLED_HOUR_TPE → pending（資料尚未收盤確認）。

    這是防禦性檢查：正常 ingestion 走 TWSE OpenAPI 不會有盤中資料，
    但若使用者手動跑 `market_update --date today` 在 13:30 之前，daily_price 會被部分資料填上，
    score_stock 出來的分數就是 look-ahead 自己。標 pending 比靜默信任安全。
    """
    try:
        from app.data.clock import taipei_now
        d = date.fromisoformat(as_of)
    except (ValueError, TypeError):
        return False
    now = taipei_now()
    if d != now.date():
        return False
    return now.hour < C.MARKET_SETTLED_HOUR_TPE


# ======================================================================
# 進出場訊號
# ======================================================================
def recommendation_label(score: Optional[float]) -> str:
    if score is None:
        return C.RECOMMENDATION_INSUFFICIENT
    for threshold, label in C.RECOMMENDATION_TIERS:
        if score >= threshold:
            return label
    return C.RECOMMENDATION_BEAR


def build_signals(
    price_df: pd.DataFrame,
    short: ScoreBreakdown,
    mid: ScoreBreakdown,
    long_: ScoreBreakdown,
    chip_snap: dict,
    regime: str | None = None,
) -> dict[str, Any]:
    last = price_df.iloc[-1]
    close = float(last["close"])
    signals: dict[str, Any] = {}

    # 綜合建議：None-aware + regime-aware（v5e #1）
    composite, comp_completeness = composite_score(short.total, mid.total, long_.total, regime=regime)
    signals["composite_score"] = composite
    signals["composite_completeness"] = round(comp_completeness, 3)
    signals["recommendation"] = recommendation_label(composite)
    if regime:
        signals["regime"] = regime

    # 進場建議（必須分數存在才考慮）
    entry: list[str] = []
    if short.total is not None and short.total >= C.ENTRY_SHORT_TOTAL and (short.parts.get("kd") or 0) >= C.ENTRY_KD_PART:
        entry.append("KD 偏多且短線轉強，可分批布局")
    if (short.parts.get("volume") or 0) >= C.ENTRY_VOL_PART and (short.parts.get("ma_alignment") or 0) >= C.ENTRY_MA_ALIGN_PART:
        entry.append("量增+均線多頭排列，追價風險偏低")
    if (not pd.isna(last.get("ma20"))
            and mid.total is not None
            and close < last["ma20"] * C.ENTRY_NEAR_MA20_RATIO
            and mid.total >= C.ENTRY_MID_TOTAL_NEAR_MA20):
        entry.append(f"中期多頭，接近月線（{last['ma20']:.2f}）附近可留意")
    if mid.total is not None and long_.total is not None and mid.total >= C.ENTRY_MID_TOTAL and long_.total >= C.ENTRY_LONG_TOTAL:
        entry.append("中長期趨勢向上，逢回可布局")
    signals["entry"] = entry or ["目前無明確進場訊號"]

    # 停損參考
    stop_loss: list[str] = []
    if not pd.isna(last.get("ma20")):
        stop_loss.append(f"跌破月線 {last['ma20']:.2f}（-{(close-last['ma20'])/close*100:.1f}%）")
    if not pd.isna(last.get("ma60")):
        stop_loss.append(f"跌破季線 {last['ma60']:.2f}")
    sl_pct = C.DEFAULT_STOP_LOSS_PCT
    stop_loss.append(f"停損 -{sl_pct*100:.0f}%（{close * (1 - sl_pct):.2f}）")
    signals["stop_loss"] = stop_loss

    # 停利參考
    take_profit: list[str] = []
    if not pd.isna(last.get("rsi14")) and last["rsi14"] >= C.TAKE_PROFIT_RSI:
        take_profit.append(f"RSI {last['rsi14']:.1f} 已過熱，可分批停利")
    if not pd.isna(last.get("bb_upper")):
        take_profit.append(f"布林上軌 {last['bb_upper']:.2f} 附近留意")
    tp1, tp2 = C.TAKE_PROFIT_TIER1_PCT, C.TAKE_PROFIT_TIER2_PCT
    take_profit.append(
        f"停利 +{tp1*100:.0f}%（{close * (1 + tp1):.2f}）/"
        f"+{tp2*100:.0f}%（{close * (1 + tp2):.2f}）"
    )
    signals["take_profit"] = take_profit

    # 風險提示
    warnings: list[str] = []
    if not pd.isna(last.get("rsi14")) and last["rsi14"] >= C.WARN_RSI_OVERBOUGHT:
        warnings.append(f"⚠️ RSI {last['rsi14']:.1f} 超買")
    if len(price_df) >= 6:
        ret5 = (close / price_df.iloc[-6]["close"] - 1)
        if ret5 > C.WARN_5D_RETURN_PCT:
            warnings.append(f"⚠️ 近 5 日漲幅 {ret5*100:.1f}%，留意追高")
    chg5 = chip_snap.get("margin_chg5")
    if chg5 is not None and chg5 > C.WARN_MARGIN_5D_INC_PCT:
        warnings.append(f"⚠️ 融資 5 日增 {chg5*100:.1f}%，散戶追高")
    if chip_snap.get("foreign_streak_sell", 0) >= C.WARN_FOREIGN_STREAK_SELL:
        warnings.append(f"⚠️ 外資連賣 {chip_snap['foreign_streak_sell']} 日")
    # 完整度過低則增加提醒
    dim_completeness = overall_completeness(short, mid, long_, regime=regime)
    if dim_completeness < C.WARN_LOW_COMPLETENESS:
        warnings.append(f"⚠️ 評分可信度 {dim_completeness*100:.0f}%：部分指標缺資料，請審慎參考")
    signals["warnings"] = warnings

    return signals


# ======================================================================
# 主函式
# ======================================================================
def _swap_to_adjusted(price: pd.DataFrame) -> pd.DataFrame:
    """若 daily_price_adj 有資料，把 close/open/high/low 換成還原價。
    這樣 0050 在 2025 1:4 分割後 ma5/ma60 不會出現假跳崖。"""
    df = price.copy()
    for col in ("close", "open", "high", "low"):
        adj_col = f"{col}_adj"
        if adj_col in df.columns:
            # 還原價缺失就 fallback 原始價
            df[col] = df[adj_col].fillna(df[col])
    return df


def industry_yield_z_for_stock(
    db: Database,
    stock_id: str,
    conn=None,
    *,
    as_of: str | None = None,
) -> Optional[float]:
    """單股版的同產業殖利率 z-score，邏輯與 radar._industry_yield_z_map 對齊。

    雷達批次掃描時會預先算好整個產業的 yield z-score 注入 fund_snap，讓
    score_dividend 走「同產業比較」分支（避免不同產業殖利率天花板差異造成不公平）。
    個股詳情頁是即時呼叫 score_stock，沒有預載全市場 per_pbr，必須在這裡補一次同樣
    的計算，否則同一檔股票在雷達/自選總覽 vs 個股詳情頁會看到不同的長期分數。

    `conn` 可選：score_stock 把它跟其他 read_sql 共用同一連線，省一次 open。
    沒帶就自己開（保留 backward compat）。

    回傳 None 的情況：缺 industry、產業內樣本 < 4、stdev≈0。
    """
    if conn is None:
        with db.connect() as c:
            return _industry_yield_z_with_conn(c, stock_id, as_of=as_of)
    return _industry_yield_z_with_conn(conn, stock_id, as_of=as_of)


# 同產業殖利率 z-score 的小型快取：key=(industry, per_pbr 最新日期)。
# per_pbr 每日 market_update 後最新日會推進，自動失效；同一日同產業只算一次。
# 內容是 (yields_by_sid: dict, mean, std) — 個股 z 直接用 sid 查 yield 後算。
#
# 限制 cache key 數量避免長期 process（FastAPI）下 dict 緩慢累積：每次 cache_key 變動
# （新交易日進來），舊交易日的 entry 全清掉，因為 per_pbr 已往前推進、舊 z 不再有意義。
# 並用 lock 保證並發 score_stock / score_all 不會 race（CPython dict 的 __setitem__ 看似
# atomic 但 cleanup 段需要排他）。
_yield_z_cache: dict[tuple[str, str | None], dict[str, float] | None] = {}
_yield_z_cache_lock = threading.Lock()
_YIELD_Z_CACHE_MAX_KEYS = 64  # 一個交易日 ~30 個產業 → 兩天份就 64 夠用


def industry_yield_z_from_yields(yields_by_sid: dict[str, float]) -> dict[str, float]:
    """共用 helper：給定 {sid -> dividend_yield} 同產業同期樣本 → 回 {sid -> z-score}。

    回空 dict 的情境：peer group 太小（<4）、stdev≈0、輸入空。

    為什麼要抽：score_stock（個股詳情頁，per-stock）跟 score_all（雷達批次，bulk）兩條路徑
    各自算 z 容易 drift；CLAUDE.md 明確警告這條會讓 detail page 跟 watchlist 看到不同長期分數。
    這個 helper 把核心數學集中，各 caller 仍各自管 I/O 與快取策略。
    """
    if len(yields_by_sid) < 4:
        return {}
    ys = list(yields_by_sid.values())
    mean = statistics.mean(ys)
    try:
        std = statistics.stdev(ys)
    except statistics.StatisticsError:
        return {}
    if std <= 1e-9:
        return {}
    return {sid: (y - mean) / std for sid, y in yields_by_sid.items()}


def _industry_yield_z_with_conn(conn, stock_id: str, *, as_of: str | None = None) -> Optional[float]:
    ind_row = conn.execute(
        "SELECT industry_category FROM stock_info WHERE stock_id=?", (stock_id,)
    ).fetchone()
    if not ind_row or not ind_row["industry_category"]:
        return None
    industry = ind_row["industry_category"]

    # cache key：用 per_pbr 在 as_of 以前的最新日期，避免歷史重播吃到未來資料。
    if as_of:
        mx_row = conn.execute(
            "SELECT MAX(date) AS mx FROM per_pbr WHERE date <= ?",
            (as_of,),
        ).fetchone()
    else:
        mx_row = conn.execute("SELECT MAX(date) AS mx FROM per_pbr").fetchone()
    if not mx_row or not mx_row["mx"]:
        return None
    cache_key = (industry, mx_row["mx"] if mx_row else None)

    with _yield_z_cache_lock:
        cached = _yield_z_cache.get(cache_key)
        cache_present = cache_key in _yield_z_cache
    if not cache_present:
        # 抓「該產業每檔最新 (在 as_of 之前) 的 dividend_yield」
        if as_of:
            sql = """
                SELECT p.stock_id, p.dividend_yield FROM per_pbr p
                INNER JOIN (
                    SELECT stock_id, MAX(date) AS mx
                    FROM per_pbr
                    WHERE date <= ?
                    GROUP BY stock_id
                ) m ON p.stock_id = m.stock_id AND p.date = m.mx
                INNER JOIN stock_info i ON i.stock_id = p.stock_id
                WHERE i.industry_category = ? AND p.dividend_yield IS NOT NULL
                """
            args = (as_of, industry)
        else:
            sql = """
                SELECT p.stock_id, p.dividend_yield FROM per_pbr p
                INNER JOIN (
                    SELECT stock_id, MAX(date) AS mx FROM per_pbr GROUP BY stock_id
                ) m ON p.stock_id = m.stock_id AND p.date = m.mx
                INNER JOIN stock_info i ON i.stock_id = p.stock_id
                WHERE i.industry_category = ? AND p.dividend_yield IS NOT NULL
                """
            args = (industry,)
        rows = conn.execute(sql, args).fetchall()
        yields_by_sid = {r["stock_id"]: float(r["dividend_yield"]) for r in rows}
        # 算整個產業的 z map（共用 helper，與 radar 同步）；空 dict = peer group 無效
        z_map = industry_yield_z_from_yields(yields_by_sid)
        cached = z_map if z_map else None
        with _yield_z_cache_lock:
            # FIFO 清理：當 cache 滿就清掉（key 已經依 (industry, latest_per_pbr_date) 切，
            # 同一日新交易日進來時所有舊 entry 都不會再命中，清光成本可忽略）
            if len(_yield_z_cache) >= _YIELD_Z_CACHE_MAX_KEYS:
                _yield_z_cache.clear()
            _yield_z_cache[cache_key] = cached
    if cached is None:
        return None
    return cached.get(stock_id)


def _load_stock_bundle(conn, stock_id: str, *, as_of: str | None = None) -> dict[str, pd.DataFrame]:
    """一次抓 5 張表（margin / per_pbr / financials / financials_cumulative / financials_quarterly_derived）。
    共用同一個 conn，避免 score_stock 每次 open / close 五次連線。"""
    # margin / per_pbr 是逐日 row，date 即觀測日，沒有公告 lag 概念，date <= as_of 即可。
    # 三張財報表 (fin / fin_cum / fin_derived) 都有 publish_date：date 是季末日（事件日），
    # 但實際法定公告日落在季末後 6-12 週 → 用 COALESCE(publish_date, date) <= as_of 才不會 look-ahead。
    date_clause = " AND date <= ?" if as_of else ""
    params = [stock_id, as_of] if as_of else [stock_id]
    fin_sql = "SELECT * FROM financials WHERE stock_id=?"
    fin_cum_sql = "SELECT * FROM financials_cumulative WHERE stock_id=?"
    fin_derived_sql = "SELECT * FROM financials_quarterly_derived WHERE stock_id=?"
    fin_params = [stock_id]
    fin_cum_params = [stock_id]
    fin_derived_params = [stock_id]
    if as_of:
        fin_sql += " AND date <= ? AND COALESCE(publish_date, date) <= ?"
        fin_cum_sql += " AND date <= ? AND COALESCE(publish_date, date) <= ?"
        fin_derived_sql += " AND date <= ? AND COALESCE(publish_date, date) <= ?"
        fin_params.extend([as_of, as_of])
        fin_cum_params.extend([as_of, as_of])
        fin_derived_params.extend([as_of, as_of])
    fin_sql += " ORDER BY date"
    fin_cum_sql += " ORDER BY date"
    fin_derived_sql += " ORDER BY date"
    return {
        "margin": pd.read_sql_query(
            "SELECT * FROM margin WHERE stock_id=?" + date_clause + " ORDER BY date",
            conn,
            params=params,
        ),
        "per_pbr": pd.read_sql_query(
            "SELECT * FROM per_pbr WHERE stock_id=?" + date_clause + " ORDER BY date",
            conn,
            params=params,
        ),
        "fin": pd.read_sql_query(
            fin_sql,
            conn,
            params=fin_params,
        ),
        "fin_cum": pd.read_sql_query(
            fin_cum_sql,
            conn,
            params=fin_cum_params,
        ),
        "fin_derived": pd.read_sql_query(
            fin_derived_sql,
            conn,
            params=fin_derived_params,
        ),
    }


def _override_last_close(price: pd.DataFrame, live_price: float) -> pd.DataFrame:
    """把最後一筆的 close 換成 live_price，high/low 同步擴張包住新 close。

    用途：盤中即時報價 / what-if 假設價位重算分數。tech.enrich 必須在覆寫**之後**呼叫，
    這樣 MA / RSI / KD / Bollinger 才會反映新 close（覆寫之前 enrich 算出的指標
    是基於舊 close 的，無意義）。

    high/low 為什麼要擴張：KD 用 (close-low9)/(high9-low9)，若 close 跑出 [low, high]
    區間，RSV 會 > 100 或 < 0。把當日 high/low 擴張包住 live_price 是最直觀的修法
    （概念上等於：盤中觸及 live_price 那一刻就刷新當日高/低）。
    """
    df = price.copy()
    last_idx = df.index[-1]
    old_high = df.at[last_idx, "high"]
    old_low = df.at[last_idx, "low"]
    df.at[last_idx, "close"] = live_price
    if pd.notna(old_high):
        df.at[last_idx, "high"] = max(float(old_high), live_price)
    else:
        df.at[last_idx, "high"] = live_price
    if pd.notna(old_low):
        df.at[last_idx, "low"] = min(float(old_low), live_price)
    else:
        df.at[last_idx, "low"] = live_price
    return df


def score_stock(
    db: Database,
    stock_id: str,
    stock_name: str = "",
    *,
    live_price: float | None = None,
    as_of: str | date | None = None,
) -> StockScore | None:
    """從 DB 讀資料、計算指標、產出評分。
    技術指標用「還原價」計算，避免除權息或分割造成 MA / KD 等指標失真。

    live_price: 若給定（盤中即時報價或 what-if 假設值），覆寫最後一筆 close 後再算技術指標。
    短期 / 中期分數會反映新 close；長期分數（ROE/EPS/股利）不受影響。回傳的 close 欄位
    為覆寫後的數值，signals 內會多塞 `live_price_used=True` 讓 UI 顯示「盤中估算」標記。
    as_of: 歷史重播基準日（YYYY-MM-DD/date）。提供後僅使用 `date <= as_of` 且
    `publish_date <= as_of` 的資料，語意與 score_all(as_of) 對齊。
    """
    as_of_str: str | None = None
    if isinstance(as_of, str):
        as_of_str = date.fromisoformat(as_of).isoformat()
    elif isinstance(as_of, date):
        as_of_str = as_of.isoformat()

    if as_of_str and live_price is not None and live_price > 0:
        raise ValueError("as_of 與 live_price 不能同時使用")

    price = load_adjusted_price(db, stock_id, as_of=as_of_str)
    if price.empty or len(price) < 60:
        return None

    price = _swap_to_adjusted(price)
    if live_price is not None and live_price > 0:
        price = _override_last_close(price, float(live_price))
    price = tech.enrich(price)
    inst = db.load_institutional(stock_id, as_of=as_of_str)

    # 全部 read_sql + industry_yield_z 共用一個 conn，省下重複 open / close。
    with db.connect() as conn:
        bundle = _load_stock_bundle(conn, stock_id, as_of=as_of_str)
        z = industry_yield_z_for_stock(db, stock_id, conn=conn, as_of=as_of_str)
    margin = bundle["margin"]
    per_pbr = bundle["per_pbr"]
    fin = bundle["fin"]
    fin_cum = bundle["fin_cum"]
    fin_derived = bundle["fin_derived"]
    if not margin.empty:
        margin["date"] = pd.to_datetime(margin["date"])
    if not per_pbr.empty:
        per_pbr["date"] = pd.to_datetime(per_pbr["date"])

    chip_snap = chip_ind.latest_chip_snapshot(inst, margin)
    # 注入 20 日平均日成交量（給 score_foreign_mid / score_trust_mid 做 % of ADV 規模化）。
    # 必須與 radar.score_all 同步，否則「個股詳情」與「批次快照」對 foreign_mid 評分會不一致。
    if "volume" in price.columns and len(price) >= 20:
        avg_vol_20 = float(price["volume"].tail(20).mean())
        if avg_vol_20 > 0:
            chip_snap["avg_volume_20"] = avg_vol_20
    fund_snap = fund_ind.fundamental_snapshot(fin, per_pbr, fin_cum, fin_derived)
    if z is not None:
        fund_snap["dividend_yield_z"] = z
    # 注入 industry_category 給 score_revenue_growth (建設股 TTM 切換) + 金融業 cap 用
    with db.connect() as conn:
        ind_row = conn.execute(
            "SELECT industry_category FROM stock_info WHERE stock_id=?", (stock_id,)
        ).fetchone()
    if ind_row and ind_row["industry_category"]:
        fund_snap["industry_category"] = ind_row["industry_category"]

    short = score_short_term(price, chip_snap)
    mid = score_mid_term(price, chip_snap, fund_snap, stock_id=stock_id)
    long_ = score_long_term(fund_snap, stock_id=stock_id)

    as_of = str(price.iloc[-1]["date"].date())
    is_stale = check_stale(as_of)
    is_pending = is_pending_intraday(as_of)

    # v5e #1：偵測 regime 給 composite_score 動態調權
    from app.scoring.regime import detect_regime
    regime = detect_regime(db, as_of=as_of_str or as_of)

    signals = build_signals(price, short, mid, long_, chip_snap, regime=regime)
    signals["chip_snapshot"] = chip_snap
    signals["fundamental_snapshot"] = fund_snap
    signals["data_completeness"] = overall_completeness(short, mid, long_, regime=regime)
    signals["is_stale"] = is_stale
    signals["is_pending"] = is_pending
    signals["stale_days"] = (taipei_today() - date.fromisoformat(as_of)).days if is_stale else 0
    # 4 個風格分數（v5c Wave 2）：Value / Growth / Momentum / Income
    # 用既有 sub-factor 加權平均、不新增 sub-factor、不影響 long/mid/short/composite
    from app.scoring.style import compute_style_scores
    signals["style_scores"] = compute_style_scores(short.parts, mid.parts, long_.parts)
    if live_price is not None and live_price > 0:
        signals["live_price_used"] = True
        signals["live_price"] = float(live_price)

    return StockScore(
        stock_id=stock_id,
        stock_name=stock_name,
        as_of=as_of,
        close=float(price.iloc[-1]["close"]),
        short=short,
        mid=mid,
        long=long_,
        signals=signals,
        is_stale=is_stale,
        is_pending=is_pending,
    )
