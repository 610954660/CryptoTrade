"""
指标公共基础
============
- KLineBar: 一根 K 线的结构 (list->KLineBar 转换)
- IndicatorSet: 一次扫描算完所有指标, 给 matcher 用
- to_dataframe: 列表转 pandas DataFrame
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import pandas as pd


@dataclass
class KLineBar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class MaPoint:
    time: int
    ma5: float
    ma10: float
    ma20: float
    ma60: float


@dataclass
class MacdPoint:
    time: int
    dif: float
    dea: float
    macd: float  # 柱状 (dif - dea) * 2


@dataclass
class RsiPoint:
    time: int
    rsi: float


@dataclass
class KdjPoint:
    time: int
    k: float
    d: float
    j: float


@dataclass
class VolPoint:
    time: int
    volume: float
    avg_vol_5: float
    avg_vol_20: float


@dataclass
class PricePoint:
    time: int
    open: float
    high: float
    low: float
    close: float
    pct_change: float  # 涨跌幅 % (相对前一根)


@dataclass
class IndicatorSet:
    """所有指标的集合, 给 matcher 一次性查。"""
    bars: List[KLineBar] = None
    boll: list = None           # List[BollPoint]
    ma: List[MaPoint] = None
    macd: List[MacdPoint] = None
    rsi: List[RsiPoint] = None
    kdj: List[KdjPoint] = None
    vol: List[VolPoint] = None
    price: List[PricePoint] = None


def to_dataframe(klines: Sequence[Sequence]) -> pd.DataFrame:
    """把 K 线原始数据统一转 DataFrame。"""
    if not klines:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    rows = []
    for k in klines:
        rows.append({
            "time":   int(k[0]),
            "open":   float(k[1]),
            "high":   float(k[2]),
            "low":    float(k[3]),
            "close":  float(k[4]),
            "volume": float(k[5]) if len(k) > 5 else 0.0,
        })
    return pd.DataFrame(rows)


def to_indicator_set(klines: Sequence[Sequence]) -> List[KLineBar]:
    """列表 -> KLineBar 列表 (按时间升序)。"""
    bars: List[KLineBar] = []
    for k in klines:
        bars.append(KLineBar(
            time=int(k[0]),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]) if len(k) > 5 else 0.0,
        ))
    return bars
