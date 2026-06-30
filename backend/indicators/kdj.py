"""
KDJ 随机指标
============
默认 9 周期。
RSV = (close - low9) / (high9 - low9) * 100
K = SMA(RSV, 3, 1)    (今日 RSV/3 + 前日 K * 2/3)
D = SMA(K, 3, 1)
J = 3K - 2D
"""
from __future__ import annotations

from typing import List

import pandas as pd

from .base import KdjPoint, to_dataframe


def compute_kdj(klines, n: int = 9, k_period: int = 3, d_period: int = 3) -> List[KdjPoint]:
    if not klines:
        return []
    df = to_dataframe(_norm(klines))
    if len(df) < n:
        return []
    low_n = df["low"].rolling(window=n, min_periods=n).min()
    high_n = df["high"].rolling(window=n, min_periods=n).max()
    span = (high_n - low_n).replace(0, float("nan"))
    rsv = (df["close"] - low_n) / span * 100
    # K/D 用 SMA
    k_vals = [float("nan")] * len(df)
    d_vals = [float("nan")] * len(df)
    k_prev = 50.0
    d_prev = 50.0
    for i in range(len(df)):
        rv = rsv.iloc[i]
        if pd.isna(rv):
            continue
        k_new = (rv + k_prev * (k_period - 1)) / k_period
        d_new = (k_new + d_prev * (d_period - 1)) / d_period
        k_vals[i] = k_new
        d_vals[i] = d_new
        k_prev = k_new
        d_prev = d_new
    out: List[KdjPoint] = []
    for i in range(len(df)):
        k = k_vals[i]
        d = d_vals[i]
        if pd.isna(k) or pd.isna(d):
            continue
        j = 3 * k - 2 * d
        out.append(KdjPoint(time=int(df["time"].iloc[i]), k=k, d=d, j=j))
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