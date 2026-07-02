"""
FastAPI 主入口
==============
提供:
  GET  /api/health                    健康检查
  GET  /api/indicators                指标+形态+支持周期元数据
  GET  /api/a-stocks/list             A 股列表
  GET  /api/a-stocks/kline            单只 A 股 K 线
  GET  /api/crypto/list               Binance 永续合约列表
  GET  /api/crypto/kline              单只币 K 线
  POST /api/scan                      扫描 (按规则)
  GET  /api/patterns                  列出所有支持的形态 (旧 BOLL 兼容)

静态资源 (frontend/) 由根路由直接提供, PWA 一体化。
"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# --------- 启动期错误捕获 (终端闪退时能留底) ---------
# 任何 import / 初始化阶段的异常都写到 data/logs/{date}/startup-error.log
# 这样即使 .bat 启动后窗口秒关, 也能从文件读完整堆栈。
_BACKEND_ROOT = Path(__file__).resolve().parent
_LOG_DIR = _BACKEND_ROOT / "data" / "logs" / datetime.now().strftime("%Y-%m-%d")
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_STARTUP_LOG = _LOG_DIR / "startup-error.log"
import time as _start_t
_log_age_h = ((_start_t.time() - _STARTUP_LOG.stat().st_mtime) / 3600) if _STARTUP_LOG.exists() else 0


def _log_startup_error(tag: str, exc: BaseException):
    """把启动期异常写进日志文件 (含完整 traceback)。"""
    try:
        with open(_STARTUP_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n[{datetime.now().isoformat(timespec='seconds')}] {tag}\n")
            f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            f.write("\n")
    except Exception:
        pass  # 写日志失败别再炸


def _truncate_stale_log():
    """启动时清掉 >24h 前的旧日志, 避免误导 (上次修复前的 crash 仍躺在文件里)。"""
    try:
        if _STARTUP_LOG.exists():
            import time as _t
            age_h = (_t.time() - _STARTUP_LOG.stat().st_mtime) / 3600
            if age_h > 24:
                _STARTUP_LOG.unlink()
                return
        # 写一行"启动 OK"标记, 后面新 crash 才能跟这个对照
        with open(_STARTUP_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] STARTUP OK (上一条 = 距今 {age_h:.1f}h 内或刚截断)\n")
    except Exception:
        pass


# 启动时先截断/标记日志 (import 失败也无所谓, 我们已经定义好了)
_truncate_stale_log()


try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field

    from data_sources import a_share, crypto
    from indicators import compute_boll, boll_to_dicts
    from indicators.registry import list_indicators, list_intervals, market_supports_interval
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
    import configs as app_configs
except Exception as _e:
    _log_startup_error("IMPORT", _e)
    print(f"[FATAL] 导入阶段失败: {_e}", file=sys.stderr)
    print(f"[FATAL] 详细堆栈: {_STARTUP_LOG}", file=sys.stderr)
    raise


# --------- 日志: 同时输出到 stdout 和按日期文件 ---------
_APP_LOG = _LOG_DIR / "app.log"
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
try:
    _fh = logging.FileHandler(_APP_LOG, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(_fh)
except Exception as _e:
    print(f"[WARN] 没法挂文件日志 handler: {_e}", file=sys.stderr)
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
    """支持的形态定义 (向后兼容: 仅 BOLL 形态)。"""
    return {
        "patterns": [
            {"key": k, "label": v}
            for k, v in PATTERN_LABELS.items()
        ]
    }


# --------- 指标 + 形态 + 支持周期 (新) ---------
@app.get("/api/indicators")
async def api_indicators(market: str = Query("a_share")):
    """返回前端规则 UI 需要的全部元数据:
      - indicators: 所有指标 + 每指标的所有形态 (含 value_required / value_label)
      - intervals : 该 market 支持的周期 (按数据源过滤)
      - markets   : 支持的市场 + 它们各自的周期
    """
    m = (market or "").lower().strip()
    # 把 crypto / crypto_okx / crypto_binance 都归到 crypto 系列
    if m in ("crypto", "crypto_okx", "crypto_binance", "binance", "okx"):
        # 取一个合理的 provider, okx 系列按 okx 暴露 (OKX 没有 3d)
        provider = "okx" if m in ("crypto_okx", "okx") else "binance"
        intervals = crypto.supported_intervals(provider)
    else:
        intervals = a_share.supported_intervals()

    markets = {
        "a_share": a_share.supported_intervals(),
        "crypto": crypto.supported_intervals("binance"),
        "crypto_okx": crypto.supported_intervals("okx"),
        "crypto_binance": crypto.supported_intervals("binance"),
    }
    return {
        "indicators": list_indicators(),
        "intervals": intervals,
        "markets": markets,
        "market": m or "a_share",
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
    """单条规则 (新 schema)。

    兼容旧用法: 只填 interval + pattern (默认 indicator='boll')。
    """
    interval: str = Field(..., description="时间周期, 如 5m / 15m / 1h / 1d")
    pattern: str = Field(..., description="形态 key, 如 cross_mid_up")
    # 新字段 (可选)
    indicator: Optional[str] = Field(None, description="指标 key, 如 boll / ma / macd / rsi / kdj / vol / price")
    value: Optional[float] = Field(None, description="数值阈值 (vol/price/rsi/kdj 阈值时用)")
    lookback: Optional[int] = Field(None, ge=10, le=2000, description="回看 K 线数")
    match_count: Optional[int] = Field(None, ge=1, le=500, description="需要多少根 K 线匹配")


class ScanSchema(BaseModel):
    market: str = Field(..., description="a_share | crypto | crypto_okx")
    rules: List[RuleSchema]
    combine: str = Field("all", description="all (AND) | any (OR)")
    symbols: Optional[List[str]] = None
    limit: int = Field(200, ge=20, le=1000)
    concurrency: int = Field(8, ge=1, le=32)
    tag_filters: Optional[Dict[str, List[str]]] = Field(
        None,
        description="标的预筛标签: {'exchange':['sh'],'board':['star'],'quality':['margin']}, 同 key 内 OR, 跨 key AND",
    )
    kline_filter_enabled: bool = Field(
        True,
        description="K 线筛选总开关: True=按 pipeline 过滤; False=跳过 pipeline, 把标的预筛池全部当命中",
    )


def _to_interval_rule(r: RuleSchema) -> IntervalRule:
    return IntervalRule(
        interval=r.interval,
        indicator=r.indicator or "boll",
        pattern=r.pattern,
        value=r.value,
        lookback=r.lookback if r.lookback is not None else 200,
        match_count=r.match_count if r.match_count is not None else 1,
    )


# --------- 扫描筛选维度定义 (按市场) ---------
# 标签 key 是 group key; 同一 group 内多选 = OR; 跨 group 多选 = AND。
# 后端 _apply_tag_filters 严格按此定义匹配。
_FILTER_OPTIONS = {
    "a_share": {
        "groups": [
            {"key": "exchange", "label": "交易所", "multi": True, "options": [
                {"key": "sh", "label": "上交所"},
                {"key": "sz", "label": "深交所"},
                {"key": "bj", "label": "北交所"},
            ]},
            {"key": "board", "label": "板块", "multi": True, "options": [
                {"key": "main",    "label": "主板"},
                {"key": "star",    "label": "科创板"},
                {"key": "chinext", "label": "创业板"},
                {"key": "bse",     "label": "北证"},
            ]},
            {"key": "warning", "label": "风险警示", "multi": True, "options": [
                {"key": "st", "label": "ST/*ST"},
            ]},
            {"key": "quality", "label": "资金/质量", "multi": True, "options": [
                {"key": "margin", "label": "双融"},
                {"key": "blue",   "label": "蓝筹"},
                {"key": "white",  "label": "白马"},
            ]},
        ],
        "hint": "组内任选其一即纳入初筛, 跨组之间为\"且\"关系。",
    },
    "crypto": {
        "groups": [
            {"key": "bluechip", "label": "主流币", "multi": True, "options": [
                {"key": "bluechip_yes", "label": "主流币"},
                {"key": "bluechip_no",  "label": "非主流"},
            ]},
            {"key": "quote", "label": "计价币种", "multi": True, "options": [
                {"key": "quote_USDT", "label": "USDT"},
                {"key": "quote_USDC", "label": "USDC"},
            ]},
        ],
        "hint": "组内任选其一即纳入初筛, 跨组之间为\"且\"关系。",
    },
    "crypto_okx": {
        "groups": [
            {"key": "bluechip", "label": "主流币", "multi": True, "options": [
                {"key": "bluechip_yes", "label": "主流币"},
                {"key": "bluechip_no",  "label": "非主流"},
            ]},
        ],
        "hint": "OKX 仅 USDT 永续, 无需选计价币种。",
    },
}


@app.get("/api/scan/filter-options")
async def api_scan_filter_options(market: str = Query("a_share")):
    """返回该市场可用的复选筛选维度定义。

    前端拿到后渲染复选框, 收集 {group_key: [option_key, ...]} 提交到 /api/scan。
    """
    m = market.lower().strip()
    if m in ("okx", "crypto_okx"):
        m = "crypto_okx"
    elif m in ("binance", "crypto"):
        m = "crypto"
    elif m in ("a", "a-share", "a_share", "ashare"):
        m = "a_share"
    cfg = _FILTER_OPTIONS.get(m)
    if not cfg:
        raise HTTPException(400, f"未知市场: {market}")
    return {"market": m, **cfg}


@app.post("/api/scan")
async def api_scan(body: ScanSchema):
    # K 线筛选关闭时, 允许空规则 (后端会把标的预筛池全部输出)
    if body.kline_filter_enabled and not body.rules:
        raise HTTPException(400, "至少需要一条规则")
    req = ScanRequest(
        market=body.market,
        rules=[_to_interval_rule(r) for r in body.rules],
        combine=body.combine,
        symbols=body.symbols,
        limit=body.limit,
        concurrency=body.concurrency,
        tag_filters=body.tag_filters,
        kline_filter_enabled=body.kline_filter_enabled,
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

    if body.kline_filter_enabled and not body.rules:
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
        rules=[_to_interval_rule(r) for r in body.rules],
        combine=body.combine,
        symbols=body.symbols,
        limit=body.limit,
        concurrency=body.concurrency,
        tag_filters=body.tag_filters,
        progress_cb=on_progress,
        kline_filter_enabled=body.kline_filter_enabled,
    )

    async def _runner():
        try:
            result = await scan(req)
            queue.put_nowait(("done", {
                "hit_count": result["hit_count"],
                "elapsed_sec": result["elapsed_sec"],
                "errors": result["errors"],
                "total": result["total_symbols"],
                # K线筛选关闭时, progress_cb 没传 hit, 必须把全部 hit 在 done 事件里补齐
                "hits": result["hits"],
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


# --------- 规则配置 (多个配置, 每个一组规则) ---------
@app.get("/api/configs")
async def api_configs_list(market: Optional[str] = Query(None)):
    """列出规则配置 + 当前选中的 id。

    market: 给定时只返回该市场的配置, selected_id 也会按市场选。
            不给时返回所有市场 (兼容老调用)。
    """
    return app_configs.list_configs(market=market)


class ConfigCreateSchema(BaseModel):
    name: Optional[str] = "新配置"
    market: Optional[str] = "a_share"   # a_share | crypto | crypto_binance | crypto_okx
    rules: Optional[List[dict]] = None


@app.post("/api/configs")
async def api_configs_create(body: ConfigCreateSchema):
    """新建一个配置, 默认 1 条规则。 body: {name?: str, market?: str, rules?: list}"""
    cfg = app_configs.create_config(
        name=body.name or "新配置",
        market=body.market or "a_share",
        rules=body.rules,
    )
    return cfg


class ConfigPatchSchema(BaseModel):
    name: Optional[str] = None
    rules: Optional[List[dict]] = None
    market: Optional[str] = None


@app.patch("/api/configs/{cid}")
async def api_configs_patch(cid: str, body: ConfigPatchSchema):
    """改名 / 改规则 / 改市场。 body: {name?, rules?, market?}"""
    cfg = app_configs.update_config(
        cid,
        name=body.name,
        rules=body.rules,
        market=body.market,
    )
    if not cfg:
        raise HTTPException(404, f"配置不存在: {cid}")
    return cfg


@app.delete("/api/configs/{cid}")
async def api_configs_delete(cid: str):
    """删除一个配置。 同市场自动选另一个, 同市场最后 1 个不删。"""
    return app_configs.delete_config(cid)


class ConfigSelectSchema(BaseModel):
    id: str


@app.post("/api/configs/select")
async def api_configs_select(body: ConfigSelectSchema):
    """切换当前选中的配置。"""
    ok = app_configs.select_config(body.id)
    if not ok:
        raise HTTPException(404, f"配置不存在: {body.id}")
    return {"selected_id": body.id}


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
    try:
        uvicorn.run(
            "main:app",
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
            reload=os.getenv("RELOAD", "0") == "1",
        )
    except Exception as _e:
        _log_startup_error("UVICORN_RUN", _e)
        print(f"\n[FATAL] 启动失败: {_e}", file=sys.stderr)
        print(f"[FATAL] 详细堆栈: {_STARTUP_LOG}", file=sys.stderr)
        # 让窗口停住, 方便看到错误 (防止 .bat 闪退)
        try:
            input("\n按回车退出...")
        except EOFError:
            pass
        raise
