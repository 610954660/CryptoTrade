"""
价格/涨跌幅统计
================
- 开高低收
- 相对前一根的涨跌幅 (%)
"""
from __future__ import annotations

from typing import List

import pandas as pd

from .base import PricePoint, to_dataframe


def compute_price_stats(klines) -> List[PricePoint]:
    if not klines:
        return []
    df = to_dataframe(_norm(klines))
    close = df["close"].astype(float)
    pct = close.pct_change() * 100
    out: List[PricePoint] = []
    for i in range(len(df)):
        c = close.iloc[i]
        prev = close.iloc[i - 1] if i > 0 else float("nan")
        p = pct.iloc[i] if i > 0 else float("nan")
        out.append(PricePoint(
            time=int(df["time"].iloc[i]),
            open=float(df["open"].iloc[i]),
            high=float(df["high"].iloc[i]),
            low=float(df["low"].iloc[i]),
            close=float(c),
            pct_change=_f(p),
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