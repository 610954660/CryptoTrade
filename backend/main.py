"""
FastAPI 主入口
==============
提供:
  GET  /api/health                    健康检查
  GET  /api/a-stocks/list             A 股列表
  GET  /api/a-stocks/kline            单只 A 股 K 线
  GET  /api/crypto/list               Binance 永续合约列表
  GET  /api/crypto/kline              单只币 K 线
  POST /api/scan                      扫描 (按规则)
  GET  /api/patterns                  列出所有支持的形态

静态资源 (frontend/) 由根路由直接提供, PWA 一体化。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from data_sources import a_share, crypto
from indicators import compute_boll, boll_to_dicts
from scanner.matcher import PATTERN_LABELS
from scanner.service import (
    IntervalRule,
    ScanRequest,
    scan,
)
from cache import db as cache_db
from cache import service as cache_service
from cache import repository as cache_repository
import settings as app_settings

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app")


# --------- 应用生命周期 ---------
@asynccontextmanager
async def lifespan(app: FastAPI):
    proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or os.getenv("ALL_PROXY")
    provider = os.getenv("CRYPTO_PROVIDER", "auto")
    logger.info("=" * 60)
    logger.info("[ENV] HTTPS_PROXY  = %s", proxy or "(未设置 - 走直连)")
    logger.info("[ENV] CRYPTO_PROVIDER = %s", provider)
    logger.info("[ENV] BINANCE_BASE_URLS = %s", os.getenv("BINANCE_BASE_URLS") or "(用默认)")
    logger.info("=" * 60)
    logger.info("[CACHE] 启动时不做预热。缓存仅在用户扫描 / 拉 K 线时按需填充。")
    # 初始化 SQLite 缓存 (建表, 不读任何数据)
    try:
        await cache_db.init_db()
    except Exception as e:
        logger.warning("缓存 DB 初始化失败: %s", e)
    yield
    await crypto.close_client()
    await cache_db.close_db()


app = FastAPI(
    title="CryptoTrade 筛选器",
    description="A 股 / Binance 合约 BOLL 形态筛选",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------- 健康检查 ---------
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "proxy": os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or None,
        "provider": os.getenv("CRYPTO_PROVIDER", "auto"),
    }


@app.get("/api/status")
async def status():
    """诊断端点: 主动探测 Binance / OKX 是否可用, 方便前端展示。"""
    # 优先用 settings 里配的, 没配才回退到 env
    p = app_settings.get_proxy()
    proxy_repr = p["url"] if (p.get("enabled") and p.get("url")) else (
        os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or None
    )
    out = {"binance": None, "okx": None, "proxy": proxy_repr}

    async def _probe(name: str, coro):
        try:
            await coro
            out[name] = "ok"
        except Exception as e:
            out[name] = f"fail: {e.__class__.__name__} {str(e)[:80]}"

    await _probe("binance", crypto._binance_list(use_cache=False))
    await _probe("okx",     crypto._okx_list(use_cache=False))
    return out


# --------- 形态列表 ---------
@app.get("/api/patterns")
async def list_patterns():
    """支持的形态定义。"""
    return {
        "patterns": [
            {"key": k, "label": v}
            for k, v in PATTERN_LABELS.items()
        ]
    }


# --------- A 股 ---------
@app.get("/api/a-stocks/list")
async def api_a_stocks_list(limit: int = 0, use_cache: bool = Query(True)):
    items = await cache_service.get_or_fetch_symbols("a_share")
    if limit > 0:
        items = items[:limit]
    return {"count": len(items), "items": items, "from_cache": use_cache}


@app.get("/api/a-stocks/hs300")
async def api_a_stocks_hs300():
    """沪深 300 成分股, 适合中速扫描。"""
    items = await a_share.list_hs300()
    return {"count": len(items), "items": items}


@app.get("/api/a-stocks/kline")
async def api_a_stocks_kline(
    symbol: str = Query(..., min_length=6, max_length=6),
    interval: str = Query("1d"),
    limit: int = Query(200, ge=10, le=1000),
    with_boll: bool = Query(True),
    force_refresh: bool = Query(False),
):
    try:
        klines = await cache_service.get_or_fetch_klines(
            "a_share", symbol, interval, limit, force_refresh
        )
    except Exception as e:
        raise HTTPException(502, f"获取 K 线失败: {e}")
    if not klines:
        raise HTTPException(404, "无 K 线数据")
    payload = {
        "symbol": symbol,
        "interval": interval,
        "klines": klines,
    }
    if with_boll:
        points = compute_boll(klines)
        payload["boll"] = boll_to_dicts(points)
    return payload


# --------- Crypto ---------
@app.get("/api/crypto/list")
async def api_crypto_list(limit: int = 0, provider: str = Query("binance")):
    market = "crypto_okx" if provider == "okx" else "crypto"
    try:
        items = await cache_service.get_or_fetch_symbols(market)
    except Exception as e:
        raise HTTPException(502, f"获取列表失败: {e}")
    if limit > 0:
        items = items[:limit]
    return {"count": len(items), "items": items, "provider": provider}


@app.get("/api/crypto/kline")
async def api_crypto_kline(
    symbol: str = Query(..., description="binance: BTCUSDT / okx: BTC-USDT-SWAP"),
    interval: str = Query("1h"),
    limit: int = Query(200, ge=10, le=1000),
    with_boll: bool = Query(True),
    provider: str = Query("binance"),
    force_refresh: bool = Query(False),
):
    market = "crypto_okx" if provider == "okx" else "crypto"
    try:
        klines = await cache_service.get_or_fetch_klines(
            market, symbol, interval, limit, force_refresh
        )
    except Exception as e:
        raise HTTPException(502, f"获取 K 线失败: {e}")
    if not klines:
        raise HTTPException(404, "无 K 线数据")
    payload = {
        "symbol": symbol,
        "interval": interval,
        "klines": klines,
        "provider": provider,
    }
    if with_boll:
        points = compute_boll(klines)
        payload["boll"] = boll_to_dicts(points)
    return payload


# --------- 扫描 ---------
class RuleSchema(BaseModel):
    interval: str = Field(..., description="时间周期, 如 5m / 15m / 1h / 1d")
    pattern: str = Field(..., description="形态 key, 如 cross_mid_up")


class ScanSchema(BaseModel):
    market: str = Field(..., description="a_share | crypto | crypto_okx")
    rules: List[RuleSchema]
    combine: str = Field("all", description="all (AND) | any (OR)")
    symbols: Optional[List[str]] = None
    limit: int = Field(100, ge=20, le=500)
    concurrency: int = Field(8, ge=1, le=32)


@app.post("/api/scan")
async def api_scan(body: ScanSchema):
    if not body.rules:
        raise HTTPException(400, "至少需要一条规则")
    req = ScanRequest(
        market=body.market,
        rules=[IntervalRule(r.interval, r.pattern) for r in body.rules],
        combine=body.combine,
        symbols=body.symbols,
        limit=body.limit,
        concurrency=body.concurrency,
    )
    try:
        result = await scan(req)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("扫描失败")
        raise HTTPException(500, f"扫描失败: {e}")
    return result


@app.post("/api/scan/stream")
async def api_scan_stream(body: ScanSchema):
    """SSE 端点: 实时推送扫描进度和命中事件。

    事件类型:
      progress  -> {done, total, percent}
      hit       -> {symbol, name, ...}
      done      -> {hit_count, elapsed_sec, errors}
      error     -> {message}
    """
    import asyncio
    import json

    from fastapi.responses import StreamingResponse

    if not body.rules:
        raise HTTPException(400, "至少需要一条规则")

    queue: asyncio.Queue = asyncio.Queue()

    def on_progress(done, total, latest_hit):
        # latest_hit 是 SymbolHit 或 None
        hit_dict = None
        if latest_hit is not None:
            hit_dict = {
                "symbol": latest_hit.symbol,
                "name": latest_hit.name,
                "display": latest_hit.display,
                "last_close": latest_hit.last_close,
                "last_mid": latest_hit.last_mid,
                "rules": latest_hit.rules,
            }
        queue.put_nowait(("progress", {"done": done, "total": total,
                                       "percent": round(done / total * 100, 1) if total else 0,
                                       "latest_hit": hit_dict}))

    req = ScanRequest(
        market=body.market,
        rules=[IntervalRule(r.interval, r.pattern) for r in body.rules],
        combine=body.combine,
        symbols=body.symbols,
        limit=body.limit,
        concurrency=body.concurrency,
        progress_cb=on_progress,
    )

    async def _runner():
        try:
            result = await scan(req)
            queue.put_nowait(("done", {
                "hit_count": result["hit_count"],
                "elapsed_sec": result["elapsed_sec"],
                "errors": result["errors"],
                "total": result["total_symbols"],
            }))
        except Exception as e:
            logger.exception("扫描失败")
            queue.put_nowait(("error", {"message": str(e)}))
        finally:
            queue.put_nowait(("__close__", None))

    async def _event_stream():
        asyncio.create_task(_runner())
        yield f"event: ready\ndata: {json.dumps({'ok': True})}\n\n"
        while True:
            ev, data = await queue.get()
            if ev == "__close__":
                break
            yield f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx 不要缓存
        },
    )


# --------- 缓存管理 ---------
@app.get("/api/cache/stats")
async def api_cache_stats():
    """缓存统计: 行数、磁盘占用、按市场分组。"""
    return await cache_db.stats()


@app.get("/api/cache/symbols")
async def api_cache_symbols(market: Optional[str] = Query(None)):
    """列出缓存中所有标的 (含 K 线统计)。"""
    items = await cache_repository.list_symbols_with_stats(market)
    return {"count": len(items), "items": items}


@app.post("/api/cache/clear")
async def api_cache_clear(market: Optional[str] = Query(None, description="a_share / crypto / crypto_okx, 不传清空全部")):
    await cache_db.clear(market)
    return {"cleared": market or "ALL"}


class DeleteSymbolsSchema(BaseModel):
    market: str
    symbols: List[str]


@app.post("/api/cache/delete-symbols")
async def api_cache_delete_symbols(body: DeleteSymbolsSchema):
    """批量删除指定标的的 symbols 表记录 + 全部 K 线。"""
    from cache import repository as cache_repository
    for sym in body.symbols:
        await cache_repository.delete_klines_for(body.market, sym)
    await cache_repository.delete_symbols(body.market, body.symbols)
    return {"deleted": len(body.symbols)}


class DeleteKlinesSchema(BaseModel):
    market: str
    symbol: str
    interval: Optional[str] = None  # 不传删所有周期


@app.post("/api/cache/delete-klines")
async def api_cache_delete_klines(body: DeleteKlinesSchema):
    """删除某标的的 K 线缓存 (可指定周期)。"""
    from cache import repository as cache_repository
    await cache_repository.delete_klines_for(body.market, body.symbol, body.interval)
    return {"deleted_klines": True, "market": body.market, "symbol": body.symbol, "interval": body.interval}


class RefreshSchema(BaseModel):
    market: str
    symbol: str
    interval: str
    limit: int = 200


@app.post("/api/cache/refresh")
async def api_cache_refresh(body: RefreshSchema):
    """强制刷新某标的某周期的 K 线 (删除旧缓存 + 重新拉)。"""
    from cache import repository as cache_repository
    await cache_repository.delete_klines_for(body.market, body.symbol, body.interval)
    rows = await cache_service.get_or_fetch_klines(
        body.market, body.symbol, body.interval, body.limit
    )
    return {"refreshed": True, "rows": len(rows)}


class WarmupSchema(BaseModel):
    market: str = Field(..., description="a_share / crypto / crypto_okx")
    intervals: List[str] = Field(default=["1d"], description="要预热的周期列表")
    max_symbols: int = Field(50, ge=1, le=500, description="最多预热多少个标的")


@app.post("/api/cache/warmup")
async def api_cache_warmup(body: WarmupSchema):
    """预热: 把指定市场的 N 个标的多周期 K 线提前写进缓存。"""
    import asyncio
    await cache_service.prewarm_market(body.market, body.intervals, body.max_symbols)
    return await cache_db.stats()


# --------- 预热 (一键启动所有市场) ---------
@app.post("/api/cache/prewarm")
async def api_cache_prewarm_all(background: bool = Query(True)):
    """一键预热 a_share / crypto / crypto_okx。

    background=True (默认): 后台跑, 立即返回 {started: True}。
    background=False: 同步等所有市场预热完, 返回 stats。
    """
    import asyncio
    if background:
        # 用可取消的 task
        result = await cache_service.start_prewarm()
        return {
            "started": result.get("started", False),
            "already_running": result.get("already_running", False),
            "note": "后台预热已启动, 可通过 /api/cache/prewarm/status 查看进度",
        }
    await cache_service.prewarm_all()
    return await cache_db.stats()


@app.get("/api/cache/prewarm/status")
async def api_cache_prewarm_status():
    """查询当前预热状态 (含进度)。"""
    return cache_service.get_prewarm_state()


@app.post("/api/cache/prewarm/stop")
async def api_cache_prewarm_stop():
    """停止正在运行的预热。 幂等: 没在跑就 noop。"""
    return await cache_service.stop_prewarm()


# --------- 设置 (持久化在 data/settings.json) ---------
@app.get("/api/settings")
async def api_settings_get():
    """返回完整设置 + 当前生效的代理。"""
    p = app_settings.get_proxy()
    return {
        **app_settings.get_all(),
        "proxy_effective": p,
    }


class SettingsPutSchema(BaseModel):
    proxy: Optional[dict] = None
    runtime: Optional[dict] = None


@app.put("/api/settings")
async def api_settings_put(body: SettingsPutSchema):
    """部分更新设置。"""
    new_state = app_settings.update(proxy=body.proxy, runtime=body.runtime)
    # 代理变了 -> 让 crypto 客户端下次请求时自动重建
    return {
        **new_state,
        "proxy_effective": app_settings.get_proxy(),
    }


@app.post("/api/settings/proxy/test")
async def api_settings_proxy_test():
    """测试当前代理是否可用 (ping Binance futures)。"""
    import httpx
    p = app_settings.get_proxy()
    if not p.get("url"):
        return {"ok": False, "message": "未配置代理 URL"}
    if not p.get("enabled", True):
        return {"ok": False, "message": "代理未启用 (enabled=False)"}
    try:
        async with httpx.AsyncClient(
            proxy=p["url"],
            timeout=httpx.Timeout(8.0, connect=5.0),
            headers={"User-Agent": "Mozilla/5.0 (compatible; CryptoTradeApp/1.0)"},
        ) as cli:
            r = await cli.get("https://fapi.binance.com/fapi/v1/ping")
            if r.status_code == 200:
                return {"ok": True, "message": f"Binance ping 200 (proxy={p['url']})"}
            return {"ok": False, "message": f"Binance 返回 HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "message": f"代理失败: {e.__class__.__name__}: {str(e)[:120]}"}


# --------- 静态前端 ---------


# --------- 静态前端 ---------
# 把 frontend/ 下的 css / js / icons 子目录挂载到根路径,
# 这样 index.html 里写 <link href="/css/style.css"> 能直接命中。
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    for sub in ("css", "js", "icons"):
        sub_path = FRONTEND_DIR / sub
        if sub_path.exists():
            app.mount(f"/{sub}", StaticFiles(directory=str(sub_path)), name=sub)

    @app.get("/")
    async def root_index():
        return FileResponse(str(FRONTEND_DIR / "index.html"))

    @app.get("/index.html")
    async def root_index_html():
        return FileResponse(str(FRONTEND_DIR / "index.html"))

    @app.get("/cache.html")
    async def root_cache_html():
        return FileResponse(str(FRONTEND_DIR / "cache.html"))

    @app.get("/manifest.webmanifest")
    async def pwa_manifest():
        return FileResponse(str(FRONTEND_DIR / "manifest.webmanifest"), media_type="application/manifest+json")

    @app.get("/sw.js")
    async def pwa_sw():
        return FileResponse(str(FRONTEND_DIR / "sw.js"), media_type="application/javascript")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "0") == "1",
    )
