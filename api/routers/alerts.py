"""/api/alerts/* — 預警規則 CRUD + 立即評估。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_db
from api.schemas.common import CamelModel
from app import alerts as alerts_mod
from app.data.db import Database

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class AlertRuleBody(BaseModel):
    stock_id: str
    rule_kind: Literal["price_below", "price_above", "score_drop", "score_rise", "atr_breached"]
    threshold: float | None = None
    note: str | None = None


class AlertRuleRow(CamelModel):
    """欄位 snake_case，序列化成 camelCase（CamelModel 規則）。"""

    id: int
    stock_id: str
    rule_kind: str
    threshold: float | None
    note: str | None
    active: bool
    last_triggered_at: str | None = None
    created_at: str
    # 即時評估（給 UI「現值 / 離觸發距離」顯示用）；資料不足時為 None
    actual_value: float | None = None
    triggered: bool = False


class AlertHitOut(CamelModel):
    rule_id: int
    stock_id: str
    stock_name: str
    rule_kind: str
    threshold: float | None
    actual_value: float | None
    message: str


def _to_row(db: Database, r: dict, *, evaluate: bool = True) -> AlertRuleRow:
    """共用 row builder。evaluate=True 時順便算 actualValue/triggered（給 list 用）；
    create/toggle 等寫入流程也呼叫 evaluate=True，保持回應結構一致。"""
    actual: float | None = None
    triggered = False
    if evaluate and bool(r["active"]):
        actual, triggered = alerts_mod.current_state(db, r)
    return AlertRuleRow(
        id=r["id"],
        stock_id=r["stock_id"],
        rule_kind=r["rule_kind"],
        threshold=r["threshold"],
        note=r["note"],
        active=bool(r["active"]),
        last_triggered_at=r["last_triggered_at"],
        created_at=r["created_at"],
        actual_value=actual,
        triggered=triggered,
    )


@router.get("/rules", response_model=list[AlertRuleRow])
def list_rules(active_only: bool = False, db: Database = Depends(get_db)):
    """列出所有預警規則。?active_only=true 只看開啟中。

    每條 active 規則會即時評估 actualValue/triggered，供 UI 顯示「現值 / 離觸發距離」。
    inactive 規則不評估（沒意義也避免做白工）。
    """
    rows = alerts_mod.list_rules(db, active_only=active_only)
    return [_to_row(db, r) for r in rows]


@router.post("/rules", response_model=AlertRuleRow, status_code=201)
def create_rule(body: AlertRuleBody, db: Database = Depends(get_db)):
    try:
        rid = alerts_mod.create_rule(
            db, body.stock_id, body.rule_kind, body.threshold, body.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    rule = next((r for r in alerts_mod.list_rules(db) if r["id"] == rid), None)
    if not rule:
        raise HTTPException(status_code=500, detail="rule created but not found in DB")
    return _to_row(db, rule)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Database = Depends(get_db)) -> None:
    alerts_mod.delete_rule(db, rule_id)


@router.patch("/rules/{rule_id}/active", response_model=AlertRuleRow)
def toggle_rule(rule_id: int, active: bool, db: Database = Depends(get_db)):
    alerts_mod.set_active(db, rule_id, active)
    rule = next((r for r in alerts_mod.list_rules(db) if r["id"] == rule_id), None)
    if not rule:
        raise HTTPException(status_code=404, detail="rule not found")
    return _to_row(db, rule)


@router.post("/check", response_model=list[AlertHitOut])
def check_now(push: bool = False, db: Database = Depends(get_db)):
    """立即評估所有 active 規則。?push=true 真的推 Discord；false 只回傳命中清單（給 UI 預覽）。"""
    hits = alerts_mod.check_alerts(db, push=push)
    return [
        AlertHitOut(
            rule_id=h.rule_id,
            stock_id=h.stock_id,
            stock_name=h.stock_name,
            rule_kind=h.rule_kind,
            threshold=h.threshold,
            actual_value=h.actual_value,
            message=h.message,
        )
        for h in hits
    ]
