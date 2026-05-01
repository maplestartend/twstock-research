"""個股 vs 同業中位數比較。

直接打 cache 表（per_pbr / monthly_revenue / financials_quarterly_derived）算出
產業內中位數 + 該股排名，避免每檔重跑 fundamental_snapshot（太貴）。

V1 涵蓋：本益比、殖利率、毛利率、EPS YoY、營收 YoY。

ROE 因為需要 TTM 淨利 / 期末股東權益（兩張表 join + 4 季加總）暫不納入；待用戶
反饋顯示需要時再加（評估：~30ms→100ms 的查詢成本），與其先做不如先觀察使用率。
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Literal

from app.data.db import Database
from app.data.market_type import is_etf
from app.data.sql_utils import make_placeholders


# 同產業樣本不足這個數字就放棄 — 中位數沒意義（且容易把離群值當成「同業」）
MIN_PEERS = 5


@dataclass
class MetricSpec:
    key: str
    label: str
    unit: str
    better: Literal["higher", "lower"]


# 顯示順序固定（先估值類、再獲利類、再成長類）
_METRIC_SPECS: list[MetricSpec] = [
    MetricSpec("per", "本益比", "倍", "lower"),
    MetricSpec("dividend_yield", "殖利率", "%", "higher"),
    MetricSpec("gross_margin", "毛利率", "%", "higher"),
    MetricSpec("eps_yoy", "EPS 年增", "%", "higher"),
    MetricSpec("revenue_yoy", "營收年增", "%", "higher"),
]


def _peer_industry(conn, stock_id: str) -> tuple[str | None, list[str]]:
    """回 (industry, [產業內 stock_id 列表，含自己])。

    過濾條件：
    - 必須有 industry_category（興櫃常為空）
    - 自己若是 ETF（代號 00 開頭）→ 直接 None；ETF 沒有 EPS/ROE/毛利率語意
    - peer 列表也排除 ETF（在程式端用 is_etf 過濾，避免「ETF」這個 industry_category
      把 252 檔受益憑證當成同業）
    """
    if is_etf(stock_id):
        return None, []
    r = conn.execute(
        "SELECT industry_category FROM stock_info WHERE stock_id=?", (stock_id,)
    ).fetchone()
    if not r or not r["industry_category"]:
        return None, []
    industry = r["industry_category"]
    rows = conn.execute(
        "SELECT stock_id FROM stock_info WHERE industry_category=?",
        (industry,),
    ).fetchall()
    sids = [r["stock_id"] for r in rows if not is_etf(r["stock_id"])]
    return industry, sids


def _latest_per_pbr(conn, sids: list[str]) -> dict[str, dict]:
    """每檔最新一筆 per_pbr 的 per / dividend_yield。"""
    if not sids:
        return {}
    ph = make_placeholders(len(sids))
    rows = conn.execute(
        f"""
        SELECT p.stock_id, p.per, p.dividend_yield
        FROM per_pbr p
        INNER JOIN (
            SELECT stock_id, MAX(date) AS d
            FROM per_pbr WHERE stock_id IN ({ph})
            GROUP BY stock_id
        ) m ON m.stock_id = p.stock_id AND m.d = p.date
        """,
        sids,
    ).fetchall()
    return {r["stock_id"]: dict(r) for r in rows}


def _latest_revenue_yoy(conn, sids: list[str]) -> dict[str, float]:
    """每檔最新一筆 monthly_revenue 的 yoy_pct（已是 0.xx 小數，例如 0.18 = 18%）。"""
    if not sids:
        return {}
    ph = make_placeholders(len(sids))
    rows = conn.execute(
        f"""
        SELECT mr.stock_id, mr.yoy_pct
        FROM monthly_revenue mr
        INNER JOIN (
            SELECT stock_id, MAX(date) AS d
            FROM monthly_revenue WHERE stock_id IN ({ph})
            GROUP BY stock_id
        ) m ON m.stock_id = mr.stock_id AND m.d = mr.date
        WHERE mr.yoy_pct IS NOT NULL
        """,
        sids,
    ).fetchall()
    return {r["stock_id"]: float(r["yoy_pct"]) for r in rows}


def _latest_quarterly_metrics(conn, sids: list[str]) -> dict[str, dict]:
    """從 financials_quarterly_derived 算 gross_margin（最新季） + eps_yoy（同期 vs 上一年）。

    long-format 表用 GROUP BY 樞紐成 wide：每檔每季抓 Revenue / GrossProfit / EPS。
    取最新有公布的那一季當作 latest_quarter，再去找去年同 quarter 的 EPS 算 YoY。
    """
    if not sids:
        return {}
    ph = make_placeholders(len(sids))
    # 抓近 6 季（足夠涵蓋 latest 與 latest-4 季）的 wide 形式
    rows = conn.execute(
        f"""
        SELECT stock_id, year, quarter, type, value
        FROM financials_quarterly_derived
        WHERE stock_id IN ({ph})
          AND type IN ('Revenue', 'GrossProfit', 'EPS')
        ORDER BY stock_id, year DESC, quarter DESC
        """,
        sids,
    ).fetchall()

    # 樞紐成：{(sid, year, quarter): {"Revenue": ..., "GrossProfit": ..., "EPS": ...}}
    by_sq: dict[tuple[str, int, int], dict[str, float]] = {}
    for r in rows:
        key = (r["stock_id"], int(r["year"]), int(r["quarter"]))
        by_sq.setdefault(key, {})[r["type"]] = float(r["value"]) if r["value"] is not None else None

    # 對每檔，找它最新的 (year, quarter) 與 (year-1, quarter)
    out: dict[str, dict] = {}
    by_sid_quarters: dict[str, list[tuple[int, int]]] = {}
    for (sid, y, q) in by_sq.keys():
        by_sid_quarters.setdefault(sid, []).append((y, q))
    for sid, quarters in by_sid_quarters.items():
        quarters.sort(reverse=True)  # 最新在前
        latest_y, latest_q = quarters[0]
        latest = by_sq.get((sid, latest_y, latest_q), {})
        prev_year = by_sq.get((sid, latest_y - 1, latest_q), {})

        revenue = latest.get("Revenue")
        gross = latest.get("GrossProfit")
        eps_now = latest.get("EPS")
        eps_prev = prev_year.get("EPS")

        item: dict = {}
        if revenue and revenue > 0 and gross is not None:
            item["gross_margin"] = gross / revenue
        if eps_now is not None and eps_prev is not None and eps_prev != 0:
            # YoY 用「絕對值分母」避免 prev 為負時符號顛倒（與 fundamental_snapshot 一致）
            item["eps_yoy"] = (eps_now - eps_prev) / abs(eps_prev)
        if item:
            out[sid] = item
    return out


def _rank_in(values: list[float], target: float, better: str) -> tuple[int, int]:
    """target 在 values 中的排名（1 = 該方向最好），與分母（有效樣本數）。

    處理同分：用 < / > 計數，target 自己不算。
    """
    valid = [v for v in values if v is not None]
    if better == "higher":
        better_count = sum(1 for v in valid if v > target)
    else:
        better_count = sum(1 for v in valid if v < target)
    return better_count + 1, len(valid)


def compute_peer_comparison(db: Database, stock_id: str) -> dict | None:
    """主入口。回 None 表示無同業可比（產業空、樣本 < MIN_PEERS、或自己不在 stock_info）。

    回傳 dict 結構供 router 直接餵 Pydantic：
    {
      "stock_id": ..., "industry": ..., "peer_count": N,
      "metrics": [{"key", "label", "unit", "better_direction",
                   "value", "median", "rank", "out_of"}, ...],
    }
    """
    with db.connect() as conn:
        industry, peer_ids = _peer_industry(conn, stock_id)
        if industry is None or len(peer_ids) < MIN_PEERS:
            return None

        per_pbr = _latest_per_pbr(conn, peer_ids)
        rev_yoy = _latest_revenue_yoy(conn, peer_ids)
        qmetrics = _latest_quarterly_metrics(conn, peer_ids)

    # 把所有 peer 的 metric values 收集成 dict（後面算 median / rank）
    peer_values: dict[str, list[float]] = {spec.key: [] for spec in _METRIC_SPECS}
    self_values: dict[str, float | None] = {spec.key: None for spec in _METRIC_SPECS}

    for sid in peer_ids:
        per_row = per_pbr.get(sid, {})
        q_row = qmetrics.get(sid, {})

        per = per_row.get("per")
        # PER ≤ 0 沒有意義（虧損股 PER 為負或無），剔除避免拖偏 median
        if per is not None and per > 0:
            peer_values["per"].append(per)
            if sid == stock_id:
                self_values["per"] = per

        dy = per_row.get("dividend_yield")
        if dy is not None and dy >= 0:
            peer_values["dividend_yield"].append(dy)
            if sid == stock_id:
                self_values["dividend_yield"] = dy

        gm = q_row.get("gross_margin")
        if gm is not None:
            peer_values["gross_margin"].append(gm)
            if sid == stock_id:
                self_values["gross_margin"] = gm

        eps_yoy = q_row.get("eps_yoy")
        if eps_yoy is not None:
            peer_values["eps_yoy"].append(eps_yoy)
            if sid == stock_id:
                self_values["eps_yoy"] = eps_yoy

        ry = rev_yoy.get(sid)
        if ry is not None:
            peer_values["revenue_yoy"].append(ry)
            if sid == stock_id:
                self_values["revenue_yoy"] = ry

    metrics: list[dict] = []
    for spec in _METRIC_SPECS:
        vals = peer_values[spec.key]
        if len(vals) < MIN_PEERS:
            # 該指標樣本不足 → 仍輸出但 median=None / rank=None，前端可顯示 N/A
            metrics.append({
                "key": spec.key, "label": spec.label, "unit": spec.unit,
                "better_direction": spec.better,
                "value": self_values[spec.key],
                "median": None, "rank": None, "out_of": len(vals),
            })
            continue
        median = statistics.median(vals)
        my_value = self_values[spec.key]
        if my_value is None:
            metrics.append({
                "key": spec.key, "label": spec.label, "unit": spec.unit,
                "better_direction": spec.better,
                "value": None, "median": median,
                "rank": None, "out_of": len(vals),
            })
        else:
            rank, out_of = _rank_in(vals, my_value, spec.better)
            metrics.append({
                "key": spec.key, "label": spec.label, "unit": spec.unit,
                "better_direction": spec.better,
                "value": my_value, "median": median,
                "rank": rank, "out_of": out_of,
            })

    return {
        "stock_id": stock_id,
        "industry": industry,
        "peer_count": len(peer_ids),
        "metrics": metrics,
    }
