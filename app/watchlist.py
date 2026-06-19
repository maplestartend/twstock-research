"""自選股（watchlist.yaml）的讀寫與增刪 + tags 標籤。

YAML 結構（v2，向後相容 v1）：

    stocks:
      '1722': 台肥
      '3033': 威健
    tags:           # 可選；舊檔沒有此鍵也能正常 load
      '1722': [長期持有, 配息]
      '3033': [短線]

設計取捨：tags 用 parallel map（與 stocks 同層）而非把 value 改成 dict，目的是
- `load()` 仍回 dict[str, str]，舊呼叫端（dq.py / report.py / portfolio.py 等多處）零改動
- 寫進 yaml 也仍可被舊版 reader 解析（多餘鍵會被忽略）

`tags` 入清理規則：去頭尾空白、捨空字串、保留出現順序去重，避免「長期持有」與
「長期持有 」（後綴空白）被當兩個 tag。
"""
from __future__ import annotations

import copy
import threading
import time
from pathlib import Path

import yaml

from app.config import PROJECT_ROOT

WATCHLIST_PATH = PROJECT_ROOT / "watchlist.yaml"

# watchlist.yaml 每個請求會被讀好幾次（例如 /dashboard/home 一次就有 ex_dividend /
# my_score_changes / movers↑ / movers↓ 各自 load() → 4 次 YAML parse）。檔案極少變動，
# 用 (mtime_ns, size) 當 key 快取已解析的 raw dict：命中就跳過 open + yaml.safe_load。
#
# 正確性保證：
# - 程式自身寫入一律走 _write_raw（atomic replace），它會主動 _invalidate_cache，
#   **不依賴 mtime 解析度** → in-process 的 add/remove/save/set_tags 永遠拿得到新值。
# - _read_raw() 永遠回 deepcopy：save/save_tags 等 mutator 會就地改 raw，回 copy 才不會
#   污染到快取物件。watchlist 很小（數十檔），deepcopy 成本遠低於重新 parse YAML。
#
# 外部改檔（app 外手動編輯）偵測：靠 size 變化 + mtime 變化。但 Windows 檔案 mtime 受
# 系統時鐘粗 tick 限制（實測同一 ~ms tick 內多次寫入會拿到相同 st_mtime_ns，並非奈秒解析度），
# 故「同大小、且落在同一 mtime tick」的改寫理論上會撞 key。用 _MTIME_SETTLE_NS 防護：
# mtime 距今很近（檔案可能還在變動）時不信任快取、強制重讀且不快取，等檔案「定下來」
# 超過此窗才開始快取 → 任何外部改檔只要 read 發生在改檔後 1s 內就一定重讀到新內容。
_MTIME_SETTLE_NS = 1_000_000_000  # 1s：mtime 距今 < 此值視為「剛寫過、可能還在變」
_cache_lock = threading.Lock()
_raw_cache: dict | None = None
_raw_cache_key: tuple[int, int] | None = None


def _read_raw() -> dict:
    global _raw_cache, _raw_cache_key
    try:
        st = WATCHLIST_PATH.stat()
    except OSError:
        # 檔案不存在 / 無法 stat → 視為空，並清掉可能殘留的快取
        with _cache_lock:
            _raw_cache = None
            _raw_cache_key = None
        return {}
    key = (st.st_mtime_ns, st.st_size)
    # 剛寫過的檔（mtime 距今 < settle 窗）不信任快取：避開粗 mtime tick 下「同大小、同 tick」
    # 改寫被快取鎖成陳舊的風險。穩態下 watchlist 幾乎不變動，此分支幾乎永遠為 False。
    fresh_write = (time.time_ns() - st.st_mtime_ns) < _MTIME_SETTLE_NS
    with _cache_lock:
        if not fresh_write and _raw_cache_key == key and _raw_cache is not None:
            return copy.deepcopy(_raw_cache)
    # cache miss：在鎖外做 IO + parse（避免長時間持鎖）
    with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        data = {}
    with _cache_lock:
        if fresh_write:
            # 檔案可能還在變動 → 不鎖進快取（避免把中途版本當成穩定值）
            _raw_cache = None
            _raw_cache_key = None
        else:
            _raw_cache = data
            _raw_cache_key = key
    return copy.deepcopy(data)


def _invalidate_cache() -> None:
    global _raw_cache, _raw_cache_key
    with _cache_lock:
        _raw_cache = None
        _raw_cache_key = None


def _write_raw(payload: dict) -> None:
    """原子寫入：先寫 .yaml.tmp 再 replace，避免中途 crash 把檔案截半。"""
    tmp = WATCHLIST_PATH.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            payload, f, allow_unicode=True, default_flow_style=False, sort_keys=False
        )
    tmp.replace(WATCHLIST_PATH)
    # 寫入後立即失效，不靠 mtime 解析度偵測（同一奈秒內的後續讀取也保證拿到新內容）
    _invalidate_cache()


def _normalize_tags(tags: list[str] | None) -> list[str]:
    """trim + 去空 + 保序去重。給 set_tags / save_tags 用。"""
    if not tags:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        s = (t or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def load() -> dict[str, str]:
    raw = _read_raw()
    return {str(k): str(v) for k, v in (raw.get("stocks") or {}).items()}


def load_tags() -> dict[str, list[str]]:
    """每檔的 tags；缺鍵或結構壞了 → 空 dict。
    結果只包含「真的有 tag」的檔；UI 不需擔心 KeyError。"""
    raw = _read_raw()
    out: dict[str, list[str]] = {}
    for sid, tags in (raw.get("tags") or {}).items():
        if isinstance(tags, list):
            cleaned = _normalize_tags([str(t) for t in tags])
            if cleaned:
                out[str(sid)] = cleaned
    return out


def save(stocks: dict[str, str]) -> None:
    """覆寫 stocks 區段；保留現有 tags 不動。"""
    raw = _read_raw()
    raw["stocks"] = dict(sorted(stocks.items(), key=lambda x: x[0]))
    _write_raw(raw)


def save_tags(tags_map: dict[str, list[str]]) -> None:
    """覆寫整個 tags 區段。空 list / 不存在的 stock_id 自動清除（避免野鬼）。"""
    raw = _read_raw()
    valid_sids = set(raw.get("stocks") or {})
    cleaned: dict[str, list[str]] = {}
    for sid, tags in tags_map.items():
        sid = str(sid)
        if sid not in valid_sids:
            continue
        normalized = _normalize_tags(tags)
        if normalized:
            cleaned[sid] = normalized
    if cleaned:
        # tags 段按 stock_id 排序，diff 友善
        raw["tags"] = dict(sorted(cleaned.items()))
    else:
        raw.pop("tags", None)  # 全清空就把整段拿掉，yaml 看起來乾淨
    _write_raw(raw)


def set_tags(stock_id: str, tags: list[str]) -> bool:
    """單檔 tags 覆寫；stock 不在 watchlist 時回 False（caller 應先確認存在）。"""
    sid = str(stock_id).strip()
    raw = _read_raw()
    if sid not in (raw.get("stocks") or {}):
        return False
    current = load_tags()
    cleaned = _normalize_tags(tags)
    if cleaned:
        current[sid] = cleaned
    else:
        current.pop(sid, None)  # 空 list = 清掉該檔的 tags
    save_tags(current)
    return True


def all_tags() -> list[str]:
    """目前出現過的所有 tag（distinct，按出現次數降序、相同次數時按字典序）。"""
    counts: dict[str, int] = {}
    for tags in load_tags().values():
        for t in tags:
            counts[t] = counts.get(t, 0) + 1
    return sorted(counts.keys(), key=lambda t: (-counts[t], t))


def add(stock_id: str, stock_name: str = "") -> bool:
    """新增一檔。已存在則不動，回傳 False。"""
    stocks = load()
    sid = str(stock_id).strip()
    if not sid:
        return False
    if sid in stocks:
        return False
    stocks[sid] = stock_name or sid
    save(stocks)
    return True


def add_many(items: dict[str, str]) -> int:
    """批次新增，回傳實際新增（已存在的不算）筆數。"""
    stocks = load()
    added = 0
    for sid, name in items.items():
        sid = str(sid).strip()
        if not sid or sid in stocks:
            continue
        stocks[sid] = name or sid
        added += 1
    if added:
        save(stocks)
    return added


def remove(stock_id: str) -> bool:
    stocks = load()
    sid = str(stock_id).strip()
    if sid not in stocks:
        return False
    del stocks[sid]
    save(stocks)
    # 順手把 orphan tags 清掉
    tags = load_tags()
    if sid in tags:
        tags.pop(sid)
        save_tags(tags)
    return True


def remove_many(stock_ids: list[str]) -> int:
    stocks = load()
    tags = load_tags()
    removed = 0
    for sid in stock_ids:
        sid = str(sid).strip()
        if sid in stocks:
            del stocks[sid]
            tags.pop(sid, None)
            removed += 1
    if removed:
        save(stocks)
        save_tags(tags)
    return removed


def contains(stock_id: str) -> bool:
    return str(stock_id).strip() in load()
