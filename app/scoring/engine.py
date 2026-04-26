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

import pandas as pd

from app.data.adjuster import load_adjusted_price
from app.data.clock import taipei_today
from app.data.db import Database
from app.data.market_type import is_etf
from app.indicators import chips as chip_ind
from app.indicators import fundamentals as fund_ind
from app.indicators import technical as tech
from app.scoring import rubric as R

# 資料視為過期的天數門檻（例：最新 daily_price 日期距今 > 3 天 → 標記為 stale）
STALE_THRESHOLD_DAYS = 3


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


def score_mid_term(price_df: pd.DataFrame, chip_snap: dict, fund_snap: dict) -> ScoreBreakdown:
    last = price_df.iloc[-1]
    parts: dict[str, Optional[float]] = {
        "trend": R.score_trend_mid(last),
        "foreign_cum": R.score_foreign_mid(chip_snap),
        "trust_cum": R.score_trust_mid(chip_snap),
        "eps_growth": R.score_eps_growth(fund_snap),
        "revenue_growth": R.score_revenue_growth(fund_snap),
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
    return ScoreBreakdown(
        total=total,
        completeness=round(completeness, 3),
        parts={k: _round_or_none(v) for k, v in parts.items()},
    )


def composite_score(
    short: Optional[float], mid: Optional[float], long_: Optional[float]
) -> tuple[Optional[float], float]:
    """綜合分數：短/中/長跳過 None 維度、re-normalize 權重。

    回傳 (composite, completeness)
    - completeness = 用到的維度權重 / 三維總權重 (1.0 = 三維都有分)
    """
    dims: dict[str, Optional[float]] = {"short": short, "mid": mid, "long": long_}
    return _weighted(dims, R.COMPOSITE_WEIGHTS, min_completeness=R.MIN_DIM_COMPLETENESS)


def overall_completeness(short: ScoreBreakdown, mid: ScoreBreakdown, long_: ScoreBreakdown) -> float:
    """把三個維度的子指標 completeness 依 composite 權重加權平均，作為整體評分可信度。"""
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


# 台股集合競價收盤 13:30，正常給 TWSE OpenAPI 30 分鐘後資料才會釋出，14:00 開始才算「真正收盤」。
# 在這之前若 daily_price 出現「今天」這筆資料，幾乎可以斷定是某個 ad-hoc 抓取程序灌的盤中部分資料，
# 此時計算出的分數視為 pending；UI 應該顯示警示，避免使用者照盤中分數做進場決策。
_MARKET_SETTLED_HOUR_TPE = 14


def is_pending_intraday(as_of: str) -> bool:
    """as_of 等於台北今日且當下 < 14:00 → pending（資料尚未收盤確認）。

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
    return now.hour < _MARKET_SETTLED_HOUR_TPE


# ======================================================================
# 進出場訊號
# ======================================================================
def recommendation_label(score: Optional[float]) -> str:
    if score is None:
        return "⚪ 資料不足"
    if score >= 75: return "🟢 強力偏多"
    if score >= 60: return "🟢 偏多"
    if score >= 45: return "🟡 中性"
    if score >= 30: return "🔴 偏空"
    return "🔴 強力偏空"


def build_signals(
    price_df: pd.DataFrame,
    short: ScoreBreakdown,
    mid: ScoreBreakdown,
    long_: ScoreBreakdown,
    chip_snap: dict,
) -> dict[str, Any]:
    last = price_df.iloc[-1]
    close = float(last["close"])
    signals: dict[str, Any] = {}

    # 綜合建議：None-aware
    composite, comp_completeness = composite_score(short.total, mid.total, long_.total)
    signals["composite_score"] = composite
    signals["composite_completeness"] = round(comp_completeness, 3)
    signals["recommendation"] = recommendation_label(composite)

    # 進場建議（必須分數存在才考慮）
    entry: list[str] = []
    if short.total is not None and short.total >= 65 and (short.parts.get("kd") or 0) >= 65:
        entry.append("KD 偏多且短線轉強，可分批布局")
    if (short.parts.get("volume") or 0) >= 65 and (short.parts.get("ma_alignment") or 0) >= 65:
        entry.append("量增+均線多頭排列，追價風險偏低")
    if (not pd.isna(last.get("ma20"))
            and mid.total is not None
            and close < last["ma20"] * 1.02
            and mid.total >= 60):
        entry.append(f"中期多頭，接近月線（{last['ma20']:.2f}）附近可留意")
    if mid.total is not None and long_.total is not None and mid.total >= 70 and long_.total >= 60:
        entry.append("中長期趨勢向上，逢回可布局")
    signals["entry"] = entry or ["目前無明確進場訊號"]

    # 停損參考
    stop_loss: list[str] = []
    if not pd.isna(last.get("ma20")):
        stop_loss.append(f"跌破月線 {last['ma20']:.2f}（-{(close-last['ma20'])/close*100:.1f}%）")
    if not pd.isna(last.get("ma60")):
        stop_loss.append(f"跌破季線 {last['ma60']:.2f}")
    stop_loss.append(f"停損 -8%（{close * 0.92:.2f}）")
    signals["stop_loss"] = stop_loss

    # 停利參考
    take_profit: list[str] = []
    if not pd.isna(last.get("rsi14")) and last["rsi14"] >= 70:
        take_profit.append(f"RSI {last['rsi14']:.1f} 已過熱，可分批停利")
    if not pd.isna(last.get("bb_upper")):
        take_profit.append(f"布林上軌 {last['bb_upper']:.2f} 附近留意")
    take_profit.append(f"停利 +15%（{close * 1.15:.2f}）/+25%（{close * 1.25:.2f}）")
    signals["take_profit"] = take_profit

    # 風險提示
    warnings: list[str] = []
    if not pd.isna(last.get("rsi14")) and last["rsi14"] >= 75:
        warnings.append(f"⚠️ RSI {last['rsi14']:.1f} 超買")
    if len(price_df) >= 6:
        ret5 = (close / price_df.iloc[-6]["close"] - 1) * 100
        if ret5 > 15:
            warnings.append(f"⚠️ 近 5 日漲幅 {ret5:.1f}%，留意追高")
    chg5 = chip_snap.get("margin_chg5")
    if chg5 is not None and chg5 > 0.15:
        warnings.append(f"⚠️ 融資 5 日增 {chg5*100:.1f}%，散戶追高")
    if chip_snap.get("foreign_streak_sell", 0) >= 5:
        warnings.append(f"⚠️ 外資連賣 {chip_snap['foreign_streak_sell']} 日")
    # 完整度過低則增加提醒
    dim_completeness = overall_completeness(short, mid, long_)
    if dim_completeness < 0.6:
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
            return _industry_yield_z_with_conn(c, stock_id)
    return _industry_yield_z_with_conn(conn, stock_id)


def _industry_yield_z_with_conn(conn, stock_id: str) -> Optional[float]:
    ind_row = conn.execute(
        "SELECT industry_category FROM stock_info WHERE stock_id=?", (stock_id,)
    ).fetchone()
    if not ind_row or not ind_row["industry_category"]:
        return None
    industry = ind_row["industry_category"]
    rows = conn.execute(
        """
        SELECT p.stock_id, p.dividend_yield FROM per_pbr p
        INNER JOIN (
            SELECT stock_id, MAX(date) AS mx FROM per_pbr GROUP BY stock_id
        ) m ON p.stock_id = m.stock_id AND p.date = m.mx
        INNER JOIN stock_info i ON i.stock_id = p.stock_id
        WHERE i.industry_category = ? AND p.dividend_yield IS NOT NULL
        """,
        (industry,),
    ).fetchall()
    yields = [float(r["dividend_yield"]) for r in rows]
    target = next((float(r["dividend_yield"]) for r in rows if r["stock_id"] == stock_id), None)
    if target is None or len(yields) < 4:
        return None
    mean = statistics.mean(yields)
    try:
        std = statistics.stdev(yields)
    except statistics.StatisticsError:
        return None
    if std <= 1e-9:
        return None
    return (target - mean) / std


def _load_stock_bundle(conn, stock_id: str) -> dict[str, pd.DataFrame]:
    """一次抓 5 張表（margin / per_pbr / financials / financials_cumulative / financials_quarterly_derived）。
    共用同一個 conn，避免 score_stock 每次 open / close 五次連線。"""
    return {
        "margin": pd.read_sql_query(
            "SELECT * FROM margin WHERE stock_id=? ORDER BY date", conn, params=[stock_id]
        ),
        "per_pbr": pd.read_sql_query(
            "SELECT * FROM per_pbr WHERE stock_id=? ORDER BY date", conn, params=[stock_id]
        ),
        "fin": pd.read_sql_query(
            "SELECT * FROM financials WHERE stock_id=? ORDER BY date", conn, params=[stock_id]
        ),
        "fin_cum": pd.read_sql_query(
            "SELECT * FROM financials_cumulative WHERE stock_id=? ORDER BY date",
            conn, params=[stock_id],
        ),
        "fin_derived": pd.read_sql_query(
            "SELECT * FROM financials_quarterly_derived WHERE stock_id=? ORDER BY date",
            conn, params=[stock_id],
        ),
    }


def score_stock(db: Database, stock_id: str, stock_name: str = "") -> StockScore | None:
    """從 DB 讀資料、計算指標、產出評分。
    技術指標用「還原價」計算，避免除權息或分割造成 MA / KD 等指標失真。"""
    price = load_adjusted_price(db, stock_id)
    if price.empty or len(price) < 60:
        return None

    price = _swap_to_adjusted(price)
    price = tech.enrich(price)
    inst = db.load_institutional(stock_id)

    # 全部 read_sql + industry_yield_z 共用一個 conn，省下重複 open / close。
    with db.connect() as conn:
        bundle = _load_stock_bundle(conn, stock_id)
        z = industry_yield_z_for_stock(db, stock_id, conn=conn)
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

    short = score_short_term(price, chip_snap)
    mid = score_mid_term(price, chip_snap, fund_snap)
    long_ = score_long_term(fund_snap, stock_id=stock_id)

    as_of = str(price.iloc[-1]["date"].date())
    is_stale = check_stale(as_of)
    is_pending = is_pending_intraday(as_of)

    signals = build_signals(price, short, mid, long_, chip_snap)
    signals["chip_snapshot"] = chip_snap
    signals["fundamental_snapshot"] = fund_snap
    signals["data_completeness"] = overall_completeness(short, mid, long_)
    signals["is_stale"] = is_stale
    signals["is_pending"] = is_pending
    signals["stale_days"] = (taipei_today() - date.fromisoformat(as_of)).days if is_stale else 0

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
