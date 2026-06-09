"""TWSE/TPEX 盤中即時報價（mis.twse.com.tw）。

設計目的：
- 評分系統的價格相關因子（RSI / MA / Bollinger / KD）對最後一筆 close 高度敏感。
- 盤後 16:30 才更新 daily_price → 隔天進場時，盤中價格已偏移，分數失準。
- 此 client 從 TWSE mis 拿到盤中即時價，讓 UI 能在盤中重算「短期分數」而不誤導使用者。

注意：
- mis API 是 TWSE 官網即時牌頁的後端，非正式對外 API（無 SLA、可能被改）。
- 只用於「個股詳情頁的即時切換 / what-if」這種互動場景；**不寫入 daily_price，也不灌進 signal_history**
  （否則會污染回測來源；參見 CLAUDE.md 第 5 點）。
- 30 秒記憶體快取避免 hammer：同一檔股票 30 秒內反覆 query 只會 hit 一次外部。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

_BASE = "https://mis.twse.com.tw/stock"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://mis.twse.com.tw/stock/index.jsp",
}

_CACHE_TTL_SEC = 30.0


@dataclass(frozen=True)
class IndexQuote:
    """大盤指數的盤中報價快照（mis 對指數的回傳格式比個股精簡，沒有 a/b/v 欄位）。

    - `value` 取得優先序：`z` 最新指數 → `pz` 前一筆 → `y` 昨收（fallback，is_live=False）
    - 指數沒有委託簿 / 漲跌停限制，所以個股的 midpoint / limit_up 那幾條 fallback 用不到
    - `tv` 是當日累積成交值（NTD），盤後資料尚未結帳前看會偏低；caller 不要拿來算交易量比例
    """
    index_id: str          # mis ex_ch 的 code 部分（例 't00'）
    name: str              # 指數中文名（例 '發行量加權股價指數'）
    value: float           # 當下指數值
    prev_close: Optional[float]
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    quote_time: Optional[str] = None
    is_live: bool = True
    quote_source: str = "match"  # "match" | "prev_match" | "prev_close"


@dataclass(frozen=True)
class IntradayQuote:
    """單一檔股票的盤中報價快照。

    `price` 的取得優先序（mis 對 5 秒撮合制下，多數時刻 `z` 為空白 — 只有剛成交的股票才有；
    漲跌停鎖死 + 撮合空檔同時發生時 `z` 跟 `pz` 都會是 '-'，要靠價量結構判斷）：
      1. `z`         最新成交價（match）
      2. `pz`        前一筆撮合價（prev_match）
      3. `u` if h==u  漲停鎖死（limit_up，威剛 3260 之類觸發）
      4. `w` if l==w  跌停鎖死（limit_down）
      5. (a1+b1)/2   最佳買賣中價（midpoint），盤前/閒置撮合間正常波動
      6. a1 only     只有 ask 一邊有報價（罕見，急跌中）
      7. b1 only     只有 bid 一邊有報價（罕見）
      8. y           昨收 fallback（盤後／興櫃／全失敗）
    1~7 視為「即時」(`is_live=True`)；只有掉到第 8 步才 `is_live=False`，前端會標「非盤中（昨收）」。
    """
    stock_id: str
    price: float           # 依上方優先序選出的「當下價」
    prev_close: Optional[float]    # 昨收 (y)
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    bid1: Optional[float] = None   # 最佳買價（5 檔報價最頂）
    ask1: Optional[float] = None   # 最佳賣價
    volume_lots: Optional[float] = None   # 累計成交張數（1 張 = 1000 股）
    quote_time: Optional[str] = None      # "HH:MM:SS"，mis 給的撮合時間
    is_live: bool = True
    quote_source: str = "match"
    # 可能值："match" | "prev_match" | "limit_up" | "limit_down" |
    #         "midpoint" | "ask_only" | "bid_only" | "prev_close"


class _Cache:
    """單純的 thread-safe TTL cache。沒用到 cachetools 是因為這個模組就只有一處用。"""
    def __init__(self) -> None:
        self._d: dict[str, tuple[float, IntradayQuote | None]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float) -> tuple[bool, IntradayQuote | None]:
        with self._lock:
            entry = self._d.get(key)
            if entry is None:
                return False, None
            ts, val = entry
            if time.time() - ts > ttl:
                return False, None
            return True, val

    def put(self, key: str, val: IntradayQuote | None) -> None:
        with self._lock:
            self._d[key] = (time.time(), val)


_cache = _Cache()
_session: requests.Session | None = None
_session_lock = threading.Lock()


def _get_session() -> requests.Session:
    """共用一個 session 避免每次 query 都重做 TLS handshake；headers 預先設好。"""
    global _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            s.headers.update(_HEADERS)
            # mis 偶爾要先打 index.jsp 拿 cookie 才會回完整 JSON；warm-up 失敗不致命。
            try:
                s.get(f"{_BASE}/index.jsp", timeout=5)
            except requests.RequestException:
                pass
            _session = s
        return _session


def _ex_ch(stock_id: str, market_type: str | None) -> str:
    """`ex_ch` 是 mis API 的查詢 key 格式：`tse_2330.tw` 或 `otc_5483.tw`。

    `market_type` 從 stock_info.type 來：'twse' / 'tpex' / 'emerging'。
    emerging（興櫃）mis 不提供即時，會回空 array；caller 拿到 None。
    未知時優先試 tse_，失敗再試 otc_（caller 端 fallback）。
    """
    prefix = "tse" if market_type == "twse" else "otc"
    return f"{prefix}_{stock_id}.tw"


def _parse_msg(msg: dict[str, Any]) -> IntradayQuote | None:
    """mis 回傳的單筆 msgArray entry → IntradayQuote。

    五秒撮合制重點：mis 對任一瞬間多數股票 `z` 為空白 ('-')，只有剛在上一個 5 秒窗口成交的股票才有。
    所以 fallback 不能直接從 z 跳到 y（會誤把盤中當盤後）。優先序：
      1) z   最新撮合價
      2) pz  前一筆撮合價
      3) mid (a1+b1)/2 最佳買賣中價（盤中只要有委託簿就有）
      4) y   昨收（前三都缺，視為非盤中）

    欄位 ref：z/pz/tv/o/h/l/v/y/c/t/a/b（a/b 為 5 檔，下劃線分隔；最頂筆 = best ask/bid1）。
    """
    sid = msg.get("c") or ""
    if not sid:
        return None
    z = _to_float(msg.get("z"))
    pz = _to_float(msg.get("pz"))
    y = _to_float(msg.get("y"))
    o = _to_float(msg.get("o"))
    h = _to_float(msg.get("h"))
    l = _to_float(msg.get("l"))
    u = _to_float(msg.get("u"))    # 漲停價
    w = _to_float(msg.get("w"))    # 跌停價
    v = _to_float(msg.get("v"))
    t = msg.get("t") or None
    a1 = _first_level(msg.get("a"))
    b1 = _first_level(msg.get("b"))

    if z is not None:
        price, source, is_live = z, "match", True
    elif pz is not None:
        price, source, is_live = pz, "prev_match", True
    elif _at_limit(h, u) and _has_volume(v):
        # 漲停鎖死：h 觸到 u 且今日有成交。鎖死期 mis 常 z=pz='-'、a='-'、b1=0（市價單佔位），
        # 漏抓會掉到 y 顯示 -10%（威剛 3260 那類飆股回報的真實 case）。直接吃 u 才對。
        price, source, is_live = u, "limit_up", True  # type: ignore[assignment]
    elif _at_limit(l, w) and _has_volume(v):
        price, source, is_live = w, "limit_down", True  # type: ignore[assignment]
    elif a1 is not None and b1 is not None:
        price, source, is_live = round((a1 + b1) / 2, 4), "midpoint", True
    elif a1 is not None:
        price, source, is_live = a1, "ask_only", True
    elif b1 is not None:
        price, source, is_live = b1, "bid_only", True
    elif y is not None:
        price, source, is_live = y, "prev_close", False
    else:
        return None

    return IntradayQuote(
        stock_id=sid,
        price=price,
        prev_close=y,
        open=o,
        high=h,
        low=l,
        bid1=b1,
        ask1=a1,
        volume_lots=v,
        quote_time=t,
        is_live=is_live,
        quote_source=source,
    )


def _first_level(s: Any) -> float | None:
    """mis 的 a/b 欄位是 5 檔報價以 '_' 分隔的字串，取最頂層（best）。

    `0.0000` 是 mis 對「市價單佔位」的編碼（漲跌停鎖死時市價買賣單會排在「任意價」這格，
    顯示成 0），不是真的 0 元委買委賣。連同 '-'/空白一起當缺失處理。
    """
    if not s:
        return None
    head = str(s).split("_", 1)[0]
    f = _to_float(head)
    if f is None or f <= 0:
        return None
    return f


def _at_limit(price: float | None, limit: float | None) -> bool:
    """price 是否觸到 limit（價差 < 0.005，避免浮點誤差）。"""
    if price is None or limit is None:
        return False
    return abs(price - limit) < 0.005


def _has_volume(v: float | None) -> bool:
    return v is not None and v > 0


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_quote(
    stock_id: str,
    market_type: str | None = None,
    *,
    use_cache: bool = True,
) -> IntradayQuote | None:
    """抓單檔即時報價。失敗（network / 無此股 / 興櫃）回 None。

    - `market_type`：stock_info.type；不確定就傳 None，會自動 tse → otc fallback。
    - 30 秒 cache 命中時直接回傳，不打外部。
    - 興櫃股票 mis 不支援；fallback 都失敗就回 None。
    """
    cache_key = f"{stock_id}:{market_type or '?'}"
    if use_cache:
        hit, val = _cache.get(cache_key, _CACHE_TTL_SEC)
        if hit:
            return val

    try:
        quote = _do_fetch(stock_id, market_type)
    except requests.RequestException as exc:
        logger.debug("intraday fetch %s failed: %s", stock_id, exc)
        quote = None

    _cache.put(cache_key, quote)
    return quote


def _do_fetch(stock_id: str, market_type: str | None) -> IntradayQuote | None:
    """實際打 mis API。market_type 未知時 tse → otc 兩道試。"""
    candidates: list[str]
    if market_type == "twse":
        candidates = ["twse"]
    elif market_type == "tpex":
        candidates = ["tpex"]
    elif market_type == "emerging":
        return None  # mis 不支援興櫃
    else:
        candidates = ["twse", "tpex"]

    sess = _get_session()
    for mt in candidates:
        ex_ch = _ex_ch(stock_id, mt)
        url = f"{_BASE}/api/getStockInfo.jsp"
        resp = sess.get(url, params={"ex_ch": ex_ch, "json": "1", "delay": "0"}, timeout=8)
        if resp.status_code != 200:
            continue
        try:
            j = resp.json()
        except ValueError:
            continue
        msgs = j.get("msgArray") or []
        if not msgs:
            continue
        quote = _parse_msg(msgs[0])
        if quote is not None:
            return quote
    return None


# mis 單一請求可串接的最大檔數。mis 的 ex_ch 用 '|' 串多檔（tse_2330.tw|otc_5483.tw|...），
# 一次回傳整包 msgArray。實測 ~100 檔仍穩定，保守抓 50 以壓低 URL 長度與單請求失敗的爆炸半徑。
_BATCH_CHUNK = 50


def fetch_quotes(
    pairs: list[tuple[str, str | None]],
    *,
    use_cache: bool = True,
) -> dict[str, IntradayQuote]:
    """批次抓多檔即時報價（給雷達/自選「盤中即時重算一頁」用）。

    `pairs`：[(stock_id, market_type), ...]，market_type 同 fetch_quote（'twse'/'tpex'/None）。
    回傳 `{stock_id: IntradayQuote}`，**只含成功抓到的檔**（興櫃 / mis 失敗 / 無此股的直接缺席，
    caller 用「有就覆蓋、沒有就維持快照」邏輯，與 holdings/intraday 一致）。

    與「for sid: fetch_quote(sid)」相比的差異與理由：
    - 用 mis 原生的 '|' 串接，一次 HTTP 抓 ~50 檔，把一頁（≤50）的對外請求數從 50 壓到 1。
      盤中清單每 30s 刷一次、多使用者同時看時，這是避免 hammer mis（非官方、會 rate-limit）的關鍵。
    - 沿用 fetch_quote 的 30s per-symbol cache：cache 命中的檔不進這輪外部請求，
      只把「真的 cold」的檔串成一個 batch query。
    - market_type 不可為 None（呼叫端先查好 stock_info.type）：None 會無從決定 tse_/otc_ 前綴，
      批次模式不做逐檔 tse→otc fallback（那會讓請求數翻倍、違背批次的初衷）。None 一律當 'twse' 試。
    """
    out: dict[str, IntradayQuote] = {}
    cold: list[tuple[str, str | None]] = []
    # 先吃 cache，把命中的直接收下，只有 miss 的才進 batch
    for sid, mt in pairs:
        if not sid:
            continue
        if use_cache:
            hit, val = _cache.get(f"{sid}:{mt or '?'}", _CACHE_TTL_SEC)
            if hit:
                if val is not None:
                    out[sid] = val
                continue
        cold.append((sid, mt))

    # cold 依 chunk 切批，逐批打一次 mis
    for i in range(0, len(cold), _BATCH_CHUNK):
        chunk = cold[i:i + _BATCH_CHUNK]
        ex_ch_list = "|".join(_ex_ch(sid, mt) for sid, mt in chunk)
        parsed: dict[str, IntradayQuote] = {}
        try:
            sess = _get_session()
            url = f"{_BASE}/api/getStockInfo.jsp"
            resp = sess.get(url, params={"ex_ch": ex_ch_list, "json": "1", "delay": "0"}, timeout=10)
            if resp.status_code == 200:
                try:
                    j = resp.json()
                except ValueError:
                    j = None
                if j is not None:
                    for msg in (j.get("msgArray") or []):
                        q = _parse_msg(msg)
                        if q is not None:
                            parsed[q.stock_id] = q
        except requests.RequestException as exc:
            logger.debug("intraday batch fetch failed (%d symbols): %s", len(chunk), exc)
        # 逐檔寫 cache（含 miss → None，避免同一檔 30s 內反覆進 cold batch）
        for sid, mt in chunk:
            q = parsed.get(sid)
            _cache.put(f"{sid}:{mt or '?'}", q)
            if q is not None:
                out[sid] = q
    return out


_index_cache: dict[str, tuple[float, IndexQuote | None]] = {}
_index_cache_lock = threading.Lock()

# mis 對 TWSE 加權指數的查詢 key
TAIEX_EX_CH = "tse_t00.tw"
TAIEX_NAME = "發行量加權股價指數"


def _parse_index_msg(msg: dict[str, Any]) -> IndexQuote | None:
    """mis 指數回傳的單筆 entry → IndexQuote。

    與股票的差異：
    - 指數無 a/b（委託簿）也無 u/w（漲跌停），所以只走 z → pz → y 三段。
    - z='-' 不代表盤後 — 指數每秒都在更新，z 空白時實務上很罕見（例如 mis 短暫故障）；
      但既然偶爾會發生（與個股 5 秒撮合制不同的另一種空白原因），仍保留 pz fallback。
    """
    sid = msg.get("c") or ""
    if not sid:
        return None
    name = msg.get("n") or ""
    z = _to_float(msg.get("z"))
    pz = _to_float(msg.get("pz"))
    y = _to_float(msg.get("y"))
    o = _to_float(msg.get("o"))
    h = _to_float(msg.get("h"))
    lo = _to_float(msg.get("l"))
    t = msg.get("t") or None

    if z is not None:
        value, source, is_live = z, "match", True
    elif pz is not None:
        value, source, is_live = pz, "prev_match", True
    elif y is not None:
        value, source, is_live = y, "prev_close", False
    else:
        return None

    return IndexQuote(
        index_id=sid,
        name=name,
        value=value,
        prev_close=y,
        open=o,
        high=h,
        low=lo,
        quote_time=t,
        is_live=is_live,
        quote_source=source,
    )


def fetch_index_quote(
    ex_ch: str = TAIEX_EX_CH,
    *,
    use_cache: bool = True,
) -> IndexQuote | None:
    """抓大盤指數即時值（預設 TAIEX）。失敗回 None。

    - 30 秒 cache 與股票分開（避免 key 衝突；caller 只查指數時不會撞到股票快取容量）
    - 不可寫入 `index_daily`（盤中值非 final close，會污染回測來源）— 與 fetch_quote 同樣
      的職責邊界，參見模組 docstring。
    """
    cache_key = ex_ch
    if use_cache:
        with _index_cache_lock:
            entry = _index_cache.get(cache_key)
            if entry is not None and time.time() - entry[0] <= _CACHE_TTL_SEC:
                return entry[1]

    quote: IndexQuote | None = None
    try:
        sess = _get_session()
        url = f"{_BASE}/api/getStockInfo.jsp"
        resp = sess.get(url, params={"ex_ch": ex_ch, "json": "1", "delay": "0"}, timeout=8)
        if resp.status_code == 200:
            try:
                j = resp.json()
            except ValueError:
                j = None
            if j is not None:
                msgs = j.get("msgArray") or []
                if msgs:
                    quote = _parse_index_msg(msgs[0])
    except requests.RequestException as exc:
        logger.debug("intraday index fetch %s failed: %s", ex_ch, exc)
        quote = None

    with _index_cache_lock:
        _index_cache[cache_key] = (time.time(), quote)
    return quote


def clear_cache() -> None:
    """測試用：清掉 in-memory cache（含個股 + 指數兩份）。"""
    with _cache._lock:
        _cache._d.clear()
    with _index_cache_lock:
        _index_cache.clear()
