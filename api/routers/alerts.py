"""/api/alerts/* — 預警規則 CRUD + 立即評估。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_db
from app import alerts as alerts_mod
from app.data.db import Database

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class AlertRuleBody(BaseModel):
    stock_id: str
    rule_kind: Literal["price_below", "price_above", "score_drop", "score_rise", "atr_breached"]
    threshold: float | None = None
    note: str | None = None


class AlertRuleRow(BaseModel):
    model_config = {"populate_by_name": True}

    id: int
    stockId: str = Field(..., alias="stock_id")
    ruleKind: str = Field(..., alias="rule_kind")
    threshold: float | None
    note: str | None
    active: bool
    lastTriggeredAt: str | None = Field(None, alias="last_triggered_at")
    createdAt: str = Field(..., alias="created_at")


class AlertHitOut(BaseModel):
    ruleId: int
    stockId: str
    stockName: str
    ruleKind: str
    threshold: float | None
    actualValue: float | None
    message: str


@router.get("/rules", response_model=list[AlertRuleRow])
def list_rules(active_only: bool = False, db: Database = Depends(get_db)):
    """列出所有預警規則。?active_only=true 只看開啟中。"""
    rows = alerts_mod.list_rules(db, active_only=active_only)
    return [
        AlertRuleRow(
            id=r["id"],
            stock_id=r["stock_id"],
            rule_kind=r["rule_kind"],
            threshold=r["threshold"],
            note=r["note"],
            active=bool(r["active"]),
            last_triggered_at=r["last_triggered_at"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


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
    return AlertRuleRow(
        id=rule["id"],
        stock_id=rule["stock_id"],
        rule_kind=rule["rule_kind"],
        threshold=rule["threshold"],
        note=rule["note"],
        active=bool(rule["active"]),
        last_triggered_at=rule["last_triggered_at"],
        created_at=rule["created_at"],
    )


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Database = Depends(get_db)) -> None:
    alerts_mod.delete_rule(db, rule_id)


@router.patch("/rules/{rule_id}/active", response_model=AlertRuleRow)
def toggle_rule(rule_id: int, active: bool, db: Database = Depends(get_db)):
    alerts_mod.set_active(db, rule_id, active)
    rule = next((r for r in alerts_mod.list_rules(db) if r["id"] == rule_id), None)
    if not rule:
        raise HTTPException(status_code=404, detail="rule not found")
    return AlertRuleRow(
        id=rule["id"],
        stock_id=rule["stock_id"],
        rule_kind=rule["rule_kind"],
        threshold=rule["threshold"],
        note=rule["note"],
        active=bool(rule["active"]),
        last_triggered_at=rule["last_triggered_at"],
        created_at=rule["created_at"],
    )


@router.post("/check", response_model=list[AlertHitOut])
def check_now(push: bool = False, db: Database = Depends(get_db)):
    """立即評估所有 active 規則。?push=true 真的推 Discord；false 只回傳命中清單（給 UI 預覽）。"""
    hits = alerts_mod.check_alerts(db, push=push)
    return [
        AlertHitOut(
            ruleId=h.rule_id,
            stockId=h.stock_id,
            stockName=h.stock_name,
            ruleKind=h.rule_kind,
            threshold=h.threshold,
            actualValue=h.actual_value,
            message=h.message,
        )
        for h in hits
    ]
