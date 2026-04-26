"""常用技術指標。純 pandas/numpy，無外部 TA 依賴。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    # Wilder 平滑
    avg_gain = up.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = down.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def kd(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 9) -> tuple[pd.Series, pd.Series]:
    """台股常用 9 日 KD（Stochastic）。"""
    lowest = low.rolling(window=period, min_periods=period).min()
    highest = high.rolling(window=period, min_periods=period).max()
    rsv = (close - lowest) / (highest - lowest).replace(0, np.nan) * 100
    # 台股 KD 慣例：K = 2/3 * prev_K + 1/3 * RSV
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    return k, d


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger(close: pd.Series, period: int = 20, std_mult: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = sma(close, period)
    std = close.rolling(window=period, min_periods=period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower


def bb_position(close: pd.Series, period: int = 20, std_mult: float = 2.0) -> pd.Series:
    """布林通道位置，0=下軌、0.5=中線、1=上軌。"""
    upper, _, lower = bollinger(close, period, std_mult)
    width = (upper - lower).replace(0, np.nan)
    return (close - lower) / width


def volume_ratio(volume: pd.Series, period: int = 5) -> pd.Series:
    """量比 = 今日成交量 / 近 N 日均量。"""
    avg = volume.rolling(window=period, min_periods=period).mean()
    return volume / avg.replace(0, np.nan)


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """在 daily_price DataFrame 上加入所有常用技術指標。期望欄位：date/open/high/low/close/volume。"""
    df = df.sort_values("date").copy()
    df["ma5"] = sma(df["close"], 5)
    df["ma10"] = sma(df["close"], 10)
    df["ma20"] = sma(df["close"], 20)
    df["ma60"] = sma(df["close"], 60)
    df["ma120"] = sma(df["close"], 120)
    df["ma240"] = sma(df["close"], 240)

    df["rsi14"] = rsi(df["close"], 14)

    k, d = kd(df["high"], df["low"], df["close"], 9)
    df["k9"] = k
    df["d9"] = d

    macd_line, sig, hist = macd(df["close"])
    df["macd"] = macd_line
    df["macd_signal"] = sig
    df["macd_hist"] = hist

    upper, mid, lower = bollinger(df["close"], 20, 2.0)
    df["bb_upper"] = upper
    df["bb_mid"] = mid
    df["bb_lower"] = lower
    df["bb_pos"] = bb_position(df["close"], 20, 2.0)

    df["vol_ratio5"] = volume_ratio(df["volume"], 5)
    df["vol_ratio20"] = volume_ratio(df["volume"], 20)

    # MA 斜率（近 5 日變化率）
    df["ma20_slope"] = df["ma20"].pct_change(5)
    df["ma60_slope"] = df["ma60"].pct_change(10)

    return df
