"""盤後一鍵檢查：列出每檔持股的 ATR 停損 / 結構警戒線 / 鎖利狀態，並產出隔日動作清單。

用法：
    python -m scripts.holdings_eod_check
    python -m scripts.holdings_eod_check --capital 760000   # 指定總資金（用於部位佔比）

輸出：
- 每檔持股一個區塊，含成本/現價/浮動損益、ATR、三條停損線、觸發狀態
- 結尾「隔日動作清單」總表：✓ 持有 / ⚠️ 減半 / 🚨 全出
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import pandas as pd

# Windows cp950 console can't print emoji / box-drawing chars; force UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402
from app.data.db import Database  # noqa: E402
from app.risk import (  # noqa: E402
    compute_atr,
    trailing_atr_stop,
    trailing_atr_take_profit,
)


def fetch_price_df(db: Database, stock_id: str) -> pd.DataFrame:
    sql = """
        SELECT date, open, high, low, close, volume
        FROM daily_price WHERE stock_id=? ORDER BY date
    """
    with db.connect() as conn:
        return pd.read_sql_query(sql, conn, params=(stock_id,))


def fetch_holdings(db: Database) -> list[dict]:
    sql = """
        SELECT h.stock_id, h.shares, h.avg_cost, h.entry_date, s.stock_name
        FROM holdings h LEFT JOIN stock_info s ON s.stock_id = h.stock_id
        ORDER BY h.shares * h.avg_cost DESC
    """
    with db.connect() as conn:
        rows = conn.execute(sql).fetchall()
    return [
        {
            "stock_id": r[0],
            "shares": int(r[1]),
            "avg_cost": float(r[2]),
            "entry_date": r[3],
            "stock_name": r[4] or r[0],
        }
        for r in rows
    ]


def structural_warning_line(df: pd.DataFrame) -> dict:
    """結構警戒線 = max(5 日均線, 近 10 日最低收盤後第二低)。
    取兩者較高者，提供「動能轉弱」的早期訊號。"""
    closes = df["close"].astype(float)
    ma5 = closes.tail(5).mean()
    # 近 10 日次低收盤（避免單日噪音）
    last10 = sorted(closes.tail(10).tolist())
    second_low = last10[1] if len(last10) >= 2 else last10[0]
    return {
        "ma5": round(ma5, 2),
        "platform_floor": round(second_low, 2),
        "warning_line": round(max(ma5, second_low), 2),
    }


def status_flag(latest: float, atr_stop: float, warn: float) -> tuple[str, str]:
    if latest < atr_stop:
        return "🚨 停損", "隔日 9:15-9:45 限價全出，10:00 後改市價"
    if latest < warn:
        return "⚠️ 警戒", "隔日 9:15-9:45 限價賣半倉（無法切半 → 全出）"
    return "✅ 持有", "繼續觀察、不動作"


def fmt_money(v: float) -> str:
    return f"{v:>+12,.0f}"


def render_holding(db: Database, h: dict, capital: float | None) -> dict:
    df = fetch_price_df(db, h["stock_id"])
    if df.empty or len(df) < 20:
        print(f"⚠️  {h['stock_id']} {h['stock_name']}：資料不足，略過")
        return {}

    latest_row = df.iloc[-1]
    latest_close = float(latest_row["close"])
    latest_date = str(latest_row["date"])
    atr = float(compute_atr(df, 14).iloc[-1])

    trail = trailing_atr_stop(df, h["entry_date"], multiplier=2.0) or {}
    tp = (
        trailing_atr_take_profit(
            df, h["entry_date"], h["avg_cost"], multiplier=3.0
        )
        or {}
    )
    warn = structural_warning_line(df)

    market_value = h["shares"] * latest_close
    cost_value = h["shares"] * h["avg_cost"]
    pnl = market_value - cost_value
    pnl_pct = (latest_close - h["avg_cost"]) / h["avg_cost"] * 100
    pos_pct = (market_value / capital * 100) if capital else None

    atr_stop = trail.get("stop_price")
    flag, action = status_flag(
        latest_close, atr_stop or -1, warn["warning_line"]
    )
    # Chandelier 鎖利：armed 後若觸發，flag 升級為停損
    if tp.get("triggered"):
        flag = "🎯 鎖利觸發"
        action = "隔日 9:15-9:45 限價全出（已觸動態停利）"

    print()
    print("=" * 72)
    print(
        f"【{h['stock_id']} {h['stock_name']}】 {flag}  "
        f"({h['shares']:,} 股, 進場 {h['entry_date']})"
    )
    print("=" * 72)
    print(
        f"  收盤 {latest_date}: {latest_close:>7.2f}  "
        f"成本 {h['avg_cost']:>7.2f}  ATR(14) {atr:.3f}"
    )
    print(
        f"  浮動損益: {fmt_money(pnl)}  ({pnl_pct:+.2f}%)  "
        f"市值 {market_value:>10,.0f}"
        + (f"  部位 {pos_pct:.1f}%" if pos_pct is not None else "")
    )
    print()
    print("  📊 三條防線（與現價距離）")
    print(
        f"    ⚠️  結構警戒線:  {warn['warning_line']:>7.2f}  "
        f"({(warn['warning_line']/latest_close-1)*100:+.2f}%)  "
        f"[5MA={warn['ma5']:.2f}, 10日次低={warn['platform_floor']:.2f}]"
    )
    if atr_stop:
        print(
            f"    🚨 ATR 停損線:   {atr_stop:>7.2f}  "
            f"({(atr_stop/latest_close-1)*100:+.2f}%)  "
            f"[peak={trail.get('peak_since_entry'):.2f} − 2×ATR]"
        )
    if tp:
        armed = "🟢 已啟動" if tp.get("armed") else "⚪ 未啟動"
        print(
            f"    🎯 鎖利線(3ATR):  {tp.get('take_profit_price'):>7.2f}  "
            f"({(tp.get('take_profit_price')/latest_close-1)*100:+.2f}%)  "
            f"{armed} [浮盈 {tp.get('unrealized_pnl_pct')*100:.1f}% / "
            f"持有 {tp.get('days_held')} 日]"
        )
    print()
    print(f"  📋 隔日動作: {action}")

    return {
        "stock_id": h["stock_id"],
        "stock_name": h["stock_name"],
        "shares": h["shares"],
        "latest_close": latest_close,
        "warn_line": warn["warning_line"],
        "atr_stop": atr_stop,
        "tp_line": tp.get("take_profit_price"),
        "tp_armed": tp.get("armed", False),
        "tp_triggered": tp.get("triggered", False),
        "flag": flag,
        "action": action,
        "pnl_pct": pnl_pct,
    }


def render_summary(rows: list[dict]) -> None:
    if not rows:
        return
    print()
    print("=" * 72)
    print("📋 隔日動作彙整")
    print("=" * 72)
    # 排序：停損 > 鎖利 > 警戒 > 持有
    order = {"🚨": 0, "🎯": 1, "⚠️": 2, "✅": 3}
    rows.sort(key=lambda r: order.get(r["flag"][:1], 9))

    todo_count = 0
    for r in rows:
        if r["flag"].startswith(("🚨", "🎯", "⚠️")):
            todo_count += 1
        marker = r["flag"][:2]
        print(
            f"  {marker} {r['stock_id']} {r['stock_name']:6s}  "
            f"收 {r['latest_close']:>7.2f}  "
            f"({r['pnl_pct']:+.2f}%)  → {r['action']}"
        )
    print()
    if todo_count == 0:
        print("  🌟 全數通過，明日無需執行任何動作。")
    else:
        print(f"  ⏰ 明日需執行 {todo_count} 檔，建議 9:15-9:45 完成。")
    print()


def main():
    ap = argparse.ArgumentParser(description="盤後一鍵檢查持股 ATR 停損")
    ap.add_argument("--capital", type=float, default=None,
                    help="總資金（用於計算部位佔比，可選）")
    args = ap.parse_args()

    cfg = Config.load()
    db = Database(cfg.database.path)
    holdings = fetch_holdings(db)
    if not holdings:
        print("⚠️  目前沒有持股紀錄。")
        return

    print()
    print("┌" + "─" * 70 + "┐")
    print(f"│  📊 持股盤後檢查 ({len(holdings)} 檔)" + " " * 38 + "│")
    if args.capital:
        print(f"│  💰 總資金 NT$ {args.capital:,.0f}" + " " * 35 + "│")
    print("└" + "─" * 70 + "┘")

    rows = [render_holding(db, h, args.capital) for h in holdings]
    render_summary([r for r in rows if r])


if __name__ == "__main__":
    main()
