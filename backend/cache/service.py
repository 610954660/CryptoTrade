"""
缓存服务层 (cache-aside)
=========================
在数据源和调用方之间插一层:
  1) 先查缓存, 命中且新鲜 -> 直接返回
  2) 否则调数据源, 回写缓存, 返回

K 线增量更新策略:
  - 缓存里最新的 open_time = T
  - 下次请求: 调数据源时 startTime = T+1 (or T+interval_seconds)
  - 把增量数据 merge 进缓存
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional

from cache import repository as repo
from data_sources import a_share, crypto as crypto_src
from settings import is_no_cache

logger = logging.getLogger(__name__)


# --------- TTL 配置 ---------
SYMBOL_TTL_SEC = 24 * 3600        # 标的列表 1 天
KLINE_FRESH_SEC = 5 * 60           # K 线 5 分钟内视为新鲜, 直接读缓存


# --------- 周期 -> 秒数 (用于增量合并) ---------
INTERVAL_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
    "3d": 259200, "1w": 604800, "1M": 2592000,
}


# ============== 标的信息 ==============
async def get_or_fetch_symbols(market: str, force_refresh: bool = False) -> List[dict]:
    """获取标的信息, 优先走缓存。market: a_share | crypto | crypto_okx"""
    if is_no_cache():
        logger.info("[no_cache] 标的直连: market=%s", market)
        return await _fetch_symbols_from_source(market)

    if not force_refresh:
        age = await repo.get_symbols_age_sec(market)
        if age is not None and age < SYMBOL_TTL_SEC:
            cached = await repo.list_symbols(market)
            if cached:
                logger.debug("命中缓存: market=%s 标的=%d (age=%ds)", market, len(cached), age)
                return cached

    # 缓存失效, 重新拉
    items = await _fetch_symbols_from_source(market)
    await repo.upsert_symbols(market, items)
    return items


async def _fetch_symbols_from_source(market: str) -> List[dict]:
    """直接调数据源, 不读不写缓存。"""
    if market == "a_share":
        return await a_share.list_a_stocks(use_cache=True)
    if market == "crypto_okx":
        return await crypto_src.list_futures(provider="okx")
    # crypto (默认 binance, auto 会回退)
    try:
        return await crypto_src.list_futures(provider="binance")
    except Exception as e:
        logger.warning("Binance 拉取失败, 回退 OKX: %s", e)
        return await crypto_src.list_futures(provider="okx")


# ============== K 线 ==============
async def get_or_fetch_klines(
    market: str,
    symbol: str,
    interval: str,
    limit: int = 200,
    force_refresh: bool = False,
) -> List[list]:
    """获取 K 线: 缓存优先, 增量更新。

    强制刷新: 完全丢掉缓存重新拉 (用于 '清空缓存' 按钮)。
    默认: 查缓存最后时间, 增量拉新数据合并。
    no_cache=True: 直拉直回, 不读不写缓存。
    """
    if is_no_cache():
        logger.debug("[no_cache] K 线直连: %s %s %s", market, symbol, interval)
        return await _fetch_klines_from_source(market, symbol, interval, limit)
    if force_refresh:
        # 删除该 symbol 的缓存, 走全量拉
        from cache.db import KlineRow
        from sqlalchemy import delete, and_
        from cache.db import get_session
        async with await get_session() as s:
            await s.execute(
                delete(KlineRow).where(
                    and_(
                        KlineRow.market == market,
                        KlineRow.symbol == symbol,
                        KlineRow.interval == interval,
                    )
                )
            )
            await s.commit()
        rows = await _fetch_klines_from_source(market, symbol, interval, limit)
        if rows:
            await repo.upsert_klines(market, symbol, interval, rows)
        return rows

    # 1) 看缓存新鲜度 + 数量是否够
    last_t = await repo.get_last_open_time(market, symbol, interval)
    if last_t is not None and (time.time() - last_t) < KLINE_FRESH_SEC:
        cached = await repo.get_klines(market, symbol, interval, limit=limit, ascending=True)
        # 缓存足够 (新且够数量) -> 直接返回
        if cached and len(cached) >= limit:
            return cached

    # 2) 缓存不够新或不够多, 增量拉
    interval_sec = INTERVAL_SECONDS.get(interval, 60)
    start_time = None
    if last_t is not None:
        start_time = last_t + interval_sec  # 下一根 K 线起点

    # 如果缓存里没数据或者数量明显不够, 走全量拉 (避免无限增量)
    if start_time is None:
        new_rows = await _fetch_klines_from_source(
            market, symbol, interval, limit, start_time=None
        )
    else:
        new_rows = await _fetch_klines_from_source(
            market, symbol, interval, limit, start_time=start_time
        )
    if new_rows:
        await repo.upsert_klines(market, symbol, interval, new_rows)

    # 3) 合并: 缓存里所有 + 增量, 取最近 limit 根
    return await repo.get_klines(market, symbol, interval, limit=limit, ascending=True)


async def _fetch_klines_from_source(
    market: str,
    symbol: str,
    interval: str,
    limit: int,
    start_time: Optional[int] = None,
) -> List[list]:
    """直接调数据源, 统一返回秒级时间戳。"""
    if market == "a_share":
        rows = await a_share.fetch_a_share_kline(symbol, interval, limit)
        # A 股 fetch 已经是秒级
        return rows
    if market == "crypto_okx":
        rows = await crypto_src.fetch_kline(symbol, interval, limit, provider="okx")
    else:
        rows = await crypto_src.fetch_kline(symbol, interval, limit, provider="binance")
    # Crypto 原始是毫秒, 转秒
    return [[int(r[0] / 1000), *r[1:]] for r in rows]


# ============== 便捷接口 ==============
async def prewarm_market(market: str, intervals: List[str], max_symbols: int = 300):
    """预热: 拉若干个标的前 N 根 K 线进缓存。

    用于启动时把热门股/币的 K 线准备好, 用户扫描时直接读缓存。
    """
    symbols = await get_or_fetch_symbols(market)
    if max_symbols and len(symbols) > max_symbols:
        symbols = symbols[:max_symbols]

    sem = asyncio.Semaphore(4)

    async def _warm_one(sym_info):
        async with sem:
            for iv in intervals:
                try:
                    await get_or_fetch_klines(market, sym_info["symbol"], iv, limit=200)
                except Exception as e:
                    logger.debug("预热失败 %s %s: %s", sym_info["symbol"], iv, e)

    logger.info("开始预热: market=%s 标的=%d 周期=%s", market, len(symbols), intervals)
    await asyncio.gather(*[_warm_one(s) for s in symbols])
    logger.info("预热完成: market=%s", market)


async def prewarm_all(intervals: Optional[List[str]] = None, max_symbols: int = 200):
    """预热所有市场 (a_share / crypto / crypto_okx), 用于前端 '启动自预热' 按钮。

    并行跑, 互不阻塞。 单个市场失败不影响其他。

    进度通过模块级 _prewarm_state 暴露给前端查询/取消。
    """
    intervals = intervals or ["1d"]
    markets = ["a_share", "crypto", "crypto_okx"]
    global _prewarm_state
    _prewarm_state = {
        "running": True,
        "started_at": time.time(),
        "intervals": intervals,
        "max_symbols": max_symbols,
        "markets": markets,
        "per_market": {m: {"done": 0, "total": 0, "errored": 0} for m in markets},
        "cancelling": False,
    }

    async def _safe(m):
        try:
            syms = await get_or_fetch_symbols(m)
            total = min(len(syms), max_symbols)
            _prewarm_state["per_market"][m]["total"] = total
            sem = asyncio.Semaphore(4)

            async def _one(sym):
                if _prewarm_state.get("cancelling"):
                    return
                async with sem:
                    if _prewarm_state.get("cancelling"):
                        return
                    for iv in intervals:
                        try:
                            await get_or_fetch_klines(m, sym["symbol"], iv, limit=200)
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            _prewarm_state["per_market"][m]["errored"] += 1
                            logger.debug("预热失败 %s %s %s: %s", m, sym["symbol"], iv, e)
                    _prewarm_state["per_market"][m]["done"] += 1

            await asyncio.gather(*[_one(s) for s in syms[:max_symbols]])
        except asyncio.CancelledError:
            logger.info("[prewarm_all] %s 被取消", m)
            raise
        except Exception as e:
            logger.warning("预热 %s 失败: %s", m, e)

    try:
        await asyncio.gather(*[_safe(m) for m in markets])
    except asyncio.CancelledError:
        logger.info("[prewarm_all] 任务被取消")
    finally:
        _prewarm_state["running"] = False
        _prewarm_state["finished_at"] = time.time()
        if _prewarm_state.get("cancelling"):
            _prewarm_state["cancelled"] = True


# ===== 预热状态 (单实例, 供前端查询/取消) =====
_prewarm_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "intervals": [],
    "max_symbols": 0,
    "markets": [],
    "per_market": {},
    "cancelling": False,
    "cancelled": False,
}
_prewarm_task: Optional[asyncio.Task] = None
_prewarm_lock = asyncio.Lock()


def get_prewarm_state() -> dict:
    """返回当前预热状态 (浅拷贝, 防止外部修改)。"""
    return dict(_prewarm_state)


def is_prewarming() -> bool:
    return bool(_prewarm_state.get("running"))


async def start_prewarm(intervals: Optional[List[str]] = None, max_symbols: int = 200) -> dict:
    """启动后台预热 (幂等: 已运行则直接返回当前状态)。"""
    global _prewarm_task
    async with _prewarm_lock:
        if is_prewarming():
            return {"started": False, "already_running": True, "state": get_prewarm_state()}
        _prewarm_state["cancelling"] = False
        _prewarm_state["cancelled"] = False
        _prewarm_task = asyncio.create_task(prewarm_all(intervals, max_symbols))
        return {"started": True, "state": get_prewarm_state()}


async def stop_prewarm() -> dict:
    """取消正在运行的预热。 没有运行则 noop。"""
    global _prewarm_task
    async with _prewarm_lock:
        if not is_prewarming():
            return {"stopped": False, "reason": "not_running"}
        _prewarm_state["cancelling"] = True
        if _prewarm_task is not None and not _prewarm_task.done():
            _prewarm_task.cancel()
        return {"stopped": True, "state": get_prewarm_state()}
    logger.info("预热完成")
