"""預警引擎：根據 alert_rule 表評估當下市場狀態，觸發時推 Discord（沿用 notifier 抽象）。

設計：
- 4 種 rule_kind:
    price_below / price_above   → 比對 daily_price.MAX(date) 收盤
    score_drop                  → signal_history 短期分數 7 日內跌幅 ≥ threshold
    atr_breached                → 跟隨持股 enhanced_risk_signals 的 ATR 跌破訊號
- 命中後寫 last_triggered_at（同一條規則不會每天重複推；想再推先 reset 或 reactivate）
- 入口 `check_alerts(db)` 適合放在 daily-update 結尾、或 cron 每 N 小時跑一次

Schema in db.py: alert_rule(id, stock_id, rule_kind, threshold, note, active, last_triggered_at, created_at)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from app.data.clock import taipei_now, taipei_today
from app.data.db import Database
from app.notifier import notify

logger = logging.getLogger(__name__)

ALERT_KINDS = ("price_below", "price_above", "score_drop", "score_rise", "atr_breached")

# 觸發後 24 小時內不重複推同一條規則（避免價格在閾值附近震盪刷屏）。
RETRIGGER_COOLDOWN_HOURS = 24


@dataclass
class AlertHit:
    rule_id: int
    stock_id: str
    stock_name: str
    rule_kind: str
    threshold: float | None
    actual_value: float | None
    message: str


def _stock_name(db: Database, stock_id: str) -> str:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT stock_name FROM stock_info WHERE stock_id=?", (stock_id,),
        ).fetchone()
    return (row["stock_name"] if row else None) or stock_id


def _can_retrigger(last: str | None) -> bool:
    """同一條規則 24 小時內不重複觸發。"""
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    cooldown = timedelta(hours=RETRIGGER_COOLDOWN_HOURS)
    return taipei_now().replace(tzinfo=None) - last_dt > cooldown


def list_rules(db: Database, *, active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM alert_rule"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY id DESC"
    with db.connect() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def create_rule(
    db: Database,
    stock_id: str,
    rule_kind: str,
    threshold: float | None = None,
    note: str | None = None,
) -> int:
    if rule_kind not in ALERT_KINDS:
        raise ValueError(f"unknown rule_kind: {rule_kind}; expected one of {ALERT_KINDS}")
    if rule_kind in ("price_below", "price_above") and (threshold is None or threshold <= 0):
        raise ValueError("price 規則需要 threshold > 0")
    if rule_kind in ("score_drop", "score_rise") and (threshold is None or threshold <= 0):
        raise ValueError("score 規則需要 threshold > 0（分數差分上限，例：10 分）")
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO alert_rule (stock_id, rule_kind, threshold, note) VALUES (?, ?, ?, ?)",
            (stock_id, rule_kind, threshold, note),
        )
        conn.commit()
        return cur.lastrowid


def delete_rule(db: Database, rule_id: int) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM alert_rule WHERE id=?", (rule_id,))
        conn.commit()


def set_active(db: Database, rule_id: int, active: bool) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE alert_rule SET active=? WHERE id=?", (1 if active else 0, rule_id))
        conn.commit()


def _check_price_rule(conn, rule: dict) -> AlertHit | None:
    sid, kind, threshold = rule["stock_id"], rule["rule_kind"], rule["threshold"]
    row = conn.execute(
        "SELECT close FROM daily_price WHERE stock_id=? ORDER BY date DESC LIMIT 1",
        (sid,),
    ).fetchone()
    if not row or row["close"] is None:
        return None
    close = float(row["close"])
    hit = (
        (kind == "price_below" and close <= threshold)
        or (kind == "price_above" and close >= threshold)
    )
    if not hit:
        return None
    direction = "跌破" if kind == "price_below" else "突破"
    return AlertHit(
        rule_id=rule["id"], stock_id=sid, stock_name="",  # 由 caller 補
        rule_kind=kind, threshold=threshold, actual_value=close,
        message=f"{direction}價格 {threshold:.2f}（現價 {close:.2f}）",
    )


def _check_score_rule(conn, rule: dict) -> AlertHit | None:
    """score_drop: 短期分數 7 日內跌幅 ≥ threshold；score_rise 反向。"""
    sid, kind, threshold = rule["stock_id"], rule["rule_kind"], rule["threshold"]
    rows = conn.execute(
        "SELECT as_of, short FROM signal_history WHERE stock_id=? ORDER BY as_of DESC LIMIT 8",
        (sid,),
    ).fetchall()
    if len(rows) < 2:
        return None
    latest = rows[0]
    prev = rows[-1]  # 7 日前（或最舊一筆）
    if latest["short"] is None or prev["short"] is None:
        return None
    delta = float(latest["short"]) - float(prev["short"])
    hit = (
        (kind == "score_drop" and -delta >= threshold)
        or (kind == "score_rise" and delta >= threshold)
    )
    if not hit:
        return None
    direction = "下跌" if kind == "score_drop" else "上升"
    return AlertHit(
        rule_id=rule["id"], stock_id=sid, stock_name="",
        rule_kind=kind, threshold=threshold, actual_value=delta,
        message=f"短期分數 {direction} {abs(delta):.1f} 分（{prev['short']:.0f} → {latest['short']:.0f}，{prev['as_of']} → {latest['as_of']}）",
    )


def _check_atr_rule(db: Database, conn, rule: dict) -> AlertHit | None:
    """atr_breached: 用 enhanced_risk_signals 判 trailing-ATR 跌破（僅對持股有效）。"""
    sid = rule["stock_id"]
    holding = conn.execute(
        "SELECT shares, avg_cost, entry_date FROM holdings WHERE stock_id=? AND shares > 0",
        (sid,),
    ).fetchone()
    if not holding:
        return None  # 沒持股無 entry_date，無法算 trailing；fixed mode 由 close > avg_cost 邏輯接手
    row = conn.execute(
        "SELECT close FROM daily_price WHERE stock_id=? ORDER BY date DESC LIMIT 1",
        (sid,),
    ).fetchone()
    if not row:
        return None
    close = float(row["close"])
    from app.risk import enhanced_risk_signals
    signals = enhanced_risk_signals(
        db, sid, float(holding["avg_cost"]), holding["entry_date"], close, 0.0,
    )
    atr_signals = [s for s in signals if "跌破 ATR" in s]
    if not atr_signals:
        return None
    return AlertHit(
        rule_id=rule["id"], stock_id=sid, stock_name="",
        rule_kind="atr_breached", threshold=None, actual_value=close,
        message=f"持股觸發 ATR 停損：" + atr_signals[0][:200],
    )


def check_alerts(db: Database, *, push: bool = True) -> list[AlertHit]:
    """掃描所有 active 規則 → 回傳命中清單，並可選 push 通知。

    呼叫端：daily-update.bat 結尾呼叫一次（資料更新完才檢查）；UI `/api/alerts/check` endpoint
    給「測試規則」按鈕用（push=False 不真的推）。
    """
    today = taipei_today().isoformat()
    hits: list[AlertHit] = []
    with db.connect() as conn:
        rules = conn.execute(
            "SELECT * FROM alert_rule WHERE active = 1"
        ).fetchall()
        for r in rules:
            r_dict = dict(r)
            if not _can_retrigger(r_dict.get("last_triggered_at")):
                continue
            try:
                if r_dict["rule_kind"] in ("price_below", "price_above"):
                    hit = _check_price_rule(conn, r_dict)
                elif r_dict["rule_kind"] in ("score_drop", "score_rise"):
                    hit = _check_score_rule(conn, r_dict)
                elif r_dict["rule_kind"] == "atr_breached":
                    hit = _check_atr_rule(db, conn, r_dict)
                else:
                    hit = None
            except Exception as e:
                logger.warning("alert rule %s 評估失敗: %s", r_dict["id"], e)
                continue
            if hit is None:
                continue
            hit.stock_name = _stock_name(db, hit.stock_id)
            hits.append(hit)
        # 標記已觸發
        if hits:
            now_iso = taipei_now().replace(tzinfo=None).isoformat(timespec="seconds")
            conn.executemany(
                "UPDATE alert_rule SET last_triggered_at=? WHERE id=?",
                [(now_iso, h.rule_id) for h in hits],
            )
            conn.commit()

    if push and hits:
        body_lines = [
            f"• [{h.stock_id}] {h.stock_name}：{h.message}"
            for h in hits
        ]
        notify(
            "\n".join(body_lines),
            title=f"📡 {today} 預警觸發 {len(hits)} 條",
        )
    return hits
