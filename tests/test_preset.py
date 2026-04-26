"""權重 preset 持久層測試。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.data.db import Database
from app.scoring import preset as preset_mod


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "preset.db")


VALID_W = {
    "short": {"ma_alignment": 0.5, "kd": 0.5},
    "mid": {"trend": 0.5, "eps_growth": 0.5},
    "long": {"roe": 0.5, "dividend": 0.5},
}


def test_list_empty(db: Database) -> None:
    assert preset_mod.list_presets(db) == []


def test_upsert_then_get(db: Database) -> None:
    saved = preset_mod.upsert_preset(db, "策略A", VALID_W, "說明")
    assert saved["name"] == "策略A"
    assert saved["description"] == "說明"
    assert saved["weights"]["short"]["ma_alignment"] == 0.5

    got = preset_mod.get_preset(db, "策略A")
    assert got is not None
    assert got["weights"] == VALID_W


def test_upsert_overwrite(db: Database) -> None:
    preset_mod.upsert_preset(db, "X", VALID_W, "v1")
    new_w = {**VALID_W, "short": {"ma_alignment": 1.0}}
    preset_mod.upsert_preset(db, "X", new_w, "v2")
    got = preset_mod.get_preset(db, "X")
    assert got is not None
    assert got["description"] == "v2"
    assert got["weights"]["short"] == {"ma_alignment": 1.0}


def test_delete(db: Database) -> None:
    preset_mod.upsert_preset(db, "Y", VALID_W)
    assert preset_mod.delete_preset(db, "Y") is True
    assert preset_mod.delete_preset(db, "Y") is False
    assert preset_mod.get_preset(db, "Y") is None


def test_reject_builtin_name(db: Database) -> None:
    with pytest.raises(ValueError, match="內建"):
        preset_mod.upsert_preset(db, "default", VALID_W)
    with pytest.raises(ValueError, match="內建"):
        preset_mod.delete_preset(db, "conservative")


def test_reject_blank_name(db: Database) -> None:
    with pytest.raises(ValueError, match="名稱不可空白"):
        preset_mod.upsert_preset(db, "   ", VALID_W)


def test_reject_too_long_name(db: Database) -> None:
    with pytest.raises(ValueError, match="長度上限"):
        preset_mod.upsert_preset(db, "a" * 61, VALID_W)


def test_reject_invalid_weight_value(db: Database) -> None:
    bad = {**VALID_W, "short": {"ma_alignment": 1.5}}
    with pytest.raises(ValueError, match="0~1"):
        preset_mod.upsert_preset(db, "bad", bad)


def test_reject_negative_weight(db: Database) -> None:
    bad = {**VALID_W, "short": {"ma_alignment": -0.1}}
    with pytest.raises(ValueError, match="0~1"):
        preset_mod.upsert_preset(db, "bad", bad)


def test_reject_missing_dim(db: Database) -> None:
    bad = {"short": {"ma_alignment": 0.5}, "mid": {"trend": 0.5}}  # 缺 long
    with pytest.raises(ValueError, match="long"):
        preset_mod.upsert_preset(db, "bad", bad)


def test_reject_all_zero_dim(db: Database) -> None:
    bad = {**VALID_W, "short": {"ma_alignment": 0, "kd": 0}}
    with pytest.raises(ValueError, match="至少要有一個"):
        preset_mod.upsert_preset(db, "bad", bad)


def test_builtin_presets_have_required_dims() -> None:
    """確保 BUILTIN_WEIGHT_PRESETS 結構合法（呼叫驗證函式）。"""
    for p in preset_mod.builtin_presets():
        # 內建 preset 也要能通過權重驗證
        preset_mod._validate_weights(p["weights"])
        assert p["label"]
        assert p["description"]
