"""
移动平均线 MA
=============
同时输出 MA5 / MA10 / MA20 / MA60, 简单移动平均。
"""
from __future__ import annotations

from typing import List

import pandas as pd

from .base import MaPoint, to_dataframe


def compute_ma(klines) -> List[MaPoint]:
    if not klines:
        return []
    df = to_dataframe(_norm(klines))
    out: List[MaPoint] = []
    for i in range(len(df)):
        m5 = _safe(df["close"], 5, i)
        m10 = _safe(df["close"], 10, i)
        m20 = _safe(df["close"], 20, i)
        m60 = _safe(df["close"], 60, i)
        # 全部 NaN (数据不够) 就跳过
        if all(pd.isna(v) for v in (m5, m10, m20, m60)):
            continue
        out.append(MaPoint(
            time=int(df["time"].iloc[i]),
            ma5=_f(m5), ma10=_f(m10), ma20=_f(m20), ma60=_f(m60),
        ))
    return out


def _safe(s, n, i):
    if i + 1 < n:
        return float("nan")
    return s.iloc[i - n + 1:i + 1].mean()


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