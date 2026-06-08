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
from app.scoring.snapshot_freshness import ensure_fresh
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


def _empty_parts() -> tuple[dict[str, float | None], dict[str, float | None], dict[str, float | None]]:
    return (
        {k: None for k in SHORT_TERM_WEIGHTS},
        {k: None for k in MID_TERM_WEIGHTS},
        {k: None for k in LONG_TERM_WEIGHTS},
    )


def _breakdown_from_snapshot(
    db: Database,
    watch_items: list[tuple[str, str]],
) -> tuple[list[StockBreakdown], set[str]]:
    """優先從 signal_history + factor_parts 載入，避免逐檔 score_stock。"""
    if not watch_items:
        return [], set()

    sids = [sid for sid, _ in watch_items]
    with db.connect() as conn:
        latest_row = conn.execute("SELECT MAX(as_of) AS m FROM signal_history").fetchone()
        as_of = latest_row["m"] if latest_row and latest_row["m"] else None
        if as_of is None:
            return [], set(sids)

        ph = ",".join("?" for _ in sids)
        snapshot_rows = conn.execute(
            f"SELECT stock_id, stock_name, close, short, mid, long, composite "
            f"FROM signal_history WHERE as_of=? AND stock_id IN ({ph})",
            (as_of, *sids),
        ).fetchall()
        part_rows = conn.execute(
            f"SELECT stock_id, horizon, factor, score "
            f"FROM signal_history_factor_parts WHERE as_of=? AND stock_id IN ({ph})",
            (as_of, *sids),
        ).fetchall()

    by_sid = {r["stock_id"]: r for r in snapshot_rows}
    parts_by_sid: dict[str, dict[str, dict[str, float | None]]] = {}
    for r in part_rows:
        sid = r["stock_id"]
        horizon = str(r["horizon"] or "").lower()
        factor = str(r["factor"] or "")
        if not sid or not factor:
            continue
        if horizon not in ("short", "mid", "long"):
            continue
        if sid not in parts_by_sid:
            short_parts, mid_parts, long_parts = _empty_parts()
            parts_by_sid[sid] = {
                "short": short_parts,
                "mid": mid_parts,
                "long": long_parts,
            }
        target = parts_by_sid[sid][horizon]
        if factor in target:
            target[factor] = _sf(r["score"])

    stocks: list[StockBreakdown] = []
    missing: set[str] = set()
    for sid, watch_name in watch_items:
        snap = by_sid.get(sid)
        parts = parts_by_sid.get(sid)
        if snap is None or parts is None:
            missing.add(sid)
            continue
        stocks.append(StockBreakdown(
            stock_id=sid,
            stock_name=(snap["stock_name"] or watch_name or sid),
            close=_sf(snap["close"]),
            short_parts=parts["short"],
            mid_parts=parts["mid"],
            long_parts=parts["long"],
            short_default=_sf(snap["short"]),
            mid_default=_sf(snap["mid"]),
            long_default=_sf(snap["long"]),
            composite_default=_sf(snap["composite"]),
        ))
    return stocks, missing


@router.get("/breakdown", response_model=TunerBreakdownResponse)
def breakdown(db: Database = Depends(get_db)) -> TunerBreakdownResponse:
    wl = wl_mod.load()
    watch_items = list(wl.items())
    stocks: list[StockBreakdown] = []
    missing_sids: set[str] = set()

    # 先確保快照新鮮，再吃快照資料，只有缺漏才 fallback 即時計分。
    ensure_fresh(db)
    snapshot_stocks, missing_sids = _breakdown_from_snapshot(db, watch_items)
    stocks.extend(snapshot_stocks)
    if missing_sids:
        for sid, name in watch_items:
            if sid not in missing_sids:
                continue
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

    # 依 watchlist 原順序回傳，維持 UI 可預期排序。
    order = {sid: i for i, (sid, _) in enumerate(watch_items)}
    stocks.sort(key=lambda s: order.get(s.stock_id, 10**9))

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
