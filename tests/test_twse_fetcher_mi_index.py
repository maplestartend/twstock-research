"""TWSE MI_INDEX 去重：daily_ohlcv_and_indices 一次抓、解析出兩個 DataFrame。

daily_ohlcv() 與 daily_indices() 打的是完全相同的 MI_INDEX URL/params，原本各發一次
HTTP（多一個往返 + 一個 request_delay）。新方法共用同一份 JSON，必須：
  (1) 與分開呼叫的結果逐欄等價（解析邏輯不變）
  (2) 只發一次 HTTP（在 _get_json 邊界計數）
"""
from __future__ import annotations

from unittest.mock import Mock

import pandas as pd

from app.data.twse_fetcher import TwseFetcher

# 一份最小但結構真實的 MI_INDEX JSON：
#   tables[0] = 價格指數表；後面某張 title 含「每日收盤行情」= OHLCV 表
FAKE_MI_INDEX = {
    "stat": "OK",
    "tables": [
        {
            "title": "發行量加權股價指數",
            "fields": ["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"],
            "data": [
                ["發行量加權股價指數", "18,000.50", "<p style='color:red'>+</p>", "100.25", "0.56", ""],
                ["未含金融保險股指數", "15,000.00", "<p style='color:green'>-</p>", "20.00", "0.13", ""],
            ],
        },
        {
            "title": "每日收盤行情(全部)",
            "fields": [
                "證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額",
                "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差",
            ],
            "data": [
                ["2330", "台積電", "30,000,000", "12,345", "18,000,000,000",
                 "600.00", "610.00", "595.00", "605.00", "<p style='color:red'>+</p>", "5.00"],
                ["2317", "鴻海", "20,000,000", "8,000", "2,000,000,000",
                 "100.00", "102.00", "99.00", "98.00", "<p style='color:green'>-</p>", "2.00"],
            ],
        },
    ],
}


def _fetcher() -> TwseFetcher:
    f = TwseFetcher(request_delay=0)
    return f


def test_combined_equals_separate():
    """daily_ohlcv_and_indices 的兩個輸出 == 分開呼叫 daily_ohlcv / daily_indices。"""
    f = _fetcher()
    f._get_json = Mock(return_value=FAKE_MI_INDEX)

    ohlcv_sep = f.daily_ohlcv("20260618")
    idx_sep = f.daily_indices("20260618")
    ohlcv_comb, idx_comb = f.daily_ohlcv_and_indices("20260618")

    pd.testing.assert_frame_equal(ohlcv_comb, ohlcv_sep)
    pd.testing.assert_frame_equal(idx_comb, idx_sep)


def test_combined_issues_single_request():
    """分開呼叫 → _get_json 兩次；合併呼叫 → 只一次（這就是省下的往返）。"""
    f = _fetcher()
    f._get_json = Mock(return_value=FAKE_MI_INDEX)

    f.daily_ohlcv("20260618")
    f.daily_indices("20260618")
    assert f._get_json.call_count == 2

    f._get_json.reset_mock()
    f.daily_ohlcv_and_indices("20260618")
    assert f._get_json.call_count == 1


def test_combined_parses_expected_values():
    """sanity：確認解析結果本身合理（不是兩邊都壞掉的等價）。"""
    f = _fetcher()
    f._get_json = Mock(return_value=FAKE_MI_INDEX)
    ohlcv, idx = f.daily_ohlcv_and_indices("20260618")

    assert list(ohlcv["stock_id"]) == ["2330", "2317"]
    row = ohlcv.set_index("stock_id").loc["2330"]
    assert row["close"] == 605.0
    assert row["spread"] == 5.0  # 漲（+）
    row2 = ohlcv.set_index("stock_id").loc["2317"]
    assert row2["spread"] == -2.0  # 跌（-）→ 帶負號

    assert "發行量加權股價指數" in set(idx["index_name"])
    taiex = idx.set_index("index_name").loc["發行量加權股價指數"]
    assert taiex["close"] == 18000.5
    assert taiex["change"] == 100.25  # 漲（+）
    weighted = idx.set_index("index_name").loc["未含金融保險股指數"]
    assert weighted["change"] == -20.0  # 跌（-）→ 帶負號


def test_combined_handles_non_ok_json():
    """非交易日 / stat != OK → 兩個都回空 DataFrame，不爆。"""
    f = _fetcher()
    f._get_json = Mock(return_value=None)
    ohlcv, idx = f.daily_ohlcv_and_indices("20260101")
    assert ohlcv.empty and idx.empty


def test_ohlcv_all_empty_sid_returns_empty_not_keyerror():
    """OHLCV 表有 rows 但所有 sid 皆空 → 回空 df，不該因缺 date 欄 KeyError。"""
    j = {
        "stat": "OK",
        "tables": [
            {"title": "x指數", "fields": ["指數", "收盤指數"], "data": []},
            {
                "title": "每日收盤行情(全部)",
                "fields": ["證券代號", "證券名稱", "收盤價"],
                "data": [["", "", "100.0"], ["  ", "", "200.0"]],  # sid 全空
            },
        ],
    }
    f = _fetcher()
    f._get_json = Mock(return_value=j)
    ohlcv = f.daily_ohlcv("20260618")
    assert ohlcv.empty
