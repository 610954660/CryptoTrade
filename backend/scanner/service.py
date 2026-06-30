"""
扫描服务 (多指标)
================
协调:
  - 数据源 (A 股 / Crypto)
  - 多指标计算 (BOLL/MA/MACD/RSI/KDJ/VOL/PRICE)
  - 形态匹配 (支持 lookback + match_count)
  - 多周期联合判断

对外暴露高层 API 给 FastAPI 路由使用。

单条规则 schema (新):
  {
    interval:    '1d',
    indicator:   'boll',     # 可选, 默认 'boll'
    pattern:     'cross_mid_up',
    value:       None,        # 数值 (vol/price/rsi/kdj 阈值)
    lookback:    200,
    match_count: 1,
  }

为向后兼容, 旧 {interval, pattern} 仍可用 (默认 indicator=boll)。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from data_sources import a_share, crypto
from cache import service as cache_service
from indicators import (
    IndicatorSet,
    compute_all,
    compute_boll,
)
from scanner.matcher import (
    match_pattern,
    match_multi_timeframe,
    PATTERN_LABELS as _OLD_LABELS,  # 兼容
)

logger = logging.getLogger(__name__)


# --------- 入参定义 ---------
@dataclass
class IntervalRule:
    """单条筛选规则。"""
    interval: str
    indicator: str = "boll"          # 指标 key
    pattern: str = ""                # 形态 key
    value: Optional[float] = None    # 数值 (阈值)
    lookback: int = 200              # 回看 K 线根数
    match_count: int = 1             # 窗口内需要多少根匹配


@dataclass
class ScanRequest:
    """扫描请求。"""
    market: str
    rules: List[IntervalRule]
    combine: str = "all"             # "all" (AND) | "any" (OR)
    symbols: Optional[List[str]] = None
    limit: int = 200                 # 默认拉 200 根, 满足 lookback
    concurrency: int = 8
    progress_cb: Optional[callable] = None


# --------- 扫描结果 ---------
@dataclass
class SymbolHit:
    """单标的命中结果。"""
    symbol: str
    name: str = ""
    display: str = ""
    rules: Dict[str, dict] = field(default_factory=dict)
    combined_matched: bool = False
    last_close: float = 0.0
    last_mid: float = 0.0


# --------- 工具 ---------
def _normalize_market(market: str) -> str:
    m = market.lower().strip()
    if m in {"a", "a-share", "a_share", "ashare", "stock"}:
        return "a_share"
    if m in {"okx", "crypto_okx"}:
        return "crypto_okx"
    if m in {"crypto", "binance", "future", "futures"}:
        return "crypto"
    raise ValueError(f"未知市场: {market}")


def _data_source_provider(market: str) -> Optional[str]:
    """根据 market 选 crypto provider, 用于 fetch_kline。 None 表示 A 股。"""
    if market == "crypto_okx":
        return "okx"
    if market == "crypto":
        return "auto"
    return None


# --------- 数据源分发 (走缓存) ---------
async def _list_symbols(market: str) -> List[dict]:
    return await cache_service.get_or_fetch_symbols(market)


async def _fetch_kline(market: str, symbol: str, interval: str, limit: int) -> List[list]:
    """K 线全走 cache-aside。"""
    provider = _data_source_provider(market)
    return await cache_service.get_or_fetch_klines(market, symbol, interval, limit, provider=provider)


# --------- 单条规则匹配 ---------
def _match_rule(rule: IntervalRule, ind: IndicatorSet) -> dict:
    """执行单条规则的匹配, 返回结构化 dict 给 SSE 用。"""
    res = match_pattern(
        indicator=rule.indicator,
        pattern=rule.pattern,
        ind=ind,
        value=rule.value,
        lookback=rule.lookback,
        match_count=rule.match_count,
    )
    return {
        "interval": rule.interval,
        "indicator": rule.indicator,
        "pattern": res.pattern,
        "pattern_label": _label_for(rule, res),
        "matched": res.matched,
        "detail": res.detail,
        "matched_count": res.matched_count,
        "required_count": res.required_count,
        "lookback": res.lookback,
        "value": rule.value,
        "last_close": res.last_close,
        "last_mid": res.last_close,  # placeholder; 实际图表里不用这条
        "extra": res.extra,
    }


def _label_for(rule: IntervalRule, res) -> str:
    """根据 rule/pattern 给出简短标签 (用于 UI)。"""
    p = res.pattern or rule.pattern
    base = _OLD_LABELS.get(p)  # BOLL 兼容
    if base:
        return base
    # 其它指标从 indicator.registry 找
    try:
        from indicators.registry import INDICATORS
        ind_def = INDICATORS.get(rule.indicator, {})
        for pat in ind_def.get("patterns", []):
            if pat["key"] == p:
                return pat["label"]
    except Exception:
        pass
    return p


# --------- 单标的扫描 ---------
async def _scan_symbol(
    market: str,
    symbol_info: dict,
    rules: List[IntervalRule],
    default_limit: int,
    semaphore: asyncio.Semaphore,
) -> Optional[SymbolHit]:
    """对单个标的执行所有规则。"""
    sym = symbol_info["symbol"]
    name = symbol_info.get("name") or symbol_info.get("display", sym)

    # 按 interval 去重, 每个 interval 只拉一次 (一次性算所有指标)
    intervals_needed: Dict[str, int] = {}  # interval -> max(lookback)
    for rule in rules:
        if not rule.interval or not rule.pattern:
            continue
        # 拉取根数: max(lookback, default_limit)
        n = max(rule.lookback or 200, default_limit or 200)
        if intervals_needed.get(rule.interval, 0) < n:
            intervals_needed[rule.interval] = n

    rule_results: Dict[str, dict] = {}  # key = "{index}|{interval}|{indicator}|{pattern}"
    last_close = 0.0

    async with semaphore:
        for iv, need in intervals_needed.items():
            try:
                klines = await _fetch_kline(market, sym, iv, need)
            except Exception as e:
                logger.debug("拉取 %s %s K 线失败: %s", sym, iv, e)
                continue
            if not klines or len(klines) < 25:
                continue
            # 一次性算所有指标 (减少重复算)
            ind = compute_all(klines)
            if ind.bars:
                last_close = ind.bars[-1].close
            # 应用这个 interval 的所有规则
            # key 包含 index, 避免同 interval+indicator+pattern 覆盖 (如两条相同 ma20 规则)
            for ri, rule in enumerate(rules):
                if rule.interval != iv:
                    continue
                if not rule.pattern:
                    continue
                rd = _match_rule(rule, ind)
                key = f"{ri}|{iv}|{rule.indicator}|{rule.pattern}"
                rule_results[key] = rd

    if not rule_results:
        return None

    # 联合判断: 多规则 AND, 单规则直接看它
    if len(rule_results) == 1:
        combined = list(rule_results.values())[0]["matched"]
    else:
        combined = all(r["matched"] for r in rule_results.values())

    if not combined:
        return None

    return SymbolHit(
        symbol=sym,
        name=name,
        display=symbol_info.get("display") or sym,
        rules=rule_results,
        combined_matched=True,
        last_close=last_close,
        last_mid=last_close,
    )


# --------- 主扫描流程 ---------
async def scan(req: ScanRequest) -> dict:
    """执行扫描。 支持同步 progress_cb。"""
    market = _normalize_market(req.market)
    all_symbols = await _list_symbols(market)

    if req.symbols:
        wanted = set(req.symbols)
        targets = [s for s in all_symbols if s["symbol"] in wanted]
    else:
        targets = all_symbols

    if not req.rules:
        raise ValueError("至少需要一条规则")

    sem = asyncio.Semaphore(req.concurrency)
    started = time.time()
    hits: List[SymbolHit] = []
    errors = 0
    done_counter = 0
    lock = asyncio.Lock()

    async def _wrap(s):
        nonlocal errors
        result = None
        try:
            result = await _scan_symbol(market, s, req.rules, req.limit, sem)
        except Exception as e:
            # 完整堆栈, 方便定位 AttributeError 之类的根因
            logger.exception("扫描 %s 出错", s.get("symbol"))
            errors += 1
        if req.progress_cb:
            try:
                req.progress_cb(done_counter + 1, len(targets), result)
            except Exception:
                pass
        return result

    BATCH = 200
    for i in range(0, len(targets), BATCH):
        batch = targets[i : i + BATCH]

        async def _run_one(s):
            nonlocal done_counter
            r = await _wrap(s)
            done_counter += 1
            return r

        results = await asyncio.gather(*[_run_one(s) for s in batch])
        for r in results:
            if r is None:
                continue
            hits.append(r)

    elapsed = time.time() - started
    logger.info("扫描完成: market=%s 标的=%d 命中=%d 错误=%d 用时=%.1fs",
                market, len(targets), len(hits), errors, elapsed)
    return {
        "market": market,
        "total_symbols": len(targets),
        "scanned": len(targets),
        "hits": [_hit_to_dict(h) for h in hits],
        "hit_count": len(hits),
        "elapsed_sec": round(elapsed, 2),
        "errors": errors,
    }


def _hit_to_dict(h: SymbolHit) -> dict:
    return {
        "symbol": h.symbol,
        "name": h.name,
        "display": h.display,
        "last_close": h.last_close,
        "last_mid": h.last_mid,
        "rules": h.rules,
    }