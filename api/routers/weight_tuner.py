"""/api/weight-tuner/* — 權重調優。

- /breakdown：吐出自選股每檔的短/中/長期「子項分數」+ 預設分數，前端 client-side 即時重算。
- /presets：CRUD 使用者命名的 preset；/presets/builtin 列出內建主題式 preset；
  /presets/visible-keys 回傳「新手模式」每維度顯示的子指標白名單。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from api.common import safe_float as _sf
from api.deps import get_db
from api.schemas.common import CamelModel
from app import watchlist as wl_mod
from app.data.db import Database
from app.scoring import preset as preset_mod
from app.scoring.engine import score_stock
from app.scoring.rubric import (
    BEGINNER_VISIBLE_KEYS,
    LONG_TERM_WEIGHTS,
    MID_TERM_WEIGHTS,
    SHORT_TERM_WEIGHTS,
)

router = APIRouter(prefix="/api/weight-tuner", tags=["weight-tuner"])


class DefaultWeights(CamelModel):
    short: dict[str, float]
    mid: dict[str, float]
    long: dict[str, float]


class StockBreakdown(CamelModel):
    stock_id: str
    stock_name: str
    close: float | None = None
    short_parts: dict[str, float | None]
    mid_parts: dict[str, float | None]
    long_parts: dict[str, float | None]
    short_default: float | None = None
    mid_default: float | None = None
    long_default: float | None = None
    composite_default: float | None = None


class TunerBreakdownResponse(CamelModel):
    stocks: list[StockBreakdown]
    default_weights: DefaultWeights


@router.get("/breakdown", response_model=TunerBreakdownResponse)
def breakdown(db: Database = Depends(get_db)) -> TunerBreakdownResponse:
    wl = wl_mod.load()
    watch_items = list(wl.items())
    stocks: list[StockBreakdown] = []

    # 自選清單通常只有數檔～數十檔，直接逐檔即時計分（score_stock ~13ms/檔），取得短/中/長期
    # 子因子分數供前端 client-side 即時重算權重。依 watch_items 原順序 append，維持 UI 排序。
    # （2026-06：原本優先讀 signal_history_factor_parts 快取拆解，該表已整張移除 → 統一走即時計分。）
    for sid, name in watch_items:
        s = score_stock(db, sid, name)
        if s is None:
            continue
        stocks.append(StockBreakdown(
            stock_id=s.stock_id,
            stock_name=s.stock_name,
            close=_sf(s.close),
            short_parts={k: _sf(v) for k, v in s.short.parts.items()},
            mid_parts={k: _sf(v) for k, v in s.mid.parts.items()},
            long_parts={k: _sf(v) for k, v in s.long.parts.items()},
            short_default=_sf(s.short.total),
            mid_default=_sf(s.mid.total),
            long_default=_sf(s.long.total),
            composite_default=_sf(s.signals.get("composite_score")),
        ))

    return TunerBreakdownResponse(
        stocks=stocks,
        default_weights=DefaultWeights(
            short={k: float(v) for k, v in SHORT_TERM_WEIGHTS.items()},
            mid={k: float(v) for k, v in MID_TERM_WEIGHTS.items()},
            long={k: float(v) for k, v in LONG_TERM_WEIGHTS.items()},
        ),
    )


# ----------------------------------------------------------------------
# Presets：內建主題 + 使用者自存
# ----------------------------------------------------------------------
class WeightSet(CamelModel):
    short: dict[str, float]
    mid: dict[str, float]
    long: dict[str, float]


class BuiltinPreset(CamelModel):
    name: str
    label: str
    description: str
    weights: WeightSet


class UserPreset(CamelModel):
    name: str
    description: str
    weights: WeightSet
    created_at: str | None = None
    updated_at: str | None = None


class PresetListResponse(CamelModel):
    builtin: list[BuiltinPreset]
    user: list[UserPreset]


class PresetUpsertRequest(CamelModel):
    name: str
    description: str = ""
    weights: WeightSet


class VisibleKeysResponse(CamelModel):
    short: list[str]
    mid: list[str]
    long: list[str]


@router.get("/presets", response_model=PresetListResponse)
def list_presets(db: Database = Depends(get_db)) -> PresetListResponse:
    return PresetListResponse(
        builtin=[BuiltinPreset(**p) for p in preset_mod.builtin_presets()],
        user=[UserPreset(**p) for p in preset_mod.list_presets(db)],
    )


@router.post("/presets", response_model=UserPreset, status_code=201)
def upsert_preset(payload: PresetUpsertRequest, db: Database = Depends(get_db)) -> UserPreset:
    try:
        saved = preset_mod.upsert_preset(
            db, payload.name, payload.weights.model_dump(), payload.description
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return UserPreset(**saved)


@router.get("/presets/visible-keys", response_model=VisibleKeysResponse)
def visible_keys() -> VisibleKeysResponse:
    return VisibleKeysResponse(**BEGINNER_VISIBLE_KEYS)


@router.get("/presets/{name}", response_model=UserPreset)
def get_preset(name: str, db: Database = Depends(get_db)) -> UserPreset:
    """取單一 user preset 詳情（含 weights）。內建 preset 不在此回傳，請改打 `/presets`."""
    p = preset_mod.get_preset(db, name)
    if p is None:
        raise HTTPException(status_code=404, detail=f"找不到 preset '{name}'")
    return UserPreset(**p)


@router.delete("/presets/{name}", status_code=204)
def delete_preset(name: str, db: Database = Depends(get_db)) -> Response:
    """冪等刪除：刪不存在的 user preset 也回 204。內建 preset 不可刪（delete_preset 丟
    ValueError → 422）。"""
    try:
        preset_mod.delete_preset(db, name)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return Response(status_code=204)
