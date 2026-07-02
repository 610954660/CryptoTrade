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
    compute_indicators_needed,
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
    tag_filters: Optional[dict] = None  # {group_key: [option_key, ...]}; None = 不限
    kline_filter_enabled: bool = True   # K 线筛选总开关: False=跳过 pipeline, 标的预筛池全部当命中


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


# --------- 单标的扫描 (pipeline 形式) ---------
# pipeline 思路: 每条规则 = 一个 filter function, 顺序执行
#   data_pool -> [filter_0] -> [filter_1] -> ... -> hits
# 单条规则未启用 (enabled=False): 作为 passthrough, 不影响后续规则
# 所有规则都未启用: pipeline 为空, 所有标的都"通过" (相当于全量输出, 实际 UI 应至少 1 条启用)

async def _scan_symbol(
    market: str,
    symbol_info: dict,
    rules: List[IntervalRule],
    default_limit: int,
    intervals_klines: Dict[str, List[list]],
    indicators_needed: set,
) -> Optional[SymbolHit]:
    """对单个标的执行 pipeline。

    intervals_klines: 由调用方预批量拉好的 { interval: rows }。
    indicators_needed: 本批规则实际用到的指标集合 (例 {"boll"})。
    """
    sym = symbol_info["symbol"]
    name = symbol_info.get("name") or symbol_info.get("display", sym)

    # 按 interval -> IndicatorSet (同一 interval 复用, 不同 indicator 按需计算)
    ind_cache: Dict[str, IndicatorSet] = {}
    last_close = 0.0

    for iv, klines in intervals_klines.items():
        if not klines or len(klines) < 25:
            continue
        ind = compute_indicators_needed(klines, indicators_needed)
        ind_cache[iv] = ind
        if ind.bars:
            last_close = ind.bars[-1].close

    # 2) 构建 pipeline: 每条规则 = 一个 filter function
    rule_results: Dict[str, dict] = {}
    pipeline_matched = True
    for ri, rule in enumerate(rules):
        if not rule.interval or not rule.pattern:
            continue
        if not pipeline_matched:
            break
        ind = ind_cache.get(rule.interval)
        if ind is None:
            pipeline_matched = False
            break
        rd = _match_rule(rule, ind)
        key = f"{ri}|{rule.interval}|{rule.indicator}|{rule.pattern}"
        rule_results[key] = rd
        pipeline_matched = rd["matched"]

    if not pipeline_matched:
        return None

    if not rule_results:
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
def _symbol_matches_group(s: dict, group_key: str, option_keys: list, market: str) -> bool:
    """判断一个标的是否命中某个 group (任一 option_key 命中即 True)。

    group_key -> 内部 tag 集合的映射:
      a_share:
        exchange: 派生 ['sh','sz','bj'] -> code 前缀
        board:    派生 ['main','star','chinext','bse']
        warning:  派生 ['st']
        quality:  外部 (从 s['tags'] 里读 '双融'/'蓝筹'/'白马') -> option_key 'margin'/'blue'/'white'
      crypto / crypto_okx:
        bluechip: 派生 (s['tags'] 含 'bluechip_yes'/'bluechip_no')
        quote:    派生 (s['tags'] 含 'quote_USDT'/'quote_USDC')
    """
    code = s.get("code") or s.get("symbol", "")
    name = s.get("name") or ""
    item_tags = set(s.get("tags") or [])

    if market == "a_share":
        derived = set(a_share.derive_a_share_tags(code, name))
        # 外部标签: option_key -> 内部 tag
        QUALITY_MAP = {"margin": "双融", "blue": "蓝筹", "white": "白马"}
        if group_key in ("exchange", "board", "warning"):
            return any(opt in derived for opt in option_keys)
        if group_key == "quality":
            internal = {QUALITY_MAP.get(o) for o in option_keys}
            internal.discard(None)
            return any(t in item_tags for t in internal)
        return True  # 未知 group: 视为不限
    else:
        # crypto: 直接读 tags
        if group_key == "bluechip":
            return any(opt in item_tags for opt in option_keys)
        if group_key == "quote":
            return any(opt in item_tags for opt in option_keys)
        return True


def _apply_tag_filters(symbols: list, tag_filters: dict, market: str) -> list:
    """应用复选筛选: 跨 group AND, 同 group OR。 空 group 不限。"""
    if not tag_filters:
        return symbols
    out = []
    for s in symbols:
        ok = True
        for gk, opts in tag_filters.items():
            if not opts:  # 空 group 视为不限
                continue
            if not _symbol_matches_group(s, gk, [str(o) for o in opts], market):
                ok = False
                break
        if ok:
            out.append(s)
    return out


async def scan(req: ScanRequest) -> dict:
    """执行扫描。 支持同步 progress_cb。"""
    market = _normalize_market(req.market)
    all_symbols = await _list_symbols(market)

    if req.symbols:
        wanted = set(req.symbols)
        targets = [s for s in all_symbols if s["symbol"] in wanted]
    else:
        targets = all_symbols

    # 标签预筛: 同 group 内 OR, 跨 group AND。 空 group 视为"不限"。
    if req.tag_filters:
        before = len(targets)
        targets = _apply_tag_filters(targets, req.tag_filters, market)
        logger.info("标签预筛: %d -> %d (filters=%s)", before, len(targets), req.tag_filters)
        if not targets:
            return {
                "market": market,
                "total_symbols": 0,
                "scanned": 0,
                "hits": [],
                "hit_count": 0,
                "elapsed_sec": 0.0,
                "errors": 0,
            }

    # K 线筛选总开关关闭: 跳过 pipeline, 直接把标的预筛池全部当命中
    # 但仍拉一次最近价 (用 default_limit 根 1d K 线), 让表格里"最近价"列有意义
    if not req.kline_filter_enabled:
        logger.info("K线筛选已关闭, 跳过 pipeline, 输出 %d 个标的预筛池全部命中", len(targets))
        started = time.time()
        # 并发拉最近价 (限流到 req.concurrency, 与正常扫描一致)
        sem = asyncio.Semaphore(req.concurrency)
        PRICE_LIMIT = max(50, min(req.limit or 100, 200))  # 拉 1d 50-200 根足够取最后一个 close

        async def _fetch_price(s):
            sym = s["symbol"]
            async with sem:
                try:
                    klines = await _fetch_kline(market, sym, "1d", PRICE_LIMIT)
                except Exception:
                    return s["symbol"], 0.0
                if not klines:
                    return s["symbol"], 0.0
                # kline 格式 [ts, open, high, low, close, vol]
                try:
                    return s["symbol"], float(klines[-1][4])
                except Exception:
                    return s["symbol"], 0.0

        # 推 progress_cb: 每完成一个更新一次
        done_counter = 0

        async def _wrap(s):
            nonlocal done_counter
            sym, last_close = await _fetch_price(s)
            done_counter += 1
            if req.progress_cb:
                try:
                    req.progress_cb(done_counter, len(targets), None)
                except Exception:
                    pass
            return sym, last_close

        # 分批并发, 每批 200
        BATCH = 200
        prices: Dict[str, float] = {}
        for i in range(0, len(targets), BATCH):
            batch = targets[i : i + BATCH]
            results = await asyncio.gather(*[_wrap(s) for s in batch], return_exceptions=True)
            for r in results:
                if isinstance(r, tuple) and len(r) == 2:
                    sym, price = r
                    prices[sym] = price

        hits = [
            SymbolHit(
                symbol=s["symbol"],
                name=s.get("name") or s.get("display") or s["symbol"],
                display=s.get("display") or s["symbol"],
                rules={},  # 没有跑规则, 命中详情为空
                combined_matched=True,
                last_close=prices.get(s["symbol"], 0.0),
                last_mid=prices.get(s["symbol"], 0.0),
            )
            for s in targets
        ]
        return {
            "market": market,
            "total_symbols": len(targets),
            "scanned": len(targets),
            "hits": [_hit_to_dict(h) for h in hits],
            "hit_count": len(hits),
            "elapsed_sec": round(time.time() - started, 2),
            "errors": 0,
        }

    if not req.rules:
        raise ValueError("至少需要一条规则")

    # 收集本批规则实际用到的指标集合 (懒计算用)
    indicators_needed = {r.indicator for r in req.rules if r.indicator}

    # 按 interval 取最大 lookback -> limit
    intervals_needed: Dict[str, int] = {}
    for rule in req.rules:
        if not rule.interval or not rule.pattern:
            continue
        n = max(rule.lookback or 200, req.limit or 200)
        if intervals_needed.get(rule.interval, 0) < n:
            intervals_needed[rule.interval] = n

    sem = asyncio.Semaphore(req.concurrency)
    started = time.time()
    hits: List[SymbolHit] = []
    errors = 0
    done_counter = 0

    BATCH = 200
    for i in range(0, len(targets), BATCH):
        batch = targets[i : i + BATCH]
        symbols = [s["symbol"] for s in batch]

        # 每 batch: 并发 (interval, symbol 矩阵) 批量拉 K 线 (单 SQL 一次 N 标的)
        async def _bulk_for_iv(iv: str, n: int):
            return iv, await cache_service.get_or_fetch_klines_bulk(
                market, symbols, iv, limit=n,
                provider=_data_source_provider(market),
                concurrency=req.concurrency,
            )
        bulk_results = await asyncio.gather(*[_bulk_for_iv(iv, n) for iv, n in intervals_needed.items()])
        # iv_klines_all[interval][symbol] = rows
        iv_klines_all: Dict[str, Dict[str, List[list]]] = {iv: m for iv, m in bulk_results}

        async def _run_one(s):
            sym = s["symbol"]
            per_iv = {iv: iv_klines_all[iv].get(sym) or [] for iv in intervals_needed}
            return await _scan_symbol(market, s, req.rules, req.limit, per_iv, indicators_needed)

        results = await asyncio.gather(*[_run_one(s) for s in batch], return_exceptions=True)
        for s, r in zip(batch, results):
            done_counter += 1
            if isinstance(r, Exception):
                logger.exception("扫描 %s 出错", s.get("symbol"))
                errors += 1
                if req.progress_cb:
                    try: req.progress_cb(done_counter, len(targets), None)
                    except Exception: pass
                continue
            if r is None:
                if req.progress_cb:
                    try: req.progress_cb(done_counter, len(targets), None)
                    except Exception: pass
                continue
            hits.append(r)
            if req.progress_cb:
                try:
                    req.progress_cb(done_counter, len(targets), r)
                except Exception:
                    pass

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