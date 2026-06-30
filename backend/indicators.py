"""
技术指标计算模块
================
Bollinger Bands (布林带):
  - 中轨 (MID) = N 周期收盘价 SMA
  - 上轨 (UPPER) = MID + K * N 周期标准差
  - 下轨 (LOWER) = MID - K * N 周期标准差
  - 默认 N=20, K=2
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np
import pandas as pd


# --------- 数据结构 ---------
@dataclass
class BollPoint:
    """单根 K 线对应的布林带数值。"""
    time: int            # K 线开盘时间戳(秒)
    open: float
    high: float
    low: float
    close: float
    volume: float
    mid: float           # 中轨
    upper: float         # 上轨
    lower: float         # 下轨
    width: float         # 带宽 (upper - lower) / mid


# --------- 工具函数 ---------
def _to_dataframe(klines: Sequence[Sequence]) -> pd.DataFrame:
    """把 K 线原始数据(列表形式)统一转成 DataFrame。

    期望列: [open_time, open, high, low, close, volume, ...]
    接受任何包含这些字段的 list/tuple，按位置截取。
    """
    if not klines:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])
    rows = []
    for k in klines:
        rows.append(
            {
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]) if len(k) > 5 else 0.0,
            }
        )
    return pd.DataFrame(rows)


# --------- BOLL 计算 ---------
def compute_boll(
    klines: Sequence[Sequence],
    period: int = 20,
    std_mult: float = 2.0,
) -> List[BollPoint]:
    """计算布林带指标。

    Parameters
    ----------
    klines : raw kline rows
        每行至少包含 [open_time, open, high, low, close, volume]
    period : int
        移动平均周期，默认 20
    std_mult : float
        标准差倍数，默认 2.0
    """
    df = _to_dataframe(klines)
    if df.empty:
        return []

    close = df["close"].astype(float)
    mid = close.rolling(window=period, min_periods=period).mean()
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = mid + std_mult * std
    lower = mid - std_mult * std

    points: List[BollPoint] = []
    for i in range(len(df)):
        m = mid.iloc[i]
        u = upper.iloc[i]
        l = lower.iloc[i]
        if pd.isna(m) or pd.isna(u) or pd.isna(l):
            # 未达到 period 周期就跳过
            continue
        width = (u - l) / m if m else 0.0
        points.append(
            BollPoint(
                time=int(df["open_time"].iloc[i]),
                open=float(df["open"].iloc[i]),
                high=float(df["high"].iloc[i]),
                low=float(df["low"].iloc[i]),
                close=float(df["close"].iloc[i]),
                volume=float(df["volume"].iloc[i]),
                mid=float(m),
                upper=float(u),
                lower=float(l),
                width=float(width),
            )
        )
    return points


def boll_to_dicts(points: List[BollPoint]) -> List[dict]:
    """BollPoint -> dict 列表，方便 JSON 序列化。"""
    return [
        {
            "time": p.time,
            "open": p.open,
            "high": p.high,
            "low": p.low,
            "close": p.close,
            "volume": p.volume,
            "mid": p.mid,
            "upper": p.upper,
            "lower": p.lower,
            "width": p.width,
        }
        for p in points
    ]
