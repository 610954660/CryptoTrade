"""
扫描服务
========
负责协调:
  - 数据源 (A 股 / Crypto)
  - BOLL 指标计算
  - 形态匹配
  - 多周期联合判断

对外暴露高层 API 给 FastAPI 路由使用。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from data_sources import a_share, crypto
from cache import service as cache_service
from indicators import compute_boll, boll_to_dicts
from scanner.matcher import (
    match_pattern,
    match_multi_timeframe,
    PATTERN_LABELS,
    PATTERN_BOLL_OPEN,
    PATTERN_CROSS_MID_UP,
    PATTERN_CROSS_MID_DOWN,
)

logger = logging.getLogger(__name__)


# --------- 入参定义 ---------
@dataclass
class IntervalRule:
    """单条筛选规则: 指定一个时间周期 + 形态。"""
    interval: str         # "5m", "15m", "1h", "1d" ...
    pattern: str          # "boll_open", "cross_mid_up" ...


@dataclass
class ScanRequest:
    """扫描请求。"""
    market: str                                # "a_share" | "crypto" | "crypto_okx"
    rules: List[IntervalRule]                  # 多个周期多条规则
    combine: str = "all"                       # "all" (AND) | "any" (OR)
    symbols: Optional[List[str]] = None        # None = 扫描全市场
    limit: int = 100                           # 每只 K 线拉取根数
    concurrency: int = 8                       # 并发数
    progress_cb: Optional[callable] = None     # 进度回调 (sync, 在线程中调用)


# --------- 扫描结果 ---------
@dataclass
class SymbolHit:
    """单标的命中结果。"""
    symbol: str
    name: str = ""
    display: str = ""
    rules: Dict[str, dict] = field(default_factory=dict)  # {interval: {matched, pattern, detail, ...}}
    combined_matched: bool = False
    last_close: float = 0.0
    last_mid: float = 0.0


# --------- 工具: 把 IntervalRule 拼成请求 ---------
def _rule_to_dict(r: IntervalRule) -> dict:
    return {"interval": r.interval, "pattern": r.pattern, "pattern_label": PATTERN_LABELS.get(r.pattern, r.pattern)}


def _normalize_market(market: str) -> str:
    m = market.lower().strip()
    if m in {"a", "a-share", "a_share", "ashare", "stock"}:
        return "a_share"
    if m in {"okx", "crypto_okx"}:
        return "crypto_okx"
    if m in {"crypto", "binance", "future", "futures"}:
        return "crypto"
    raise ValueError(f"未知市场: {market}")


# --------- 数据源分发 (全部走缓存) ---------
async def _list_symbols(market: str) -> List[dict]:
    """所有标的信息都走缓存层, 24h 内不会重复拉。"""
    return await cache_service.get_or_fetch_symbols(market)


async def _fetch_kline(market: str, symbol: str, interval: str, limit: int) -> List[list]:
    """K 线全部走 cache-aside: 缓存 5 分钟内直接读, 否则增量拉。"""
    return await cache_service.get_or_fetch_klines(market, symbol, interval, limit)


# --------- 单标的扫描 ---------
async def _scan_symbol(
    market: str,
    symbol_info: dict,
    rules: List[IntervalRule],
    limit: int,
    semaphore: asyncio.Semaphore,
) -> Optional[SymbolHit]:
    """对单个标的执行所有规则, 返回 SymbolHit (未命中则 None)。"""
    sym = symbol_info["symbol"]
    name = symbol_info.get("name") or symbol_info.get("display", sym)

    rule_results: Dict[str, dict] = {}
    last_close = 0.0
    last_mid = 0.0

    async with semaphore:
        for rule in rules:
            try:
                klines = await _fetch_kline(market, sym, rule.interval, limit)
            except Exception as e:
                logger.debug("拉取 %s %s K 线失败: %s", sym, rule.interval, e)
                continue
            if not klines or len(klines) < 25:
                continue
            points = compute_boll(klines)
            if len(points) < 5:
                continue
            r = match_pattern(rule.pattern, points)
            rule_results[rule.interval] = {
                "interval": rule.interval,
                "pattern": r.pattern,
                "pattern_label": PATTERN_LABELS.get(r.pattern, r.pattern),
                "matched": r.matched,
                "detail": r.detail,
                "last_close": r.last_close,
                "last_mid": r.last_mid,
                "last_upper": r.last_upper,
                "last_lower": r.last_lower,
                "last_width": r.last_width,
                "width_change_pct": r.width_change_pct,
            }
            if points:
                last_close = points[-1].close
                last_mid = points[-1].mid

    if not rule_results:
        return None

    # 联合判断: 多规则时全部满足才算命中; 单规则时只看它本身
    combined = match_multi_timeframe(
        {iv: type("R", (), {"matched": d["matched"]}) for iv, d in rule_results.items()},
        require_all=(len(rules) > 1),
    )

    # 如果只有一条规则, 直接看它匹不匹配
    if len(rules) == 1 and rule_results:
        combined = list(rule_results.values())[0]["matched"]

    if not combined:
        return None

    return SymbolHit(
        symbol=sym,
        name=name,
        display=symbol_info.get("display") or sym,
        rules=rule_results,
        combined_matched=True,
        last_close=last_close,
        last_mid=last_mid,
    )


# --------- 主扫描流程 ---------
async def scan(req: ScanRequest) -> dict:
    """执行扫描, 返回结果字典 (供路由层 JSON 化)。

    支持按只回调: req.progress_cb(done, total, latest_hit_or_None)
    """
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
            logger.info("扫描 %s 出错: %s", s.get("symbol"), e.__class__.__name__)
            errors += 1
        if req.progress_cb:
            async with lock:
                nonlocal_done = None
                try:
                    req.progress_cb(done_counter + 1, len(targets), result)
                except Exception:
                    pass
        return result

    # 分批并发, 控制内存峰值
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
        "hits": [_hit_to_dict(h, req.rules) for h in hits],
        "hit_count": len(hits),
        "elapsed_sec": round(elapsed, 2),
        "errors": errors,
    }


def _hit_to_dict(h: SymbolHit, rules: List[IntervalRule]) -> dict:
    return {
        "symbol": h.symbol,
        "name": h.name,
        "display": h.display,
        "last_close": h.last_close,
        "last_mid": h.last_mid,
        "rules": h.rules,
    }
