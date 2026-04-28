"""權重 preset 的持久層。

存放結構：`user_weight_preset(name PK, description, weights_json, created_at, updated_at)`
weights_json 內是 `{"short": {key: w, ...}, "mid": {...}, "long": {...}}`。

設計重點：
- 只允許值合法（finite & 0~1）的權重存入；wholly invalid 的整組會丟 ValueError。
- 不檢查鍵集合與目前 SHORT/MID/LONG_TERM_WEIGHTS 完全一致，因為使用者升級程式時，
  舊的 preset 仍應可讀（缺鍵 → 由前端 / engine 重新歸一化）。
"""
from __future__ import annotations

import json
from datetime import datetime
from math import isfinite

from app.data.clock import taipei_now
from typing import Any, Iterable

from app.data.db import Database
from app.scoring.rubric import BUILTIN_WEIGHT_PRESETS

VALID_DIMS = ("short", "mid", "long")


def _validate_weights(weights: Any) -> dict[str, dict[str, float]]:
    """確保是 dict[dim -> dict[key -> finite float in 0..1]]。回傳乾淨拷貝。"""
    if not isinstance(weights, dict):
        raise ValueError("weights 必須是 object")
    out: dict[str, dict[str, float]] = {}
    for dim in VALID_DIMS:
        sub = weights.get(dim)
        if not isinstance(sub, dict) or not sub:
            raise ValueError(f"weights.{dim} 必須是非空 object")
        clean: dict[str, float] = {}
        for k, v in sub.items():
            if not isinstance(k, str):
                raise ValueError(f"weights.{dim} key 必須是字串")
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"weights.{dim}.{k} 必須是數字")
            if not isfinite(f) or f < 0 or f > 1:
                raise ValueError(f"weights.{dim}.{k} 必須在 0~1 範圍且為有限數")
            clean[k] = f
        if not any(v > 0 for v in clean.values()):
            raise ValueError(f"weights.{dim} 至少要有一個 > 0 的權重")
        out[dim] = clean
    return out


def list_presets(db: Database) -> list[dict]:
    """回傳所有使用者 preset；按更新時間倒序。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT name, description, weights_json, created_at, updated_at "
            "FROM user_weight_preset ORDER BY updated_at DESC"
        ).fetchall()
    out = []
    for r in rows:
        try:
            w = json.loads(r["weights_json"])
        except json.JSONDecodeError:
            continue
        out.append({
            "name": r["name"],
            "description": r["description"] or "",
            "weights": w,
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        })
    return out


def get_preset(db: Database, name: str) -> dict | None:
    with db.connect() as conn:
        r = conn.execute(
            "SELECT name, description, weights_json, created_at, updated_at "
            "FROM user_weight_preset WHERE name = ?",
            (name,),
        ).fetchone()
    if r is None:
        return None
    try:
        w = json.loads(r["weights_json"])
    except json.JSONDecodeError:
        return None
    return {
        "name": r["name"],
        "description": r["description"] or "",
        "weights": w,
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def upsert_preset(
    db: Database, name: str, weights: Any, description: str = ""
) -> dict:
    """新增或覆蓋一個 preset。回傳寫入後的物件。"""
    name = name.strip()
    if not name:
        raise ValueError("preset 名稱不可空白")
    if len(name) > 60:
        raise ValueError("preset 名稱長度上限 60")
    if name in BUILTIN_WEIGHT_PRESETS:
        raise ValueError(f"'{name}' 為內建 preset 名稱，請換一個")
    clean = _validate_weights(weights)
    payload = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    now = taipei_now().replace(tzinfo=None).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO user_weight_preset (name, description, weights_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                description = excluded.description,
                weights_json = excluded.weights_json,
                updated_at = excluded.updated_at
            """,
            (name, description.strip(), payload, now, now),
        )
        conn.commit()
    saved = get_preset(db, name)
    assert saved is not None
    return saved


def delete_preset(db: Database, name: str) -> bool:
    """回傳是否實際刪除了一筆。"""
    if name in BUILTIN_WEIGHT_PRESETS:
        raise ValueError(f"'{name}' 為內建 preset，無法刪除")
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM user_weight_preset WHERE name = ?", (name,))
        conn.commit()
        return cur.rowcount > 0


def builtin_presets() -> list[dict]:
    """把 BUILTIN_WEIGHT_PRESETS 轉成跟使用者 preset 同形狀的 list。"""
    out = []
    for key, p in BUILTIN_WEIGHT_PRESETS.items():
        out.append({
            "name": key,
            "label": p["label"],
            "description": p["description"],
            "weights": p["weights"],
        })
    return out
