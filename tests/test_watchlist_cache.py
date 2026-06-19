"""app.watchlist 的 _read_raw() mtime 快取契約測試（純檔案 IO、CI 可跑）。

驗證重點：
- 同一份「已定下來」的 watchlist.yaml 連續讀只 parse 一次（快取命中）
- 程式自身寫入（save/add/remove）後立即反映新內容（_write_raw 失效 + fresh_write 重讀雙保險）
- 外部直接改檔（size/mtime 變）會被偵測、重新讀取
- _read_raw() 回 deepcopy：mutator 就地改 raw 不會污染快取物件
- _MTIME_SETTLE_NS 防護：剛寫過的檔不信任快取、強制重讀

settle 說明：Windows 檔案 mtime 受系統時鐘粗 tick 限制，「剛寫的檔」會落在 settle 窗內
（_read_raw 會 bypass 快取）。要測「快取命中」本身，得先把 mtime 往回挪、跳出該窗。
"""
from __future__ import annotations

import os
import time

import yaml

import app.watchlist as wl


def _set_path(tmp_path, monkeypatch):
    """把 WATCHLIST_PATH 導到 tmp，並清掉跨測試殘留的 module 級快取。"""
    p = tmp_path / "watchlist.yaml"
    monkeypatch.setattr(wl, "WATCHLIST_PATH", p)
    wl._invalidate_cache()
    return p


def _write_yaml(p, payload):
    p.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")


def _settle(p):
    """把 mtime 往回挪 10 秒，跳出 _MTIME_SETTLE_NS 窗 → 讓快取真的會生效。"""
    past = time.time() - 10
    os.utime(p, (past, past))


def test_cache_hit_avoids_reparse(tmp_path, monkeypatch):
    p = _set_path(tmp_path, monkeypatch)
    _write_yaml(p, {"stocks": {"1722": "台肥"}})
    _settle(p)

    calls = {"n": 0}
    real_load = yaml.safe_load

    def counting_load(stream):
        calls["n"] += 1
        return real_load(stream)

    monkeypatch.setattr(wl.yaml, "safe_load", counting_load)

    # 連讀三次，檔案已定下來且未變 → 只 parse 一次
    assert wl.load() == {"1722": "台肥"}
    assert wl.load() == {"1722": "台肥"}
    assert wl.load_tags() == {}  # 也走 _read_raw，仍命中同一份快取
    assert calls["n"] == 1


def test_fresh_write_bypasses_cache(tmp_path, monkeypatch):
    """剛寫過（未 settle）的檔不信任快取：每次都重讀，避開粗 mtime tick 撞 key 的陳舊風險。"""
    p = _set_path(tmp_path, monkeypatch)
    _write_yaml(p, {"stocks": {"1722": "台肥"}})  # 不 settle → mtime≈now

    calls = {"n": 0}
    real_load = yaml.safe_load

    def counting_load(stream):
        calls["n"] += 1
        return real_load(stream)

    monkeypatch.setattr(wl.yaml, "safe_load", counting_load)
    wl.load()
    wl.load()
    wl.load()
    assert calls["n"] == 3  # fresh_write 窗內全部重讀，未快取


def test_write_invalidates_cache(tmp_path, monkeypatch):
    """end-to-end：add/remove 後 load() 立即反映（_write_raw 失效 + fresh_write 雙保險）。"""
    _set_path(tmp_path, monkeypatch)
    assert wl.add("1722", "台肥") is True
    assert wl.load() == {"1722": "台肥"}

    assert wl.add("3033", "威健") is True
    assert wl.load() == {"1722": "台肥", "3033": "威健"}

    assert wl.remove("1722") is True
    assert wl.load() == {"3033": "威健"}


def test_external_file_change_detected(tmp_path, monkeypatch):
    """已定下來的檔被外部改寫（size 變）→ 即使在 settle 窗外也靠 key 偵測到、重讀。"""
    p = _set_path(tmp_path, monkeypatch)
    _write_yaml(p, {"stocks": {"1722": "台肥"}})
    _settle(p)
    assert wl.load() == {"1722": "台肥"}  # 填快取

    # 模擬外部編輯器直接覆寫（內容與長度都不同）→ size 變、快取失效
    _write_yaml(p, {"stocks": {"2330": "台積電", "2317": "鴻海"}})
    _settle(p)  # 一樣挪到窗外，證明偵測靠 key 不靠 fresh_write
    assert wl.load() == {"2330": "台積電", "2317": "鴻海"}


def test_read_raw_returns_isolated_copy(tmp_path, monkeypatch):
    """命中快取後就地改回傳值，不會污染快取物件（deepcopy 隔離）。"""
    p = _set_path(tmp_path, monkeypatch)
    _write_yaml(p, {"stocks": {"1722": "台肥"}, "tags": {"1722": ["長期持有"]}})
    _settle(p)

    raw1 = wl._read_raw()  # miss → parse → 快取
    # 就地破壞回傳值（模擬 save() 的 raw["stocks"] = ... 之類 mutation）
    raw1["stocks"]["1722"] = "被污染"
    raw1["tags"]["1722"].append("髒tag")

    raw2 = wl._read_raw()  # 同一份已定下來的檔 → 命中快取、回乾淨 deepcopy
    assert raw2["stocks"]["1722"] == "台肥"
    assert raw2["tags"]["1722"] == ["長期持有"]


def test_missing_file_returns_empty_and_clears_cache(tmp_path, monkeypatch):
    p = _set_path(tmp_path, monkeypatch)
    _write_yaml(p, {"stocks": {"1722": "台肥"}})
    _settle(p)
    assert wl.load() == {"1722": "台肥"}  # 填快取（已 settle → 真的有快取）
    assert wl._raw_cache is not None

    p.unlink()  # 檔案被刪
    assert wl.load() == {}
    assert wl._raw_cache is None  # OSError 分支清掉殘留快取
