"""每日早報產生器：跑在 market_update 之後。輸出 daily_report.md + 可選推播通知。"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from app import portfolio as pf
from app import watchlist as wl_mod
from app.data.db import Database
from app.notifier import notify
from app.risk import enhanced_risk_signals
from app.scoring import history as sh
from app.scoring import radar

logger = logging.getLogger(__name__)


REPORT_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "reports"


def _top_change(db: Database, as_of: str, prev_as_of: str, target_ids: set[str]) -> pd.DataFrame:
    """比對兩個 snapshot 之間，watchlist/holdings 股票的短期分數異動。"""
    snap_now = sh.load_snapshot(db, as_of)
    snap_prev = sh.load_snapshot(db, prev_as_of)
    if snap_now.empty or snap_prev.empty:
        return pd.DataFrame()
    a = snap_now[snap_now["stock_id"].isin(target_ids)][["stock_id", "stock_name", "short", "mid", "long", "composite", "close"]]
    b = snap_prev[snap_prev["stock_id"].isin(target_ids)][["stock_id", "short", "mid", "composite"]].rename(
        columns={"short": "short_prev", "mid": "mid_prev", "composite": "composite_prev"}
    )
    out = a.merge(b, on="stock_id", how="left")
    out["d_short"] = out["short"] - out["short_prev"]
    out["d_comp"] = out["composite"] - out["composite_prev"]
    return out.sort_values("d_short", ascending=False)


def build_report(db: Database, as_of: str | None = None) -> str:
    """組合報告內文（Markdown）。"""
    wl = wl_mod.load()
    holdings = pf.list_holdings(db)
    holding_ids = {h.stock_id for h in holdings}
    target_ids = set(wl.keys()) | holding_ids

    dates = sh.available_dates(db)
    if not dates:
        return "# 今日無資料\n\n請先執行 market_update。"
    as_of = as_of or dates[0]
    prev_as_of = dates[1] if len(dates) > 1 else None

    snap_today = sh.load_snapshot(db, as_of)

    lines: list[str] = []
    lines.append(f"# 📈 每日早報 — {as_of}")
    lines.append("")

    # 1. 庫存股風險警告
    if holdings:
        lines.append("## 💼 庫存股狀態")
        with db.connect() as conn:
            prices = pd.read_sql_query(
                f"""SELECT p.stock_id, p.close FROM daily_price p
                    JOIN (SELECT stock_id, MAX(date) mx FROM daily_price GROUP BY stock_id) m
                    ON p.stock_id=m.stock_id AND p.date=m.mx
                    WHERE p.stock_id IN ({','.join('?'*len(holding_ids))})""",
                conn, params=list(holding_ids),
            )
        price_map = {r["stock_id"]: float(r["close"]) for _, r in prices.iterrows()}
        score_map = {
            r["stock_id"]: float(r["short"])
            for _, r in snap_today.iterrows()
            if r["stock_id"] in holding_ids and r["short"] is not None and not pd.isna(r["short"])
        }

        for h in holdings:
            latest = price_map.get(h.stock_id, 0)
            if not latest:
                continue
            pnl_pct = h.unrealized_pnl_pct(latest) * 100
            short = score_map.get(h.stock_id)
            signals = enhanced_risk_signals(
                db, h.stock_id, h.avg_cost, h.entry_date, latest,
                float(short) if short is not None else None,
            )
            status = "；".join(signals) if signals else "✅ 目前無風險訊號"
            lines.append(
                f"- **{h.stock_id}** {h.shares/1000:.2f} 張 @ {h.avg_cost:.2f}，"
                f"現價 {latest:.2f} ({pnl_pct:+.1f}%)，短期 {short or '—'} → {status}"
            )
        lines.append("")

    # 2. 分數異動 Top
    if prev_as_of:
        chg = _top_change(db, as_of, prev_as_of, target_ids)
        if not chg.empty:
            up = chg.head(5)
            down = chg.tail(5).iloc[::-1]
            lines.append(f"## 📈 自選/庫存股分數異動（vs {prev_as_of}）")
            if not up.empty:
                lines.append("**上升最多：**")
                for _, r in up.iterrows():
                    if pd.notna(r["d_short"]) and r["d_short"] > 0.5:
                        lines.append(f"- {r['stock_id']} {r['stock_name']}：短 {r['short_prev']:.0f} → {r['short']:.0f} ({r['d_short']:+.1f})")
            if not down.empty:
                lines.append("**下降最多：**")
                for _, r in down.iterrows():
                    if pd.notna(r["d_short"]) and r["d_short"] < -0.5:
                        lines.append(f"- {r['stock_id']} {r['stock_name']}：短 {r['short_prev']:.0f} → {r['short']:.0f} ({r['d_short']:+.1f})")
            lines.append("")

    # 3. 今日雷達 Top 命中（依綜合分數取 Top 3）
    lines.append("## 🎯 今日雷達命中（各策略 Top 3，依綜合分數）")
    try:
        full = radar.score_all(db, include_fundamentals=False)
        if not full.empty:
            for strat_name, strat in radar.STRATEGIES.items():
                hits = strat.filter_fn(full).sort_values("composite", ascending=False).head(3)
                if hits.empty:
                    continue
                names = ", ".join(
                    f"{r['stock_id']}({r['stock_name']} {r['composite']:.0f})"
                    for _, r in hits.iterrows()
                )
                lines.append(f"- **{strat_name}**：{names}")
    except Exception as e:
        lines.append(f"- _（雷達計算失敗：{e}）_")
    lines.append("")

    lines.append("---")
    lines.append("_此報告由 market_update 自動產生_")
    return "\n".join(lines)


def save_report(content: str, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or REPORT_DIR_DEFAULT
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = out_dir / f"{today}.md"
    path.write_text(content, encoding="utf-8")
    # 也寫一份 latest.md 方便固定路徑存取
    (out_dir / "latest.md").write_text(content, encoding="utf-8")
    return path


def generate_daily_report(db: Database, push: bool = False) -> Path:
    content = build_report(db)
    path = save_report(content)
    if push:
        notify(content, title="📈 每日早報")
    return path
