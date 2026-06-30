"""
RSI 相对强弱指标
================
默认 14 周期。 经典 Wilder 平滑。
RSI = 100 - 100 / (1 + RS)
RS = 平均涨幅 / 平均跌幅
"""
from __future__ import annotations

from typing import List

import pandas as pd

from .base import RsiPoint, to_dataframe


def compute_rsi(klines, period: int = 14) -> List[RsiPoint]:
    if not klines:
        return []
    df = to_dataframe(_norm(klines))
    if len(df) < period + 1:
        return []
    close = df["close"].astype(float)
    diff = close.diff()
    gain = diff.clip(lower=0.0)
    loss = (-diff).clip(lower=0.0)
    # Wilder 平滑: 第一期用均值, 之后 EMA
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    for i in range(period, len(gain)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - 100 / (1 + rs)
    out: List[RsiPoint] = []
    for i in range(len(df)):
        v = rsi.iloc[i]
        if pd.isna(v):
            continue
        out.append(RsiPoint(time=int(df["time"].iloc[i]), rsi=float(v)))
    return out


def _norm(klines):
    if not klines:
        return []
    if hasattr(klines[0], "time") and hasattr(klines[0], "close"):
        return [
            (b.time, b.open, b.high, b.low, b.close, getattr(b, "volume", 0))
            for b in klines
        ]
    return klines