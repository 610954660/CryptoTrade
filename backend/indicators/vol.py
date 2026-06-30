"""
成交量统计
==========
- 当根成交量
- 5 周期均量
- 20 周期均量

形态匹配:
- 量能放大 (volume > avg_vol_5 * X)
- 量能缩小
- 突破均量
"""
from __future__ import annotations

from typing import List

import pandas as pd

from .base import VolPoint, to_dataframe


def compute_vol_stats(klines) -> List[VolPoint]:
    if not klines:
        return []
    df = to_dataframe(_norm(klines))
    vol = df["volume"].astype(float)
    avg5 = vol.rolling(window=5, min_periods=5).mean()
    avg20 = vol.rolling(window=20, min_periods=20).mean()
    out: List[VolPoint] = []
    for i in range(len(df)):
        v = vol.iloc[i]
        a5 = avg5.iloc[i]
        a20 = avg20.iloc[i]
        # 至少要均值算得出来才记录
        if pd.isna(v):
            continue
        out.append(VolPoint(
            time=int(df["time"].iloc[i]),
            volume=float(v),
            avg_vol_5=_f(a5),
            avg_vol_20=_f(a20),
        ))
    return out


def _f(v):
    return float(v) if not pd.isna(v) else float("nan")


def _norm(klines):
    if not klines:
        return []
    if hasattr(klines[0], "time") and hasattr(klines[0], "close"):
        return [
            (b.time, b.open, b.high, b.low, b.close, getattr(b, "volume", 0))
            for b in klines
        ]
    return klines