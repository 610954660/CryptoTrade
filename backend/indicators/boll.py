"""
布林带 BOLL
===========
原 indicators.compute_boll 的拆分版本, 加了类型注解。

参数: 默认 20 周期, 2 倍标准差。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import pandas as pd

from .base import to_dataframe


@dataclass
class BollPoint:
    time: int
    mid: float      # 中轨 = MA(N)
    upper: float    # 上轨
    lower: float    # 下轨


def compute_boll(klines, period: int = 20, std_mul: float = 2.0) -> List[BollPoint]:
    """从 K 线原始数据算 BOLL。 兼容 list-of-list 和已转换的 bars 列表。"""
    if not klines:
        return []
    df = to_dataframe(_normalize(klines))
    if len(df) < period:
        return []
    mid = df["close"].rolling(window=period, min_periods=period).mean()
    std = df["close"].rolling(window=period, min_periods=period).std(ddof=0)
    upper = mid + std_mul * std
    lower = mid - std_mul * std
    out: List[BollPoint] = []
    for i in range(len(df)):
        m = mid.iloc[i]
        u = upper.iloc[i]
        l = lower.iloc[i]
        if pd.isna(m) or pd.isna(u) or pd.isna(l):
            continue
        out.append(BollPoint(
            time=int(df["time"].iloc[i]),
            mid=float(m),
            upper=float(u),
            lower=float(l),
        ))
    return out


def _normalize(klines):
    """兼容 (list[list]) 和 (list[BollPoint 之类]). 只取 time/open/high/low/close/volume."""
    if not klines:
        return []
    # 已经是 list[BollPoint]/KLineBar 之类 (有 .time 属性)
    if hasattr(klines[0], "time") and hasattr(klines[0], "close"):
        return [
            (b.time, b.open, b.high, b.low, b.close, getattr(b, "volume", 0))
            for b in klines
        ]
    return klines


def boll_to_dicts(points: Sequence[BollPoint]):
    """序列化成 dict, 给前端 SSE 用。"""
    return [
        {"time": p.time, "mid": p.mid, "upper": p.upper, "lower": p.lower}
        for p in points
    ]