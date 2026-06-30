"""
BOLL 形态匹配器
===============
支持的形态:
  - boll_open          : 布林带开口 (带宽扩张, 趋势启动信号)
  - cross_mid_up       : 收盘价由下方穿越中轨向上
  - cross_mid_down     : 收盘价由上方穿越中轨向下
  - upper_breakout     : 突破上轨
  - lower_breakout     : 跌破下轨
  - mid_trend_up       : 中轨本身趋势向上 (近 N 根中轨斜率为正)
  - mid_trend_down     : 中轨本身趋势向下
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from indicators import BollPoint


# --------- 形态定义 ---------
PATTERN_BOLL_OPEN = "boll_open"
PATTERN_CROSS_MID_UP = "cross_mid_up"
PATTERN_CROSS_MID_DOWN = "cross_mid_down"
PATTERN_UPPER_BREAKOUT = "upper_breakout"
PATTERN_LOWER_BREAKOUT = "lower_breakout"
PATTERN_MID_TREND_UP = "mid_trend_up"
PATTERN_MID_TREND_DOWN = "mid_trend_down"


@dataclass
class MatchResult:
    """单标的单周期的形态匹配结果。"""
    matched: bool
    pattern: str
    detail: str = ""
    last_close: float = 0.0
    last_mid: float = 0.0
    last_upper: float = 0.0
    last_lower: float = 0.0
    last_width: float = 0.0
    width_change_pct: float = 0.0  # 带宽变化百分比


# --------- 形态检测 ---------
def _is_boll_open(points: List[BollPoint], lookback: int = 5, expand_ratio: float = 1.05) -> MatchResult:
    """布林带开口: 最近一根 K 线的带宽比 lookback 根之前扩大 >= expand_ratio 倍。

    这是趋势启动的典型信号 (波动率从低位开始扩张)。
    """
    if len(points) < lookback + 1:
        return MatchResult(False, PATTERN_BOLL_OPEN, detail="数据不足")
    cur = points[-1]
    prev = points[-lookback - 1]
    if prev.width <= 0:
        return MatchResult(False, PATTERN_BOLL_OPEN, detail="历史带宽为 0")
    change = cur.width / prev.width
    matched = change >= expand_ratio
    return MatchResult(
        matched=matched,
        pattern=PATTERN_BOLL_OPEN,
        detail=f"带宽从 {prev.width:.4f} → {cur.width:.4f} (×{change:.2f})",
        last_close=cur.close,
        last_mid=cur.mid,
        last_upper=cur.upper,
        last_lower=cur.lower,
        last_width=cur.width,
        width_change_pct=(change - 1) * 100,
    )


def _cross_mid(points: List[BollPoint], direction: str) -> MatchResult:
    """价格穿越中轨。

    direction: "up" 表示由下穿上; "down" 表示由上穿下。
    判定: 比较最近一根 K 线和前一根 K 线的 close 与中轨位置关系。
    """
    if len(points) < 2:
        return MatchResult(False, PATTERN_CROSS_MID_UP if direction == "up" else PATTERN_CROSS_MID_DOWN,
                           detail="数据不足")
    cur, prev = points[-1], points[-2]
    if direction == "up":
        matched = prev.close <= prev.mid and cur.close > cur.mid
        detail = f"close {prev.close:.4f}≤MID {prev.mid:.4f} → close {cur.close:.4f}>{cur.mid:.4f}"
        pattern = PATTERN_CROSS_MID_UP
    else:
        matched = prev.close >= prev.mid and cur.close < cur.mid
        detail = f"close {prev.close:.4f}≥MID {prev.mid:.4f} → close {cur.close:.4f}<{cur.mid:.4f}"
        pattern = PATTERN_CROSS_MID_DOWN
    return MatchResult(
        matched=matched,
        pattern=pattern,
        detail=detail,
        last_close=cur.close,
        last_mid=cur.mid,
        last_upper=cur.upper,
        last_lower=cur.lower,
        last_width=cur.width,
    )


def _mid_trend(
    points: List[BollPoint],
    direction: str,
    lookback: int = 3,
    min_pct: float = 0.001,
) -> MatchResult:
    """中轨本身趋势。

    direction: "up" 表示中轨上行; "down" 表示中轨下行。
    lookback : 往前看几根 K 线比较 (默认 3, 之前默认 5 对日线太严格)
    min_pct  : 中轨变化最小百分比阈值 (默认 0.1%), 过滤横盘噪音

    命中条件: cur.mid vs prev.mid 方向符合 且 |变化| >= min_pct
    """
    if len(points) < lookback + 1:
        return MatchResult(False, PATTERN_MID_TREND_UP if direction == "up" else PATTERN_MID_TREND_DOWN,
                           detail="数据不足")
    cur = points[-1]
    prev = points[-lookback - 1]
    if prev.mid == 0:
        return MatchResult(False,
                           PATTERN_MID_TREND_UP if direction == "up" else PATTERN_MID_TREND_DOWN,
                           detail="中轨基线为 0")
    change_pct = (cur.mid - prev.mid) / prev.mid
    if direction == "up":
        matched = change_pct > min_pct
        detail = f"MID {prev.mid:.4f} → {cur.mid:.4f} ({change_pct*100:+.2f}% / {lookback} 根, 阈值 {min_pct*100:.2f}%)"
        pattern = PATTERN_MID_TREND_UP
    else:
        matched = change_pct < -min_pct
        detail = f"MID {prev.mid:.4f} → {cur.mid:.4f} ({change_pct*100:+.2f}% / {lookback} 根, 阈值 {min_pct*100:.2f}%)"
        pattern = PATTERN_MID_TREND_DOWN
    return MatchResult(
        matched=matched,
        pattern=pattern,
        detail=detail,
        last_close=cur.close,
        last_mid=cur.mid,
        last_upper=cur.upper,
        last_lower=cur.lower,
        last_width=cur.width,
        width_change_pct=change_pct * 100,
    )


def _band_breakout(points: List[BollPoint], direction: str) -> MatchResult:
    """突破/跌破布林带轨道。"""
    if not points:
        return MatchResult(False,
                           PATTERN_UPPER_BREAKOUT if direction == "up" else PATTERN_LOWER_BREAKOUT,
                           detail="数据不足")
    cur = points[-1]
    if direction == "up":
        matched = cur.close > cur.upper
        detail = f"close {cur.close:.4f} > UPPER {cur.upper:.4f}"
        pattern = PATTERN_UPPER_BREAKOUT
    else:
        matched = cur.close < cur.lower
        detail = f"close {cur.close:.4f} < LOWER {cur.lower:.4f}"
        pattern = PATTERN_LOWER_BREAKOUT
    return MatchResult(
        matched=matched,
        pattern=pattern,
        detail=detail,
        last_close=cur.close,
        last_mid=cur.mid,
        last_upper=cur.upper,
        last_lower=cur.lower,
        last_width=cur.width,
    )


# --------- 统一入口 ---------
PATTERN_HANDLERS = {
    PATTERN_BOLL_OPEN: lambda pts: _is_boll_open(pts),
    PATTERN_CROSS_MID_UP: lambda pts: _cross_mid(pts, "up"),
    PATTERN_CROSS_MID_DOWN: lambda pts: _cross_mid(pts, "down"),
    PATTERN_UPPER_BREAKOUT: lambda pts: _band_breakout(pts, "up"),
    PATTERN_LOWER_BREAKOUT: lambda pts: _band_breakout(pts, "down"),
    PATTERN_MID_TREND_UP: lambda pts: _mid_trend(pts, "up"),
    PATTERN_MID_TREND_DOWN: lambda pts: _mid_trend(pts, "down"),
}


PATTERN_LABELS = {
    PATTERN_BOLL_OPEN: "BOLL 开口",
    PATTERN_CROSS_MID_UP: "向上穿越中轨",
    PATTERN_CROSS_MID_DOWN: "向下穿越中轨",
    PATTERN_UPPER_BREAKOUT: "突破上轨",
    PATTERN_LOWER_BREAKOUT: "跌破下轨",
    PATTERN_MID_TREND_UP: "中轨上行",
    PATTERN_MID_TREND_DOWN: "中轨下行",
}


def match_pattern(pattern: str, points: List[BollPoint]) -> MatchResult:
    """根据形态名称匹配，传入已计算 BOLL 的 K 线序列。"""
    handler = PATTERN_HANDLERS.get(pattern)
    if handler is None:
        return MatchResult(False, pattern, detail=f"未知形态: {pattern}")
    return handler(points)


# --------- 多周期联合判断 ---------
def match_multi_timeframe(
    per_interval_results: dict,  # {interval: MatchResult}
    require_all: bool = True,
) -> bool:
    """多周期联合判断: 默认要求所有周期都匹配 (AND), 可改为 OR。

    per_interval_results: { "15m": MatchResult, "30m": MatchResult, ... }
    """
    if not per_interval_results:
        return False
    flags = [r.matched for r in per_interval_results.values()]
    return all(flags) if require_all else any(flags)
