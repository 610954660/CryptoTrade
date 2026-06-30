"""
形态匹配器 (多指标)
==================
支持指标: boll / ma / macd / rsi / kdj / vol / price

单条规则 schema:
  {
    interval:    '1d',                # 时间周期
    indicator:   'boll',              # 指标 key
    pattern:     'cross_mid_up',      # 形态 key
    value:       None,                # 数值 (value_required 时填)
    lookback:    200,                 # 查询 K 线数 (回看窗口)
    match_count: 1,                   # 在窗口内需要多少根 K 线匹配
  }

匹配模式:
  - match_count = 1 (默认): 只看最后一根 (most recent bar)
  - match_count > 1: 在最近 lookback 根里, 累计有 match_count 根满足
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from indicators import (
    BollPoint,
    MaPoint,
    MacdPoint,
    RsiPoint,
    KdjPoint,
    VolPoint,
    PricePoint,
    IndicatorSet,
)


# --------- 形态 key 集中定义 (避免散落字符串) ---------
# BOLL
P_BOLL_OPEN          = "boll_open"
P_CROSS_MID_UP       = "cross_mid_up"
P_CROSS_MID_DOWN     = "cross_mid_down"
P_UPPER_BREAKOUT     = "upper_breakout"
P_LOWER_BREAKOUT     = "lower_breakout"
P_MID_TREND_UP       = "mid_trend_up"
P_MID_TREND_DOWN     = "mid_trend_down"
P_BOLL_SQUEEZE       = "boll_squeeze"

# 兼容旧 BOLL 形态 -> 中文标签
PATTERN_LABELS = {
    P_BOLL_OPEN:        "BOLL 开口",
    P_CROSS_MID_UP:     "向上穿越中轨",
    P_CROSS_MID_DOWN:   "向下穿越中轨",
    P_UPPER_BREAKOUT:   "突破上轨",
    P_LOWER_BREAKOUT:   "跌破下轨",
    P_MID_TREND_UP:     "中轨上行",
    P_MID_TREND_DOWN:   "中轨下行",
    P_BOLL_SQUEEZE:     "BOLL 收窄",
}
# 兼容旧常量名
PATTERN_BOLL_OPEN       = P_BOLL_OPEN
PATTERN_CROSS_MID_UP    = P_CROSS_MID_UP
PATTERN_CROSS_MID_DOWN  = P_CROSS_MID_DOWN
PATTERN_UPPER_BREAKOUT  = P_UPPER_BREAKOUT
PATTERN_LOWER_BREAKOUT  = P_LOWER_BREAKOUT
PATTERN_MID_TREND_UP    = P_MID_TREND_UP
PATTERN_MID_TREND_DOWN  = P_MID_TREND_DOWN

# MA
P_PRICE_ABOVE_MA5    = "price_above_ma5"
P_PRICE_BELOW_MA5    = "price_below_ma5"
P_PRICE_ABOVE_MA20   = "price_above_ma20"
P_PRICE_BELOW_MA20   = "price_below_ma20"
P_MA5_ABOVE_MA20     = "ma5_above_ma20"
P_MA5_BELOW_MA20     = "ma5_below_ma20"
P_MA20_ABOVE_MA60    = "ma20_above_ma60"
P_MA20_BELOW_MA60    = "ma20_below_ma60"
P_BULL_ALIGN         = "bull_alignment"
P_BEAR_ALIGN         = "bear_alignment"

# MACD
P_MACD_GOLDEN        = "golden_cross"
P_MACD_DEATH         = "death_cross"
P_DIF_ABOVE_ZERO     = "dif_above_zero"
P_DIF_BELOW_ZERO     = "dif_below_zero"
P_MACD_POSITIVE      = "macd_positive"
P_MACD_NEGATIVE      = "macd_negative"
P_MACD_TOP_DIV       = "macd_top_div"
P_MACD_BOT_DIV       = "macd_bot_div"

# RSI
P_RSI_ABOVE_70       = "rsi_above_70"
P_RSI_BELOW_30       = "rsi_below_30"
P_RSI_ABOVE          = "rsi_above"
P_RSI_BELOW          = "rsi_below"
P_RSI_UP_THROUGH     = "rsi_up_through"
P_RSI_DN_THROUGH     = "rsi_dn_through"

# KDJ
P_KDJ_GOLDEN         = "kdj_golden"
P_KDJ_DEATH          = "kdj_death"
P_J_ABOVE_100        = "j_above_100"
P_J_BELOW_0          = "j_below_0"
P_KDJ_ABOVE          = "kdj_above"
P_KDJ_BELOW          = "kdj_below"

# VOL
P_VOL_ABOVE          = "vol_above"
P_VOL_BELOW          = "vol_below"
P_VOL_BREAKOUT       = "vol_breakout"
P_VOL_SHRINK         = "vol_shrink"

# PRICE
P_PCT_UP             = "pct_up"
P_PCT_DOWN           = "pct_down"
P_CLOSE_ABOVE        = "close_above"
P_CLOSE_BELOW        = "close_below"
P_NEW_HIGH           = "new_high"
P_NEW_LOW            = "new_low"


# --------- 匹配结果 ---------
@dataclass
class MatchResult:
    """单条规则的匹配结果 (给 service 序列化到 SSE/JSON)。"""
    matched: bool
    pattern: str
    indicator: str = ""
    detail: str = ""
    matched_count: int = 0          # 窗口里实际匹配数
    required_count: int = 1         # 需要多少根
    lookback: int = 200
    last_close: float = 0.0
    extra: dict = field(default_factory=dict)


# =========================================================================
# BOLL 形态
# =========================================================================
def _boll_width(p: BollPoint) -> float:
    return p.upper - p.lower


def _m_boll_open(points: List[BollPoint], lookback: int, match_count: int) -> MatchResult:
    """带宽从 lookback 前到当前扩张 >= 1.05x。"""
    if len(points) < 2:
        return MatchResult(False, P_BOLL_OPEN, "boll", "数据不足")
    cur_w = _boll_width(points[-1])
    if len(points) < lookback + 1:
        return MatchResult(False, P_BOLL_OPEN, "boll",
                          f"数据不足 (需要 {lookback+1} 根, 实际 {len(points)})")
    prev_w = _boll_width(points[-lookback - 1])
    if prev_w <= 0:
        return MatchResult(False, P_BOLL_OPEN, "boll", "历史带宽为 0")
    change = cur_w / prev_w
    matched = change >= 1.05
    return MatchResult(
        matched=matched,
        pattern=P_BOLL_OPEN,
        indicator="boll",
        matched_count=1 if matched else 0,
        required_count=match_count,
        lookback=lookback,
        last_close=0,
        detail=f"带宽 {prev_w:.4f} → {cur_w:.4f} (×{change:.2f})",
        extra={"width_change_pct": (change - 1) * 100},
    )


def _m_boll_squeeze(points: List[BollPoint], lookback: int, match_count: int) -> MatchResult:
    """带宽收窄: 当前带宽 / lookback 根前 <= 0.9。"""
    if len(points) < lookback + 1:
        return MatchResult(False, P_BOLL_SQUEEZE, "boll", "数据不足")
    cur_w = _boll_width(points[-1])
    prev_w = _boll_width(points[-lookback - 1])
    if prev_w <= 0:
        return MatchResult(False, P_BOLL_SQUEEZE, "boll", "历史带宽为 0")
    change = cur_w / prev_w
    matched = change <= 0.9
    return MatchResult(
        matched=matched, pattern=P_BOLL_SQUEEZE, indicator="boll",
        matched_count=1 if matched else 0, required_count=match_count,
        lookback=lookback, last_close=0,
        detail=f"带宽 {prev_w:.4f} → {cur_w:.4f} (×{change:.2f})",
    )


def _m_cross_mid(points: List[BollPoint], bars, direction: str, lookback: int, match_count: int) -> MatchResult:
    """最近一根从下方/上方穿越中轨。"""
    pattern = P_CROSS_MID_UP if direction == "up" else P_CROSS_MID_DOWN
    if len(points) < 2 or not bars or len(bars) < 2:
        return MatchResult(False, pattern, "boll", "数据不足")
    # 通过时间戳把 BOLL 点对齐到 bar
    bar_by_time = {b.time: b for b in bars}
    cur_b = bar_by_time.get(points[-1].time)
    prev_b = bar_by_time.get(points[-2].time)
    if cur_b is None or prev_b is None:
        return MatchResult(False, pattern, "boll", "BOLL/Bar 时间未对齐")
    cur, prev = points[-1], points[-2]
    if direction == "up":
        ok = prev_b.close <= prev.mid and cur_b.close > cur.mid
        det = f"close {prev_b.close:.4f}≤MID {prev.mid:.4f} → {cur_b.close:.4f}>{cur.mid:.4f}"
    else:
        ok = prev_b.close >= prev.mid and cur_b.close < cur.mid
        det = f"close {prev_b.close:.4f}≥MID {prev.mid:.4f} → {cur_b.close:.4f}<{cur.mid:.4f}"
    return MatchResult(
        matched=ok, pattern=pattern, indicator="boll",
        matched_count=1 if ok else 0, required_count=match_count,
        lookback=lookback, last_close=cur_b.close,
        detail=det,
    )


def _m_band_break(points: List[BollPoint], bars, direction: str, lookback: int, match_count: int) -> MatchResult:
    pattern = P_UPPER_BREAKOUT if direction == "up" else P_LOWER_BREAKOUT
    if not points or not bars:
        return MatchResult(False, pattern, "boll", "数据不足")
    cur = points[-1]
    cur_b = next((b for b in bars if b.time == cur.time), None)
    if cur_b is None:
        return MatchResult(False, pattern, "boll", "BOLL/Bar 时间未对齐")
    if direction == "up":
        ok = cur_b.close > cur.upper
        det = f"close {cur_b.close:.4f} > UPPER {cur.upper:.4f}"
    else:
        ok = cur_b.close < cur.lower
        det = f"close {cur_b.close:.4f} < LOWER {cur.lower:.4f}"
    return MatchResult(
        matched=ok, pattern=pattern, indicator="boll",
        matched_count=1 if ok else 0, required_count=match_count,
        lookback=lookback, last_close=cur_b.close,
        detail=det,
    )


def _m_mid_trend(points: List[BollPoint], direction: str, lookback: int, match_count: int) -> MatchResult:
    pattern = P_MID_TREND_UP if direction == "up" else P_MID_TREND_DOWN
    span = min(lookback, len(points) - 1)
    if span < 2:
        return MatchResult(False, pattern, "boll", "数据不足")
    cur = points[-1]
    prev = points[-1 - span]
    if prev.mid == 0:
        return MatchResult(False, pattern, "boll", "中轨基线为 0")
    pct = (cur.mid - prev.mid) / prev.mid
    if direction == "up":
        ok = pct > 0.001
    else:
        ok = pct < -0.001
    sign = "+" if pct >= 0 else ""
    return MatchResult(
        matched=ok, pattern=pattern, indicator="boll",
        matched_count=1 if ok else 0, required_count=match_count,
        lookback=lookback, last_close=0,
        detail=f"MID {prev.mid:.4f} → {cur.mid:.4f} ({sign}{pct*100:.2f}% / {span} 根)",
    )


# =========================================================================
# MA 形态
# =========================================================================
def _ma_bar_above(ma: List[MaPoint], bars: list, threshold_key: str, lookback: int, match_count: int, pattern: str) -> MatchResult:
    """价格在 ma{X} 上方/下方: 在最近 lookback 根里数, match_count 根满足条件。"""
    if not ma or not bars:
        return MatchResult(False, pattern, "ma", "数据不足")
    # 取最后 lookback 根
    span = min(lookback, len(ma), len(bars))
    if span <= 0:
        return MatchResult(False, pattern, "ma", "数据不足")
    above = (threshold_key == "above")
    matched = 0
    for i in range(1, span + 1):
        m = ma[-i]
        b = bars[-i]
        v = getattr(m, threshold_key, None)  # ma5/ma10/ma20/ma60
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        if above and b.close > v:
            matched += 1
        elif (not above) and b.close < v:
            matched += 1
    ok = matched >= match_count
    cur_close = bars[-1].close
    cur_ma = getattr(ma[-1], threshold_key, float("nan"))
    return MatchResult(
        matched=ok, pattern=pattern, indicator="ma",
        matched_count=matched, required_count=match_count,
        lookback=lookback, last_close=cur_close,
        detail=f"{matched}/{match_count} 根 close 在 {threshold_key}({cur_ma:.4f}) {'上方' if above else '下方'}",
    )


def _ma_cross(ma: List[MaPoint], fast: str, slow: str, direction: str, lookback: int, match_count: int, pattern: str) -> MatchResult:
    """ma_fast 上穿/下穿 ma_slow: 最近一根发生穿越。"""
    if len(ma) < 2:
        return MatchResult(False, pattern, "ma", "数据不足")
    cur, prev = ma[-1], ma[-2]
    f, s = getattr(cur, fast), getattr(cur, slow)
    pf, ps = getattr(prev, fast), getattr(prev, slow)
    if any(math.isnan(v) for v in (f, s, pf, ps)):
        return MatchResult(False, pattern, "ma", "均线未成型")
    if direction == "up":
        ok = pf <= ps and f > s
    else:
        ok = pf >= ps and f < s
    return MatchResult(
        matched=ok, pattern=pattern, indicator="ma",
        matched_count=1 if ok else 0, required_count=match_count,
        lookback=lookback, detail=f"{fast}({f:.4f}) {'上穿' if direction=='up' else '下穿'} {slow}({s:.4f})",
    )


def _ma_alignment(ma: List[MaPoint], bull: bool, lookback: int, match_count: int, pattern: str) -> MatchResult:
    """多头/空头排列: 看最近一根。"""
    if not ma:
        return MatchResult(False, pattern, "ma", "数据不足")
    m = ma[-1]
    vals = [m.ma5, m.ma10, m.ma20, m.ma60]
    if any(math.isnan(v) for v in vals):
        return MatchResult(False, pattern, "ma", "均线未成型")
    if bull:
        ok = m.ma5 > m.ma10 > m.ma20 > m.ma60
        det = f"MA5({m.ma5:.4f})>MA10>MA20>MA60({m.ma60:.4f})"
    else:
        ok = m.ma5 < m.ma10 < m.ma20 < m.ma60
        det = f"MA5({m.ma5:.4f})<MA10<MA20<MA60({m.ma60:.4f})"
    return MatchResult(
        matched=ok, pattern=pattern, indicator="ma",
        matched_count=1 if ok else 0, required_count=match_count,
        lookback=lookback, detail=det,
    )


# =========================================================================
# MACD 形态
# =========================================================================
def _macd_cross(macd: List[MacdPoint], direction: str, lookback: int, match_count: int, pattern: str) -> MatchResult:
    if len(macd) < 2:
        return MatchResult(False, pattern, "macd", "数据不足")
    cur, prev = macd[-1], macd[-2]
    if direction == "up":
        ok = prev.dif <= prev.dea and cur.dif > cur.dea
        det = f"DIF {prev.dif:.4f}≤DEA {prev.dea:.4f} → {cur.dif:.4f}>{cur.dea:.4f}"
    else:
        ok = prev.dif >= prev.dea and cur.dif < cur.dea
        det = f"DIF {prev.dif:.4f}≥DEA {prev.dea:.4f} → {cur.dif:.4f}<{cur.dea:.4f}"
    return MatchResult(
        matched=ok, pattern=pattern, indicator="macd",
        matched_count=1 if ok else 0, required_count=match_count,
        lookback=lookback, detail=det,
    )


def _macd_zone(macd: List[MacdPoint], above: bool, lookback: int, match_count: int, pattern: str) -> MatchResult:
    if not macd:
        return MatchResult(False, pattern, "macd", "数据不足")
    span = min(lookback, len(macd))
    matched = sum(1 for i in range(1, span + 1) if (macd[-i].dif >= 0) == above)
    ok = matched >= match_count
    cur = macd[-1]
    return MatchResult(
        matched=ok, pattern=pattern, indicator="macd",
        matched_count=matched, required_count=match_count,
        lookback=lookback,
        detail=f"{matched}/{match_count} 根 DIF 在零轴{'上' if above else '下'} (当前 DIF={cur.dif:.4f})",
    )


def _macd_bar_sign(macd: List[MacdPoint], positive: bool, lookback: int, match_count: int, pattern: str) -> MatchResult:
    if len(macd) < 2:
        return MatchResult(False, pattern, "macd", "数据不足")
    cur, prev = macd[-1], macd[-2]
    if positive:
        ok = prev.macd <= 0 and cur.macd > 0
        det = f"MACD 柱 {prev.macd:.4f}≤0 → {cur.macd:.4f}>0"
    else:
        ok = prev.macd >= 0 and cur.macd < 0
        det = f"MACD 柱 {prev.macd:.4f}≥0 → {cur.macd:.4f}<0"
    return MatchResult(
        matched=ok, pattern=pattern, indicator="macd",
        matched_count=1 if ok else 0, required_count=match_count,
        lookback=lookback, detail=det,
    )


# =========================================================================
# RSI 形态
# =========================================================================
def _rsi_threshold(rsi: List[RsiPoint], above: bool, threshold: float, lookback: int, match_count: int, pattern: str) -> MatchResult:
    if not rsi:
        return MatchResult(False, pattern, "rsi", "数据不足")
    span = min(lookback, len(rsi))
    matched = sum(1 for i in range(1, span + 1) if (rsi[-i].rsi > threshold) == above)
    ok = matched >= match_count
    cur = rsi[-1].rsi
    return MatchResult(
        matched=ok, pattern=pattern, indicator="rsi",
        matched_count=matched, required_count=match_count,
        lookback=lookback, last_close=0,
        detail=f"{matched}/{match_count} 根 RSI {'>' if above else '<'} {threshold} (当前 {cur:.2f})",
    )


def _rsi_cross(rsi: List[RsiPoint], threshold: float, direction: str, lookback: int, match_count: int, pattern: str) -> MatchResult:
    if len(rsi) < 2:
        return MatchResult(False, pattern, "rsi", "数据不足")
    cur, prev = rsi[-1], rsi[-2]
    if direction == "up":
        ok = prev.rsi <= threshold and cur.rsi > threshold
        det = f"RSI {prev.rsi:.2f}≤{threshold} → {cur.rsi:.2f}>{threshold}"
    else:
        ok = prev.rsi >= threshold and cur.rsi < threshold
        det = f"RSI {prev.rsi:.2f}≥{threshold} → {cur.rsi:.2f}<{threshold}"
    return MatchResult(
        matched=ok, pattern=pattern, indicator="rsi",
        matched_count=1 if ok else 0, required_count=match_count,
        lookback=lookback, detail=det,
    )


# =========================================================================
# KDJ 形态
# =========================================================================
def _kdj_cross(kdj: List[KdjPoint], direction: str, lookback: int, match_count: int, pattern: str) -> MatchResult:
    if len(kdj) < 2:
        return MatchResult(False, pattern, "kdj", "数据不足")
    cur, prev = kdj[-1], kdj[-2]
    if direction == "up":
        ok = prev.k <= prev.d and cur.k > cur.d
        det = f"K {prev.k:.2f}≤D {prev.d:.2f} → {cur.k:.2f}>{cur.d:.2f}"
    else:
        ok = prev.k >= prev.d and cur.k < cur.d
        det = f"K {prev.k:.2f}≥D {prev.d:.2f} → {cur.k:.2f}<{cur.d:.2f}"
    return MatchResult(
        matched=ok, pattern=pattern, indicator="kdj",
        matched_count=1 if ok else 0, required_count=match_count,
        lookback=lookback, detail=det,
    )


def _kdj_zone(kdj: List[KdjPoint], field_name: str, above: bool, threshold: float, lookback: int, match_count: int, pattern: str) -> MatchResult:
    if not kdj:
        return MatchResult(False, pattern, "kdj", "数据不足")
    span = min(lookback, len(kdj))
    def getter(p):
        if field_name == "j": return p.j
        if field_name == "k": return p.k
        return p.d
    matched = sum(1 for i in range(1, span + 1) if (getter(kdj[-i]) > threshold) == above)
    ok = matched >= match_count
    cur = getter(kdj[-1])
    return MatchResult(
        matched=ok, pattern=pattern, indicator="kdj",
        matched_count=matched, required_count=match_count,
        lookback=lookback,
        detail=f"{matched}/{match_count} 根 {field_name.upper()} {'>' if above else '<'} {threshold} (当前 {cur:.2f})",
    )


# =========================================================================
# 成交量 形态
# =========================================================================
def _vol_compare(vol: List[VolPoint], times: float, above: bool, lookback: int, match_count: int, pattern: str) -> MatchResult:
    """成交量 > N 倍 5日均量 (above=True) 或 < N 倍。"""
    if not vol:
        return MatchResult(False, pattern, "vol", "数据不足")
    span = min(lookback, len(vol))
    matched = 0
    for i in range(1, span + 1):
        v = vol[-i]
        if math.isnan(v.avg_vol_5) or v.avg_vol_5 <= 0:
            continue
        ratio = v.volume / v.avg_vol_5
        if above and ratio > times:
            matched += 1
        elif (not above) and ratio < times:
            matched += 1
    ok = matched >= match_count
    cur = vol[-1]
    cur_ratio = cur.volume / cur.avg_vol_5 if not math.isnan(cur.avg_vol_5) and cur.avg_vol_5 > 0 else 0
    return MatchResult(
        matched=ok, pattern=pattern, indicator="vol",
        matched_count=matched, required_count=match_count,
        lookback=lookback,
        detail=f"{matched}/{match_count} 根 vol/avg5 {'>' if above else '<'} {times} (当前 ×{cur_ratio:.2f})",
    )


def _vol_breakout(vol: List[VolPoint], lookback: int, match_count: int, pattern: str) -> MatchResult:
    """成交量 > 20日均量。"""
    if not vol:
        return MatchResult(False, pattern, "vol", "数据不足")
    span = min(lookback, len(vol))
    matched = sum(1 for i in range(1, span + 1) if vol[-i].volume > vol[-i].avg_vol_20)
    ok = matched >= match_count
    cur = vol[-1]
    return MatchResult(
        matched=ok, pattern=pattern, indicator="vol",
        matched_count=matched, required_count=match_count,
        lookback=lookback,
        detail=f"{matched}/{match_count} 根 vol > 20日均量",
    )


def _vol_shrink(vol: List[VolPoint], lookback: int, match_count: int, pattern: str) -> MatchResult:
    """成交量 < 0.5x 5日均量。"""
    if not vol:
        return MatchResult(False, pattern, "vol", "数据不足")
    span = min(lookback, len(vol))
    matched = sum(1 for i in range(1, span + 1)
                  if vol[-i].avg_vol_5 > 0 and vol[-i].volume < 0.5 * vol[-i].avg_vol_5)
    ok = matched >= match_count
    return MatchResult(
        matched=ok, pattern=pattern, indicator="vol",
        matched_count=matched, required_count=match_count,
        lookback=lookback,
        detail=f"{matched}/{match_count} 根 vol < 0.5x 5日均量",
    )


# =========================================================================
# 价格 / 涨跌幅 形态
# =========================================================================
def _pct_compare(price: List[PricePoint], threshold: float, up: bool, lookback: int, match_count: int, pattern: str) -> MatchResult:
    """涨/跌幅 > 阈值%。 threshold 单位是百分比 (5 表示 5%)。"""
    if not price:
        return MatchResult(False, pattern, "price", "数据不足")
    span = min(lookback, len(price))
    matched = 0
    for i in range(1, span + 1):
        p = price[-i]
        if math.isnan(p.pct_change):
            continue
        if up and p.pct_change > threshold:
            matched += 1
        elif (not up) and p.pct_change < -threshold:
            matched += 1
    ok = matched >= match_count
    cur = price[-1]
    return MatchResult(
        matched=ok, pattern=pattern, indicator="price",
        matched_count=matched, required_count=match_count,
        lookback=lookback, last_close=cur.close,
        detail=f"{matched}/{match_count} 根 {'涨' if up else '跌'}幅 {'>' if up else '<'} {threshold}% (当前 {cur.pct_change:+.2f}%)",
    )


def _price_threshold(price: List[PricePoint], threshold: float, above: bool, lookback: int, match_count: int, pattern: str) -> MatchResult:
    if not price:
        return MatchResult(False, pattern, "price", "数据不足")
    span = min(lookback, len(price))
    matched = sum(1 for i in range(1, span + 1) if (price[-i].close > threshold) == above)
    ok = matched >= match_count
    cur = price[-1]
    return MatchResult(
        matched=ok, pattern=pattern, indicator="price",
        matched_count=matched, required_count=match_count,
        lookback=lookback, last_close=cur.close,
        detail=f"{matched}/{match_count} 根 close {'>' if above else '<'} {threshold} (当前 {cur.close:.4f})",
    )


def _new_high_low(price: List[PricePoint], n: int, high: bool, lookback: int, match_count: int, pattern: str) -> MatchResult:
    """创 N 周期新高/新低。"""
    if len(price) < n + 1:
        return MatchResult(False, pattern, "price", f"数据不足 (需 {n+1})")
    cur = price[-1]
    if high:
        prev_max = max(p.high for p in price[-n - 1:-1])
        ok = cur.high > prev_max
        det = f"high {cur.high:.4f} > {n}周期内最高 {prev_max:.4f}"
    else:
        prev_min = min(p.low for p in price[-n - 1:-1])
        ok = cur.low < prev_min
        det = f"low {cur.low:.4f} < {n}周期内最低 {prev_min:.4f}"
    return MatchResult(
        matched=ok, pattern=pattern, indicator="price",
        matched_count=1 if ok else 0, required_count=match_count,
        lookback=lookback, last_close=cur.close, detail=det,
    )


# =========================================================================
# 统一入口
# =========================================================================
def match_pattern(
    indicator: str,
    pattern: str,
    ind: IndicatorSet,
    value: Optional[float] = None,
    lookback: int = 200,
    match_count: int = 1,
) -> MatchResult:
    """按指标 + 形态名匹配。

    ind   : IndicatorSet (含 boll/ma/macd/rsi/kdj/vol/price)
    value : 数值阈值 (vol/price/rsi 阈值等使用)
    lookback / match_count : 来自规则

    mode  : state | cross | window
            - state  只看最后一根 (lookback/match_count 强制为 1)
            - cross  看最后两根 (穿越)
            - window 在最近 lookback 根里, match_count 根满足
    """
    # 按 mode 强制调整窗口参数, 避免 "J<0 当前=12 但 20 根里有 10 根<0" 的误命中
    try:
        from indicators.registry import get_pattern_mode
        mode = get_pattern_mode(indicator, pattern)
    except Exception:
        mode = "state"
    if mode in ("state", "cross"):
        lb = 1
        mc = 1
    else:
        lb = max(1, int(lookback or 1))
        mc = max(1, int(match_count or 1))

    if indicator == "boll":
        pts = ind.boll or []
        bars = ind.bars or []
        if pattern == P_BOLL_OPEN:        return _m_boll_open(pts, lb, mc)
        if pattern == P_BOLL_SQUEEZE:     return _m_boll_squeeze(pts, lb, mc)
        if pattern == P_CROSS_MID_UP:     return _m_cross_mid(pts, bars, "up", lb, mc)
        if pattern == P_CROSS_MID_DOWN:   return _m_cross_mid(pts, bars, "down", lb, mc)
        if pattern == P_UPPER_BREAKOUT:   return _m_band_break(pts, bars, "up", lb, mc)
        if pattern == P_LOWER_BREAKOUT:   return _m_band_break(pts, bars, "down", lb, mc)
        if pattern == P_MID_TREND_UP:     return _m_mid_trend(pts, "up", lb, mc)
        if pattern == P_MID_TREND_DOWN:   return _m_mid_trend(pts, "down", lb, mc)
        return MatchResult(False, pattern, "boll", f"未知 BOLL 形态: {pattern}")

    if indicator == "ma":
        ma = ind.ma or []
        bars = ind.bars or []
        if pattern == P_PRICE_ABOVE_MA5:  return _ma_bar_above(ma, bars, "ma5", lb, mc, pattern)
        if pattern == P_PRICE_BELOW_MA5:  return _ma_bar_above(ma, bars, "ma5", lb, mc, pattern)
        if pattern == P_PRICE_ABOVE_MA20: return _ma_bar_above(ma, bars, "ma20", lb, mc, pattern)
        if pattern == P_PRICE_BELOW_MA20: return _ma_bar_above(ma, bars, "ma20", lb, mc, pattern)
        if pattern == P_MA5_ABOVE_MA20:   return _ma_cross(ma, "ma5", "ma20", "up", lb, mc, pattern)
        if pattern == P_MA5_BELOW_MA20:   return _ma_cross(ma, "ma5", "ma20", "down", lb, mc, pattern)
        if pattern == P_MA20_ABOVE_MA60:  return _ma_cross(ma, "ma20", "ma60", "up", lb, mc, pattern)
        if pattern == P_MA20_BELOW_MA60:  return _ma_cross(ma, "ma20", "ma60", "down", lb, mc, pattern)
        if pattern == P_BULL_ALIGN:       return _ma_alignment(ma, True, lb, mc, pattern)
        if pattern == P_BEAR_ALIGN:       return _ma_alignment(ma, False, lb, mc, pattern)
        return MatchResult(False, pattern, "ma", f"未知 MA 形态: {pattern}")

    if indicator == "macd":
        m = ind.macd or []
        if pattern == P_MACD_GOLDEN:    return _macd_cross(m, "up", lb, mc, pattern)
        if pattern == P_MACD_DEATH:     return _macd_cross(m, "down", lb, mc, pattern)
        if pattern == P_DIF_ABOVE_ZERO: return _macd_zone(m, True, lb, mc, pattern)
        if pattern == P_DIF_BELOW_ZERO: return _macd_zone(m, False, lb, mc, pattern)
        if pattern == P_MACD_POSITIVE:  return _macd_bar_sign(m, True, lb, mc, pattern)
        if pattern == P_MACD_NEGATIVE:  return _macd_bar_sign(m, False, lb, mc, pattern)
        return MatchResult(False, pattern, "macd", f"未知 MACD 形态: {pattern}")

    if indicator == "rsi":
        r = ind.rsi or []
        if pattern == P_RSI_ABOVE_70:    return _rsi_threshold(r, True, 70, lb, mc, pattern)
        if pattern == P_RSI_BELOW_30:    return _rsi_threshold(r, False, 30, lb, mc, pattern)
        if pattern == P_RSI_ABOVE:
            if value is None: return MatchResult(False, pattern, "rsi", "缺少阈值")
            return _rsi_threshold(r, True, float(value), lb, mc, pattern)
        if pattern == P_RSI_BELOW:
            if value is None: return MatchResult(False, pattern, "rsi", "缺少阈值")
            return _rsi_threshold(r, False, float(value), lb, mc, pattern)
        if pattern == P_RSI_UP_THROUGH:
            if value is None: return MatchResult(False, pattern, "rsi", "缺少阈值")
            return _rsi_cross(r, float(value), "up", lb, mc, pattern)
        if pattern == P_RSI_DN_THROUGH:
            if value is None: return MatchResult(False, pattern, "rsi", "缺少阈值")
            return _rsi_cross(r, float(value), "down", lb, mc, pattern)
        return MatchResult(False, pattern, "rsi", f"未知 RSI 形态: {pattern}")

    if indicator == "kdj":
        k = ind.kdj or []
        if pattern == P_KDJ_GOLDEN:    return _kdj_cross(k, "up", lb, mc, pattern)
        if pattern == P_KDJ_DEATH:     return _kdj_cross(k, "down", lb, mc, pattern)
        if pattern == P_J_ABOVE_100:   return _kdj_zone(k, "j", True, 100, lb, mc, pattern)
        if pattern == P_J_BELOW_0:     return _kdj_zone(k, "j", False, 0, lb, mc, pattern)
        if pattern == P_KDJ_ABOVE:
            if value is None: return MatchResult(False, pattern, "kdj", "缺少阈值")
            return _kdj_zone(k, "k", True, float(value), lb, mc, pattern)
        if pattern == P_KDJ_BELOW:
            if value is None: return MatchResult(False, pattern, "kdj", "缺少阈值")
            return _kdj_zone(k, "k", False, float(value), lb, mc, pattern)
        return MatchResult(False, pattern, "kdj", f"未知 KDJ 形态: {pattern}")

    if indicator == "vol":
        v = ind.vol or []
        if pattern == P_VOL_ABOVE:
            if value is None: return MatchResult(False, pattern, "vol", "缺少倍数 N")
            return _vol_compare(v, float(value), True, lb, mc, pattern)
        if pattern == P_VOL_BELOW:
            if value is None: return MatchResult(False, pattern, "vol", "缺少倍数 N")
            return _vol_compare(v, float(value), False, lb, mc, pattern)
        if pattern == P_VOL_BREAKOUT:   return _vol_breakout(v, lb, mc, pattern)
        if pattern == P_VOL_SHRINK:     return _vol_shrink(v, lb, mc, pattern)
        return MatchResult(False, pattern, "vol", f"未知 VOL 形态: {pattern}")

    if indicator == "price":
        p = ind.price or []
        if pattern == P_PCT_UP:
            if value is None: return MatchResult(False, pattern, "price", "缺少百分比")
            return _pct_compare(p, float(value), True, lb, mc, pattern)
        if pattern == P_PCT_DOWN:
            if value is None: return MatchResult(False, pattern, "price", "缺少百分比")
            return _pct_compare(p, float(value), False, lb, mc, pattern)
        if pattern == P_CLOSE_ABOVE:
            if value is None: return MatchResult(False, pattern, "price", "缺少价格")
            return _price_threshold(p, float(value), True, lb, mc, pattern)
        if pattern == P_CLOSE_BELOW:
            if value is None: return MatchResult(False, pattern, "price", "缺少价格")
            return _price_threshold(p, float(value), False, lb, mc, pattern)
        if pattern == P_NEW_HIGH:
            n = int(value) if value is not None else 20
            return _new_high_low(p, n, True, lb, mc, pattern)
        if pattern == P_NEW_LOW:
            n = int(value) if value is not None else 20
            return _new_high_low(p, n, False, lb, mc, pattern)
        return MatchResult(False, pattern, "price", f"未知 PRICE 形态: {pattern}")

    return MatchResult(False, pattern, indicator, f"未知指标: {indicator}")


# --------- 向后兼容: 旧 BOLL 单形态接口 ---------
def match_legacy_boll(pattern: str, points: List[BollPoint]) -> MatchResult:
    """兼容旧的 BOLL-only 接口 (不传 IndicatorSet 时)。"""
    dummy = IndicatorSet(boll=points or [])
    return match_pattern("boll", pattern, dummy, lookback=200, match_count=1)


# --------- 多周期联合判断 ---------
def match_multi_timeframe(per_interval_results: dict, require_all: bool = True) -> bool:
    """多周期联合判断: 默认要求所有周期都匹配 (AND), 可改为 OR。"""
    if not per_interval_results:
        return False
    flags = [r.matched for r in per_interval_results.values()]
    return all(flags) if require_all else any(flags)