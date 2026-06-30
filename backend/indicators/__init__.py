"""
技术指标包
==========
- boll  布林带 (BOLL)
- ma    移动平均线 (MA)
- macd  平滑异同移动平均线
- rsi   相对强弱指标
- kdj   随机指标
- vol   成交量
- price 价格/涨跌幅

公共 API:
  compute_all(klines) -> IndicatorSet
  compute_boll(klines) -> List[BollPoint]   (向后兼容)
  boll_to_dicts(points) -> List[dict]       (向后兼容)
  INDICATORS (registry)
"""
from .base import (
    IndicatorSet,
    KLineBar,
    MaPoint,
    MacdPoint,
    RsiPoint,
    KdjPoint,
    VolPoint,
    PricePoint,
    to_dataframe,
    to_indicator_set,
)
from .boll import (
    compute_boll,
    boll_to_dicts,
    BollPoint,
)
from .ma import compute_ma
from .macd import compute_macd
from .rsi import compute_rsi
from .kdj import compute_kdj
from .vol import compute_vol_stats
from .price import compute_price_stats
from .registry import INDICATORS, list_intervals, market_supports_interval


def compute_all(klines) -> IndicatorSet:
    """一次性算出所有指标, 给 scanner 用。"""
    if not klines:
        return IndicatorSet()
    bars = to_indicator_set(klines)
    return IndicatorSet(
        bars=bars,
        boll=compute_boll(bars),
        ma=compute_ma(bars),
        macd=compute_macd(bars),
        rsi=compute_rsi(bars),
        kdj=compute_kdj(bars),
        vol=compute_vol_stats(bars),
        price=compute_price_stats(bars),
    )


__all__ = [
    "INDICATORS",
    "list_intervals",
    "market_supports_interval",
    "compute_all",
    "compute_boll",
    "boll_to_dicts",
    "BollPoint",
    "IndicatorSet",
    "KLineBar",
]
