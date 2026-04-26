"""S2-7：BUILTIN_WEIGHT_PRESETS 結構不變式（每次改 rubric.py 都該擋下手滑）。

涵蓋：
- 每個 preset 三維度的 keys 必須和 SHORT/MID/LONG_TERM_WEIGHTS 完全一致
- 三組權重各自 sum 必須 == 1.0（容忍浮點誤差 1e-6）
- BEGINNER_VISIBLE_KEYS 名稱必須是上述 keys 的子集
- COMPOSITE_WEIGHTS sum == 1.0
"""
from __future__ import annotations

import math

import pytest

from app.scoring.rubric import (
    BEGINNER_VISIBLE_KEYS,
    BUILTIN_WEIGHT_PRESETS,
    COMPOSITE_WEIGHTS,
    LONG_TERM_WEIGHTS,
    MID_TERM_WEIGHTS,
    SHORT_TERM_WEIGHTS,
)

REFERENCE_KEYS = {
    "short": set(SHORT_TERM_WEIGHTS),
    "mid": set(MID_TERM_WEIGHTS),
    "long": set(LONG_TERM_WEIGHTS),
}


@pytest.mark.parametrize("preset_name", list(BUILTIN_WEIGHT_PRESETS.keys()))
def test_preset_has_label_and_description(preset_name):
    p = BUILTIN_WEIGHT_PRESETS[preset_name]
    assert "label" in p and isinstance(p["label"], str) and p["label"].strip()
    assert "description" in p and isinstance(p["description"], str) and p["description"].strip()
    assert "weights" in p and isinstance(p["weights"], dict)


@pytest.mark.parametrize("preset_name", list(BUILTIN_WEIGHT_PRESETS.keys()))
@pytest.mark.parametrize("dim", ["short", "mid", "long"])
def test_preset_keys_match_reference(preset_name, dim):
    """新增子指標 (e.g. 把 SHORT_TERM_WEIGHTS 加 'turnover') 時，preset 沒同步加會被擋下。"""
    weights = BUILTIN_WEIGHT_PRESETS[preset_name]["weights"][dim]
    actual = set(weights.keys())
    expected = REFERENCE_KEYS[dim]
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"{preset_name}.{dim} 缺 keys: {sorted(missing)}"
    assert not extra, f"{preset_name}.{dim} 多 keys: {sorted(extra)}"


@pytest.mark.parametrize("preset_name", list(BUILTIN_WEIGHT_PRESETS.keys()))
@pytest.mark.parametrize("dim", ["short", "mid", "long"])
def test_preset_weights_sum_to_one(preset_name, dim):
    """三組權重各自 sum 必須 == 1.0。改參數時手滑算錯會被擋下。"""
    weights = BUILTIN_WEIGHT_PRESETS[preset_name]["weights"][dim]
    s = sum(weights.values())
    assert math.isclose(s, 1.0, abs_tol=1e-6), (
        f"{preset_name}.{dim} weights sum = {s} (應為 1.0): {weights}"
    )


@pytest.mark.parametrize("preset_name", list(BUILTIN_WEIGHT_PRESETS.keys()))
@pytest.mark.parametrize("dim", ["short", "mid", "long"])
def test_preset_weights_non_negative(preset_name, dim):
    weights = BUILTIN_WEIGHT_PRESETS[preset_name]["weights"][dim]
    for k, v in weights.items():
        assert v >= 0, f"{preset_name}.{dim}.{k} = {v} 不應為負"


def test_reference_weights_sum_to_one():
    """SHORT/MID/LONG_TERM_WEIGHTS 自身也該 sum == 1.0。"""
    assert math.isclose(sum(SHORT_TERM_WEIGHTS.values()), 1.0, abs_tol=1e-6)
    assert math.isclose(sum(MID_TERM_WEIGHTS.values()), 1.0, abs_tol=1e-6)
    assert math.isclose(sum(LONG_TERM_WEIGHTS.values()), 1.0, abs_tol=1e-6)


def test_composite_weights_sum_to_one():
    assert math.isclose(sum(COMPOSITE_WEIGHTS.values()), 1.0, abs_tol=1e-6)
    assert set(COMPOSITE_WEIGHTS.keys()) == {"short", "mid", "long"}


@pytest.mark.parametrize("dim", ["short", "mid", "long"])
def test_beginner_visible_keys_are_subset(dim):
    """新手模式 whitelist 內所有 key 都必須是該維度真實存在的子指標。"""
    visible = set(BEGINNER_VISIBLE_KEYS[dim])
    expected = REFERENCE_KEYS[dim]
    invalid = visible - expected
    assert not invalid, f"BEGINNER_VISIBLE_KEYS.{dim} 含未知子指標: {sorted(invalid)}"
