"""統一的市場分類：依代號規則 + stock_info.type 把標的分成 上市 / 上櫃 / ETF / 其他。

ETF 識別規則：代號 4~6 碼純數字（含字尾 L/R/B/U）、且以 '00' 開頭。
- 4 碼例：0050、0051、0052... 0056、0057、0061
- 5 碼例：00878、00679B、00646、00733
- 6 碼例：00940、00946（新發行）

這條規則被 radar / dashboard / watchlist 等 router 共用，避免邏輯散落各處。
"""
from __future__ import annotations


def is_etf(stock_id: str | None) -> bool:
    """ETF 識別：以 '00' 開頭、長度 >= 4。
    台股 4 碼純數字代號：1xxx~9xxx 為一般上市/櫃公司、0xxx 為 ETF / 受益憑證 / 特殊商品。
    所以「0」開頭的 4 碼以上代號可視為 ETF（0050 是經典案例）。"""
    if not stock_id:
        return False
    return stock_id.startswith("00") and len(stock_id) >= 4


def classify_market(stock_id: str | None, type_: str | None) -> str:
    if is_etf(stock_id):
        return "ETF"
    if type_ == "twse":
        return "上市"
    if type_ == "tpex":
        return "上櫃"
    return "其他"
