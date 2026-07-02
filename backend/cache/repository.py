"""
缓存仓储层
==========
提供:
  - upsert_symbols / list_symbols           标的元信息
  - upsert_klines / get_klines / get_last   K 线 (增量更新 / 查询)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Iterable, List, Optional

from sqlalchemy import select, and_, desc, asc, func

from cache.db import KlineRow, SymbolRow, get_session


def _encode_tags(tags) -> Optional[str]:
    """tags: list[str] | None -> JSON 字符串 (空 list 也存为 '[]')。"""
    if tags is None:
        return None
    if isinstance(tags, str):
        return tags
    return json.dumps(list(tags), ensure_ascii=False)


def _decode_tags(raw) -> List[str]:
    """DB 中的 JSON 字符串 -> list[str]。 失败回 []。"""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return list(v) if isinstance(v, list) else []
    except Exception:
        return []


# ============== symbols ==============
async def upsert_symbols(market: str, items: List[dict]):
    """批量写入/更新标的元信息。 items 里的 tags 字段会写入 tags 列。"""
    if not items:
        return
    now = int(time.time())
    async with await get_session() as s:
        rows = [
            SymbolRow(
                market=market,
                symbol=it["symbol"],
                code=it.get("code"),
                name=it.get("name"),
                display=it.get("display"),
                tags=_encode_tags(it.get("tags")),
                last_updated=now,
            )
            for it in items
        ]
        # SQLite 的 ON CONFLICT 需要 dialect 级别 upsert, 这里先 delete 再 insert
        # 对全量列表场景简单可靠
        await s.execute(SymbolRow.__table__.delete().where(SymbolRow.market == market))
        s.add_all(rows)
        await s.commit()


async def list_symbols(market: str) -> List[dict]:
    async with await get_session() as s:
        r = await s.execute(
            select(SymbolRow).where(SymbolRow.market == market).order_by(SymbolRow.symbol)
        )
        return [
            {
                "symbol": row.symbol,
                "code": row.code,
                "name": row.name,
                "display": row.display,
                "tags": _decode_tags(row.tags),
                "last_updated": row.last_updated,
            }
            for row in r.scalars()
        ]


async def get_symbols_age_sec(market: str) -> Optional[int]:
    """返回市场列表的缓存年龄(秒), 没缓存返回 None。"""
    async with await get_session() as s:
        r = await s.execute(
            select(SymbolRow.last_updated).where(SymbolRow.market == market).limit(1)
        )
        ts = r.scalar()
        if ts is None:
            return None
        return int(time.time()) - int(ts)


# ============== klines ==============
async def upsert_klines(market: str, symbol: str, interval: str, rows: List[list]):
    """rows: [[open_time, open, high, low, close, volume], ...] (秒级时间戳)"""
    if not rows:
        return
    async with await get_session() as s:
        # 先按 (market, symbol, interval, open_time) 删旧的, 再插新的
        times = [int(r[0]) for r in rows]
        await s.execute(
            KlineRow.__table__.delete().where(
                and_(
                    KlineRow.market == market,
                    KlineRow.symbol == symbol,
                    KlineRow.interval == interval,
                    KlineRow.open_time.in_(times),
                )
            )
        )
        s.add_all([
            KlineRow(
                market=market,
                symbol=symbol,
                interval=interval,
                open_time=int(r[0]),
                open=float(r[1]),
                high=float(r[2]),
                low=float(r[3]),
                close=float(r[4]),
                volume=float(r[5]),
            )
            for r in rows
        ])
        await s.commit()


async def get_klines(
    market: str,
    symbol: str,
    interval: str,
    limit: int = 200,
    ascending: bool = True,
) -> List[list]:
    """从 DB 读取 K 线。返回 [time, open, high, low, close, volume] (秒级)。"""
    order = asc(KlineRow.open_time) if ascending else desc(KlineRow.open_time)
    async with await get_session() as s:
        r = await s.execute(
            select(KlineRow)
            .where(
                and_(
                    KlineRow.market == market,
                    KlineRow.symbol == symbol,
                    KlineRow.interval == interval,
                )
            )
            .order_by(order)
            .limit(limit)
        )
        rows = r.scalars().all()
    if ascending:
        out = [
            [row.open_time, row.open, row.high, row.low, row.close, row.volume]
            for row in rows
        ]
    else:
        out = [
            [row.open_time, row.open, row.high, row.low, row.close, row.volume]
            for row in reversed(rows)
        ]
    return out


async def get_last_open_time(market: str, symbol: str, interval: str) -> Optional[int]:
    """返回该 (market, symbol, interval) 缓存里最新一根 K 线的 open_time (秒), 无则 None。"""
    async with await get_session() as s:
        r = await s.execute(
            select(KlineRow.open_time)
            .where(
                and_(
                    KlineRow.market == market,
                    KlineRow.symbol == symbol,
                    KlineRow.interval == interval,
                )
            )
            .order_by(desc(KlineRow.open_time))
            .limit(1)
        )
        return r.scalar()


async def get_last_open_times_bulk(
    market: str, symbols: List[str], interval: str
) -> dict[str, Optional[int]]:
    """批量返回每个 symbol 最新 K 线 open_time。 一次 SQL, 替代 N 次单查。

    返回: { symbol: open_time 或 None }
    """
    if not symbols:
        return {}
    async with await get_session() as s:
        # SQLite 用窗口函数取每 symbol 最大的 open_time
        sub = (
            select(
                KlineRow.symbol.label("symbol"),
                func.max(KlineRow.open_time).label("last_t"),
            )
            .where(
                and_(
                    KlineRow.market == market,
                    KlineRow.symbol.in_(symbols),
                    KlineRow.interval == interval,
                )
            )
            .group_by(KlineRow.symbol)
            .subquery()
        )
        r = await s.execute(select(sub.c.symbol, sub.c.last_t))
        out = {row[0]: int(row[1]) if row[1] is not None else None for row in r.all()}
    for sym in symbols:
        out.setdefault(sym, None)
    return out


async def get_klines_bulk(
    market: str,
    symbols: List[str],
    interval: str,
    limit: int = 200,
) -> dict[str, List[list]]:
    """批量读 N 个 symbol 的最新 limit 根 K 线。 一次 SQL。

    用窗口函数 row_number() 按 symbol 分组, 取每 symbol 最近 limit 行 (升序)。
    SQLite 不支持窗口函数 row_number 时回退到 Python 端分组 (每 symbol 单查 LIMIT)。

    返回: { symbol: [[time, open, high, low, close, volume], ...] (升序) 或 [] }
    """
    if not symbols:
        return {}
    out: dict[str, List[list]] = {sym: [] for sym in symbols}

    try:
        async with await get_session() as s:
            # 用窗口函数: 按 symbol 倒序排, 取前 limit 行, 再反转成升序
            rn = func.row_number().over(
                partition_by=KlineRow.symbol,
                order_by=KlineRow.open_time.desc(),
            ).label("rn")
            sub = (
                select(KlineRow, rn)
                .where(
                    and_(
                        KlineRow.market == market,
                        KlineRow.symbol.in_(symbols),
                        KlineRow.interval == interval,
                    )
                )
                .subquery()
            )
            stmt = select(sub).where(sub.c.rn <= limit).order_by(sub.c.symbol, sub.c.open_time.asc())
            r = await s.execute(stmt)
            for row in r.all():
                sym = row.symbol
                out.setdefault(sym, []).append([
                    int(row.open_time), float(row.open), float(row.high),
                    float(row.low), float(row.close), float(row.volume),
                ])
        return out
    except Exception as e:
        # 窗口函数失败 (旧 SQLite / 其它方言): 退回到每 symbol 单查, 但并发用 asyncio.gather
        logger = logging.getLogger(__name__)
        logger.warning("get_klines_bulk 窗口函数路径失败, 回退单查: %s", e)
        results = await asyncio.gather(
            *[get_klines(market, sym, interval, limit=limit, ascending=True) for sym in symbols],
            return_exceptions=True,
        )
        for sym, r in zip(symbols, results):
            if isinstance(r, Exception):
                out[sym] = []
            else:
                out[sym] = r or []
        return out


async def get_row_count(market: str, symbol: str, interval: str) -> int:
    async with await get_session() as s:
        r = await s.execute(
            select(KlineRow).where(
                and_(
                    KlineRow.market == market,
                    KlineRow.symbol == symbol,
                    KlineRow.interval == interval,
                )
            )
        )
        return len(r.scalars().all())


async def list_symbols_with_stats(market: Optional[str] = None) -> List[dict]:
    """列出所有标的 (含每标的 K 线统计)。

    返回: [{market, symbol, name, display, kline_count, intervals, last_kline_time}, ...]
    """
    from sqlalchemy import func

    async with await get_session() as s:
        # 标的列表
        sym_q = select(SymbolRow)
        if market:
            sym_q = sym_q.where(SymbolRow.market == market)
        sym_q = sym_q.order_by(SymbolRow.market, SymbolRow.symbol)
        sym_rows = (await s.execute(sym_q)).scalars().all()

        # K 线聚合
        kline_q = select(
            KlineRow.market,
            KlineRow.symbol,
            func.count().label("cnt"),
            func.count(func.distinct(KlineRow.interval)).label("ivs"),
            func.max(KlineRow.open_time).label("last_t"),
        ).group_by(KlineRow.market, KlineRow.symbol)
        if market:
            kline_q = kline_q.where(KlineRow.market == market)
        kline_rows = (await s.execute(kline_q)).fetchall()

    kline_map = {(r[0], r[1]): (r[2], r[3], r[4]) for r in kline_rows}
    out = []
    for r in sym_rows:
        kc, ivs, last_t = kline_map.get((r.market, r.symbol), (0, 0, None))
        out.append({
            "market": r.market,
            "symbol": r.symbol,
            "code": r.code,
            "name": r.name,
            "display": r.display,
            "kline_count": kc,
            "intervals_count": ivs,
            "last_kline_time": last_t,
            "last_updated": r.last_updated,
        })
    return out


async def delete_klines_for(market: str, symbol: str, interval: Optional[str] = None):
    """删除指定 (market, symbol, [interval]) 的 K 线缓存。

    interval 不传 = 删该标的所有 K 线。
    """
    from cache.db import KlineRow, get_session
    from sqlalchemy import delete, and_

    async with await get_session() as s:
        conditions = [
            KlineRow.market == market,
            KlineRow.symbol == symbol,
        ]
        if interval:
            conditions.append(KlineRow.interval == interval)
        await s.execute(delete(KlineRow).where(and_(*conditions)))
        await s.commit()


async def delete_symbols(market: str, symbols: List[str]):
    """从 symbols 表删除指定标的元信息 (K 线单独用 delete_klines_for 删)。"""
    from cache.db import SymbolRow, get_session
    from sqlalchemy import delete, and_

    if not symbols:
        return
    async with await get_session() as s:
        await s.execute(
            delete(SymbolRow).where(
                and_(
                    SymbolRow.market == market,
                    SymbolRow.symbol.in_(symbols),
                )
            )
        )
        await s.commit()
