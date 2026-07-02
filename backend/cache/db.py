"""
SQLite 异步缓存
================
- 使用 SQLAlchemy 2.0 + aiosqlite
- 数据库文件: backend/.cache/scanner.db
- 表: symbols (标的元信息) + klines (K 线)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from sqlalchemy import Column, Index, Integer, String, Float, BigInteger, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)
DB_PATH = CACHE_DIR / "scanner.db"
DB_URL = f"sqlite+aiosqlite:///{DB_PATH}"


class Base(DeclarativeBase):
    pass


class SymbolRow(Base):
    """标的元信息 (股票/合约列表)。"""
    __tablename__ = "symbols"

    market = Column(String(16), primary_key=True)
    symbol = Column(String(64), primary_key=True)
    code = Column(String(16), nullable=True)        # A 股 code 冗余
    name = Column(String(128), nullable=True)
    display = Column(String(160), nullable=True)
    tags = Column(String(256), nullable=True)       # JSON 数组字符串, e.g. '["双融","蓝筹"]'
    last_updated = Column(BigInteger, nullable=False)  # 秒级时间戳


class KlineRow(Base):
    """单根 K 线。"""
    __tablename__ = "klines"

    market = Column(String(16), primary_key=True)
    symbol = Column(String(64), primary_key=True)
    interval = Column(String(8), primary_key=True)
    open_time = Column(BigInteger, primary_key=True)  # 秒级时间戳
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)

    __table_args__ = (
        Index("ix_klines_lookup", "market", "symbol", "interval", "open_time"),
    )


# --------- 引擎 / Session 工厂 ---------
_engine = None
_session_factory: Optional[async_sessionmaker] = None


async def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            DB_URL,
            echo=False,
            future=True,
            connect_args={"check_same_thread": False},
        )
    return _engine


async def init_db():
    """建表 (首次启动时调用)。 旧表加列走幂等 ALTER。"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # 幂等迁移: 给已存在的 symbols 表补 tags 列 (create_all 不会改老表)
    await _ensure_column("symbols", "tags", "VARCHAR(256)")
    logger.info("缓存 DB 初始化完成: %s", DB_PATH)


async def _ensure_column(table: str, column: str, col_type: str):
    """若指定列不存在, ALTER TABLE 加上去。 幂等。"""
    try:
        async with await get_session() as s:
            r = await s.execute(text(f"PRAGMA table_info({table})"))
            cols = {row[1] for row in r.fetchall()}
            if column not in cols:
                await s.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                await s.commit()
                logger.info("迁移: %s 表新增列 %s (%s)", table, column, col_type)
    except Exception as e:
        logger.warning("迁移 %s.%s 失败: %s", table, column, e)


async def get_session() -> AsyncSession:
    """获取新的 session。"""
    global _session_factory
    if _session_factory is None:
        engine = await get_engine()
        _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory()


async def close_db():
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    _session_factory = None


# --------- 统计信息 ---------
async def stats() -> dict:
    """返回缓存统计: 各表行数、磁盘大小。"""
    out = {"db_path": str(DB_PATH), "db_size_mb": 0.0, "tables": {}}
    try:
        out["db_size_mb"] = round(DB_PATH.stat().st_size / 1024 / 1024, 3)
    except FileNotFoundError:
        return out
    try:
        async with await get_session() as s:
            for tbl in ("symbols", "klines"):
                r = await s.execute(text(f"SELECT COUNT(*) FROM {tbl}"))
                out["tables"][tbl] = r.scalar() or 0
            # 按 market 统计 K 线
            r = await s.execute(
                text("SELECT market, COUNT(*) AS n, COUNT(DISTINCT symbol) AS sym, COUNT(DISTINCT interval) AS ivs FROM klines GROUP BY market")
            )
            out["klines_by_market"] = [
                {"market": row[0], "rows": row[1], "symbols": row[2], "intervals": row[3]}
                for row in r.fetchall()
            ]
    except Exception as e:
        out["error"] = str(e)
    return out


async def clear(market: Optional[str] = None):
    """清空缓存。market 不为空时只清该市场。"""
    async with await get_session() as s:
        if market:
            await s.execute(delete(SymbolRow).where(SymbolRow.market == market))
            await s.execute(delete(KlineRow).where(KlineRow.market == market))
        else:
            await s.execute(delete(SymbolRow))
            await s.execute(delete(KlineRow))
        await s.commit()
    logger.info("缓存已清空: market=%s", market or "ALL")
