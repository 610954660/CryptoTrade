"""
指标注册表
==========
前端从 /api/indicators 拿这个, 渲染规则 UI。

每条规则:
  {
    interval:   '1d',                # 周期 (按数据源筛过)
    indicator:  'boll',              # 指标 key
    pattern:    'cross_mid_up',      # 形态 key
    value:      null,                # 数值 (vol/price 需要)
    lookback:   200,                 # 查询 K 线数 (回看窗口)
    match_count: 1,                  # 符合的 K 线数
  }

形态按 mode 分类 (决定如何用 lookback / match_count):
  - "state"   : 只看最后一根 K 线 (如 "J<0 当前超卖", "DIF 在零轴上方")
                match_count / lookback 对此模式无意义, 只用最后一根
  - "cross"   : 看最后两根 K 线 (穿越只能发生一次)
                lookback/match_count 同样无意义
  - "window"  : 在最近 lookback 根里, match_count 根满足
                (如 "20 天内 10 根涨幅 > 5%")

INDICATORS 结构:
  {
    "boll": {
      "label": "布林带",
      "patterns": [
        {"key": "cross_mid_up", "label": "向上穿越中轨",
         "value_required": false, "mode": "cross"},
        ...
      ]
    },
    ...
  }
"""
from __future__ import annotations

from typing import Dict, List


# 各指标支持的所有周期 (粗略全集, 实际渲染时再按数据源过滤)
_ALL_INTERVALS = ["3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "3d", "1w"]

# 数据源 (market) 支持的周期
_MARKET_INTERVALS: Dict[str, List[str]] = {
    # A 股 (Sina 支持: 5m, 15m, 30m, 60m, 1d, 1w)  AKShare 不支持 3m
    "a_share":  ["5m", "15m", "30m", "1h", "1d", "1w"],
    # OKX 合约全支持
    "crypto_okx": list(_ALL_INTERVALS),
    # Binance 合约全支持
    "crypto_binance": list(_ALL_INTERVALS),
}


def list_intervals(market: str = None) -> List[str]:
    """返回该市场支持的周期。 market 为 None 时返回全集。"""
    if market is None:
        return list(_ALL_INTERVALS)
    return list(_MARKET_INTERVALS.get(market, _ALL_INTERVALS))


def market_supports_interval(market: str, interval: str) -> bool:
    return interval in _MARKET_INTERVALS.get(market, _ALL_INTERVALS)


# =========================================================================
# 指标定义
# =========================================================================
# 形态最低查询K线数 / 默认符合K线数 (前端渲染时套用, 也作为后端硬下限)
# 含义:
#   lookback_min: 该形态要算出来至少需要多少根 K 线 (受指标窗口影响)
#   match_count_default: window 模式下, 默认 M (10)
#   value_min: value 字段允许的最小值 (0 表示不限制, 比如 vol 倍数)
_PATTERN_DEFAULTS = {
    # BOLL: 默认 20 周期, 算上下轨需要 N + 若干
    "cross_mid_up":   {"lookback_min": 30, "match_count_default": 1},
    "cross_mid_down": {"lookback_min": 30, "match_count_default": 1},
    "upper_breakout": {"lookback_min": 30, "match_count_default": 1},
    "lower_breakout": {"lookback_min": 30, "match_count_default": 1},
    "mid_trend_up":   {"lookback_min": 60, "match_count_default": 1},  # 算斜率需要更多
    "mid_trend_down": {"lookback_min": 60, "match_count_default": 1},
    "boll_open":      {"lookback_min": 60, "match_count_default": 1},
    "boll_squeeze":   {"lookback_min": 60, "match_count_default": 1},
    # MA: MA60 需要 60+ 根
    "price_above_ma5":  {"lookback_min": 30, "match_count_default": 1},
    "price_below_ma5":  {"lookback_min": 30, "match_count_default": 1},
    "price_above_ma20": {"lookback_min": 30, "match_count_default": 1},
    "price_below_ma20": {"lookback_min": 30, "match_count_default": 1},
    "ma5_above_ma20":   {"lookback_min": 30, "match_count_default": 1},
    "ma5_below_ma20":   {"lookback_min": 30, "match_count_default": 1},
    "ma20_above_ma60":  {"lookback_min": 70, "match_count_default": 1},
    "ma20_below_ma60":  {"lookback_min": 70, "match_count_default": 1},
    "bull_alignment":   {"lookback_min": 70, "match_count_default": 1},
    "bear_alignment":   {"lookback_min": 70, "match_count_default": 1},
    # MACD: EMA 12/26/9, 起步 ~35
    "golden_cross":   {"lookback_min": 40, "match_count_default": 1},
    "death_cross":    {"lookback_min": 40, "match_count_default": 1},
    "dif_above_zero": {"lookback_min": 40, "match_count_default": 1},
    "dif_below_zero": {"lookback_min": 40, "match_count_default": 1},
    "macd_positive":  {"lookback_min": 40, "match_count_default": 1},
    "macd_negative":  {"lookback_min": 40, "match_count_default": 1},
    "macd_top_div":   {"lookback_min": 80, "match_count_default": 1},
    "macd_bot_div":   {"lookback_min": 80, "match_count_default": 1},
    # RSI: 14 周期
    "rsi_above_70":   {"lookback_min": 30, "match_count_default": 1},
    "rsi_below_30":   {"lookback_min": 30, "match_count_default": 1},
    "rsi_above":      {"lookback_min": 30, "match_count_default": 1},
    "rsi_below":      {"lookback_min": 30, "match_count_default": 1},
    "rsi_up_through": {"lookback_min": 30, "match_count_default": 1},
    "rsi_dn_through": {"lookback_min": 30, "match_count_default": 1},
    # KDJ: 9 周期
    "kdj_golden":  {"lookback_min": 30, "match_count_default": 1},
    "kdj_death":   {"lookback_min": 30, "match_count_default": 1},
    "j_above_100": {"lookback_min": 30, "match_count_default": 1},
    "j_below_0":   {"lookback_min": 30, "match_count_default": 1},
    "kdj_above":   {"lookback_min": 30, "match_count_default": 1},
    "kdj_below":   {"lookback_min": 30, "match_count_default": 1},
    # VOL: 5/20 日均量
    "vol_above":    {"lookback_min": 30, "match_count_default": 1, "value_min": 0.1,  "value_default": 1.5},
    "vol_below":    {"lookback_min": 30, "match_count_default": 1, "value_min": 0.1,  "value_default": 0.5},
    "vol_breakout": {"lookback_min": 30, "match_count_default": 1},
    "vol_shrink":   {"lookback_min": 30, "match_count_default": 1},
    # PRICE
    "pct_up":      {"lookback_min": 30, "match_count_default": 10, "value_min": 0.1, "value_default": 5.0},
    "pct_down":    {"lookback_min": 30, "match_count_default": 10, "value_min": 0.1, "value_default": 5.0},
    "close_above": {"lookback_min": 30, "match_count_default": 1,  "value_min": 0},
    "close_below": {"lookback_min": 30, "match_count_default": 1,  "value_min": 0},
    "new_high":    {"lookback_min": 60, "match_count_default": 1,  "value_min": 5, "value_default": 20},
    "new_low":     {"lookback_min": 60, "match_count_default": 1,  "value_min": 5, "value_default": 20},
}


def get_pattern_defaults(pattern: str) -> dict:
    """返回某 pattern 的 {lookback_min, match_count_default, value_min?, value_default?}。"""
    d = _PATTERN_DEFAULTS.get(pattern, {})
    return {
        "lookback_min": d.get("lookback_min", 30),
        "match_count_default": d.get("match_count_default", 1),
        "value_min": d.get("value_min"),
        "value_default": d.get("value_default"),
    }


INDICATORS: Dict[str, dict] = {
    "boll": {
        "label": "布林带 (BOLL)",
        "patterns": [
            {"key": "cross_mid_up",   "label": "向上穿越中轨",     "value_required": False, "mode": "cross"},
            {"key": "cross_mid_down", "label": "向下穿越中轨",     "value_required": False, "mode": "cross"},
            {"key": "upper_breakout", "label": "突破上轨",         "value_required": False, "mode": "state"},
            {"key": "lower_breakout", "label": "跌破下轨",         "value_required": False, "mode": "state"},
            {"key": "mid_trend_up",   "label": "中轨上行",         "value_required": False, "mode": "window"},
            {"key": "mid_trend_down", "label": "中轨下行",         "value_required": False, "mode": "window"},
            {"key": "boll_open",      "label": "BOLL 开口 (收窄后扩张)", "value_required": False, "mode": "window"},
            {"key": "boll_squeeze",   "label": "BOLL 收窄 (振幅缩小)",   "value_required": False, "mode": "window"},
        ],
    },
    "ma": {
        "label": "均线 (MA)",
        "patterns": [
            {"key": "price_above_ma5",    "label": "价格在 MA5 上方",   "value_required": False, "mode": "state"},
            {"key": "price_below_ma5",    "label": "价格在 MA5 下方",   "value_required": False, "mode": "state"},
            {"key": "price_above_ma20",   "label": "价格在 MA20 上方",  "value_required": False, "mode": "state"},
            {"key": "price_below_ma20",   "label": "价格在 MA20 下方",  "value_required": False, "mode": "state"},
            {"key": "ma5_above_ma20",     "label": "MA5 上穿 MA20 (金叉)", "value_required": False, "mode": "cross"},
            {"key": "ma5_below_ma20",     "label": "MA5 下穿 MA20 (死叉)", "value_required": False, "mode": "cross"},
            {"key": "ma20_above_ma60",    "label": "MA20 上穿 MA60",     "value_required": False, "mode": "cross"},
            {"key": "ma20_below_ma60",    "label": "MA20 下穿 MA60",     "value_required": False, "mode": "cross"},
            {"key": "bull_alignment",     "label": "多头排列 (MA5>MA10>MA20>MA60)", "value_required": False, "mode": "state"},
            {"key": "bear_alignment",     "label": "空头排列 (MA5<MA10<MA20<MA60)", "value_required": False, "mode": "state"},
        ],
    },
    "macd": {
        "label": "MACD",
        "patterns": [
            {"key": "golden_cross",    "label": "DIF 金叉 DEA",     "value_required": False, "mode": "cross"},
            {"key": "death_cross",     "label": "DIF 死叉 DEA",     "value_required": False, "mode": "cross"},
            {"key": "dif_above_zero",  "label": "DIF 在零轴上方",    "value_required": False, "mode": "state"},
            {"key": "dif_below_zero",  "label": "DIF 在零轴下方",    "value_required": False, "mode": "state"},
            {"key": "macd_positive",   "label": "MACD 柱由负转正",  "value_required": False, "mode": "cross"},
            {"key": "macd_negative",   "label": "MACD 柱由正转负",  "value_required": False, "mode": "cross"},
            {"key": "macd_top_div",    "label": "顶背离 (价格↑ MACD↓)",  "value_required": False, "mode": "state"},
            {"key": "macd_bot_div",    "label": "底背离 (价格↓ MACD↑)",  "value_required": False, "mode": "state"},
        ],
    },
    "rsi": {
        "label": "RSI",
        "patterns": [
            {"key": "rsi_above_70",  "label": "RSI > 70 (超买)",   "value_required": False, "mode": "state"},
            {"key": "rsi_below_30",  "label": "RSI < 30 (超卖)",   "value_required": False, "mode": "state"},
            {"key": "rsi_above",     "label": "RSI 高于阈值",      "value_required": True,  "value_label": "阈值 (0-100)", "mode": "state"},
            {"key": "rsi_below",     "label": "RSI 低于阈值",      "value_required": True,  "value_label": "阈值 (0-100)", "mode": "state"},
            {"key": "rsi_up_through","label": "RSI 上穿阈值",      "value_required": True,  "value_label": "阈值",          "mode": "cross"},
            {"key": "rsi_dn_through","label": "RSI 下穿阈值",      "value_required": True,  "value_label": "阈值",          "mode": "cross"},
        ],
    },
    "kdj": {
        "label": "KDJ",
        "patterns": [
            {"key": "kdj_golden",  "label": "K 上穿 D (金叉)",   "value_required": False, "mode": "cross"},
            {"key": "kdj_death",   "label": "K 下穿 D (死叉)",   "value_required": False, "mode": "cross"},
            {"key": "j_above_100", "label": "J > 100 (超买)",    "value_required": False, "mode": "state"},
            {"key": "j_below_0",   "label": "J < 0 (超卖)",      "value_required": False, "mode": "state"},
            {"key": "kdj_above",   "label": "K 高于阈值",        "value_required": True,  "value_label": "阈值", "mode": "state"},
            {"key": "kdj_below",   "label": "K 低于阈值",        "value_required": True,  "value_label": "阈值", "mode": "state"},
        ],
    },
    "vol": {
        "label": "成交量",
        "patterns": [
            {"key": "vol_above",    "label": "成交量 > N 倍 5日均量",   "value_required": True,  "value_label": "倍数 N", "mode": "state"},
            {"key": "vol_below",    "label": "成交量 < N 倍 5日均量",   "value_required": True,  "value_label": "倍数 N", "mode": "state"},
            {"key": "vol_breakout", "label": "成交量 > 20日均量",      "value_required": False, "mode": "state"},
            {"key": "vol_shrink",   "label": "成交量萎缩 (<0.5x 5日均量)", "value_required": False, "mode": "state"},
        ],
    },
    "price": {
        "label": "价格 / 涨跌幅",
        "patterns": [
            {"key": "pct_up",      "label": "涨幅 > N %",     "value_required": True, "value_label": "百分比 N", "mode": "window"},
            {"key": "pct_down",    "label": "跌幅 > N %",     "value_required": True, "value_label": "百分比 N", "mode": "window"},
            {"key": "close_above", "label": "收盘价 > 阈值",  "value_required": True, "value_label": "价格",     "mode": "state"},
            {"key": "close_below", "label": "收盘价 < 阈值",  "value_required": True, "value_label": "价格",     "mode": "state"},
            {"key": "new_high",    "label": "创 N 周期新高",   "value_required": True, "value_label": "周期数 N", "mode": "state"},
            {"key": "new_low",     "label": "创 N 周期新低",   "value_required": True, "value_label": "周期数 N", "mode": "state"},
        ],
    },
}


def list_indicators() -> List[dict]:
    """按 key 顺序返回指标列表 (含 label, patterns + 形态默认下限)。"""
    out = []
    for k, v in INDICATORS.items():
        pats = []
        for p in v["patterns"]:
            d = get_pattern_defaults(p["key"])
            pats.append({
                **p,
                "lookback_min": d["lookback_min"],
                "match_count_default": d["match_count_default"],
                **({"value_min": d["value_min"]} if d["value_min"] is not None else {}),
                **({"value_default": d["value_default"]} if d["value_default"] is not None else {}),
            })
        out.append({"key": k, "label": v["label"], "patterns": pats})
    return out


def get_pattern_mode(indicator: str, pattern: str) -> str:
    """查某形态的 mode: state / cross / window。 找不到默认 'state'。"""
    ind_def = INDICATORS.get(indicator)
    if not ind_def:
        return "state"
    for p in ind_def.get("patterns", []):
        if p["key"] == pattern:
            return p.get("mode", "state")
    return "state"