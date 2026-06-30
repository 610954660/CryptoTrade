"""
MACD 平滑异同移动平均线
=======================
默认 12 / 26 / 9。
DIF = EMA12 - EMA26
DEA = EMA(DIF, 9)
MACD = (DIF - DEA) * 2
"""
from __future__ import annotations

from typing import List

import pandas as pd

from .base import MacdPoint, to_dataframe


def compute_macd(klines, fast: int = 12, slow: int = 26, signal: int = 9) -> List[MacdPoint]:
    if not klines:
        return []
    df = to_dataframe(_norm(klines))
    if len(df) < slow + signal:
        return []
    close = df["close"].astype(float)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd = (dif - dea) * 2
    out: List[MacdPoint] = []
    for i in range(len(df)):
        d = dif.iloc[i]
        e = dea.iloc[i]
        m = macd.iloc[i]
        if pd.isna(d) or pd.isna(e) or pd.isna(m):
            continue
        out.append(MacdPoint(
            time=int(df["time"].iloc[i]),
            dif=float(d),
            dea=float(e),
            macd=float(m),
        ))
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