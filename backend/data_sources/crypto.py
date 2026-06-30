"""
Crypto 数据源
=============
默认走 Binance USDT 永续合约; 大陆地区 451 时自动回退到 OKX。

支持:
  - 代理: 读环境变量 HTTPS_PROXY / HTTP_PROXY / ALL_PROXY
  - 多 endpoint 镜像: BINANCE_BASE_URLS 逗号分隔
  - OKX 备用: 用环境变量 CRYPTO_PROVIDER=okx 直接切换
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import List, Optional

import httpx

from settings import get_proxy

logger = logging.getLogger(__name__)


# --------- Binance 配置 ---------
# 多个 endpoint 镜像, 首个失败时按顺序回退
DEFAULT_BINANCE_BASES = [
    "https://fapi.binance.com",
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
    "https://data-api.binance.vision",  # 第三方镜像, 部分时段可用
]
BINANCE_INTERVAL_MAP = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h", "1d": "1d", "3d": "3d",
    "1w": "1w", "1M": "1M",
}


# --------- OKX 配置 ---------
OKX_BASE = "https://www.okx.com"
# OKX bar 取值
OKX_BAR_MAP = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1H", "2h": "2H", "4h": "4H", "1d": "1D", "1w": "1W",
    "1M": "1M",
    # OKX 不支持 3d, 用 1D 凑合 (会标注)
}


# --------- 共享 httpx 客户端 (代理 / 共享连接池) ---------
_BINANCE_CLIENTS: dict = {}   # base -> httpx.AsyncClient
_OKX_CLIENT: Optional[httpx.AsyncClient] = None
_PROXY_CACHE: Optional[str] = "__unset__"        # 上次 httpx 用的 url
_PROXY_ENABLED_CACHE: Optional[bool] = None
_PROVIDER: str = os.getenv("CRYPTO_PROVIDER", "auto").lower()  # auto | binance | okx

# 缓存: provider -> [(base, key) -> list[dict]]
_LIST_CACHE: dict = {}


def _proxy() -> Optional[str]:
    """读 settings 中的代理配置。

    enabled=False 时返回 None (直连), 即使 url 有值。
    """
    p = get_proxy()
    if not p.get("enabled", True):
        return None
    return p.get("url") or None


async def _binance_client(base: str) -> httpx.AsyncClient:
    global _PROXY_CACHE
    proxy = _proxy()
    # 代理变了 -> 重建客户端
    if base not in _BINANCE_CLIENTS or _PROXY_CACHE != proxy:
        if base in _BINANCE_CLIENTS:
            await _BINANCE_CLIENTS[base].aclose()
        _BINANCE_CLIENTS[base] = httpx.AsyncClient(
            base_url=base,
            timeout=httpx.Timeout(15.0, connect=10.0),
            headers={"User-Agent": "Mozilla/5.0 (compatible; CryptoTradeApp/1.0)"},
            proxy=proxy,
        )
        _PROXY_CACHE = proxy
    return _BINANCE_CLIENTS[base]


async def _okx_client() -> httpx.AsyncClient:
    global _OKX_CLIENT, _PROXY_CACHE
    proxy = _proxy()
    if _OKX_CLIENT is None or _PROXY_CACHE != proxy:
        if _OKX_CLIENT is not None:
            await _OKX_CLIENT.aclose()
        _OKX_CLIENT = httpx.AsyncClient(
            base_url=OKX_BASE,
            timeout=httpx.Timeout(15.0, connect=10.0),
            headers={"User-Agent": "Mozilla/5.0 (compatible; CryptoTradeApp/1.0)"},
            proxy=proxy,
        )
        _PROXY_CACHE = proxy
    return _OKX_CLIENT


async def close_client():
    global _OKX_CLIENT
    for c in _BINANCE_CLIENTS.values():
        await c.aclose()
    _BINANCE_CLIENTS.clear()
    if _OKX_CLIENT is not None:
        await _OKX_CLIENT.aclose()
        _OKX_CLIENT = None


# --------- 对外支持的周期 (前端规则 UI 用) ---------
# OKX 不支持 3d, 所以 crypto 标 "全周期但排除 3d"
# Binance 全部支持, 取全集
BINANCE_SUPPORTED_INTERVALS = list(BINANCE_INTERVAL_MAP.keys())
OKX_SUPPORTED_INTERVALS = [k for k in OKX_BAR_MAP.keys()]
# auto/binance 默认拿 Binance 全集; okx 用 OKX
CRYPTO_SUPPORTED_INTERVALS = {
    "binance": BINANCE_SUPPORTED_INTERVALS,
    "okx":     OKX_SUPPORTED_INTERVALS,
    "auto":    BINANCE_SUPPORTED_INTERVALS,   # auto 优先试 binance, 暴露其全集
}


def supported_intervals(provider: Optional[str] = None) -> List[str]:
    """crypto 数据源支持的周期。 provider: binance | okx | auto | None (auto)"""
    p = (provider or "auto").lower()
    return list(CRYPTO_SUPPORTED_INTERVALS.get(p, BINANCE_SUPPORTED_INTERVALS))


# --------- Binance 实现 ---------
async def _binance_list(use_cache: bool = True) -> List[dict]:
    if use_cache and "binance" in _LIST_CACHE:
        return _LIST_CACHE["binance"]

    bases = os.getenv("BINANCE_BASE_URLS", "").strip()
    urls = [b.strip() for b in bases.split(",") if b.strip()] if bases else DEFAULT_BINANCE_BASES
    last_err = None
    for base in urls:
        try:
            cli = await _binance_client(base)
            r = await cli.get("/fapi/v1/exchangeInfo")
            # 451 = 真的地区封锁 (Binance 主动拒绝), 所有镜像都一样, 直接终止
            # 302 = 可能是代理拦截重定向 (登录页/企业策略), 继续试下一个镜像
            if r.status_code == 451:
                raise RuntimeError(
                    f"endpoint {base} 区域限制 HTTP 451 (Binance 主动封锁, "
                    f"proxy={_proxy() or 'NONE'})"
                )
            if r.status_code == 302:
                location = r.headers.get("Location", "")
                logger.warning(
                    "Binance endpoint %s HTTP 302 (Location=%s), 继续试下一个镜像",
                    base, location[:80],
                )
                last_err = RuntimeError(
                    f"endpoint {base} HTTP 302 (Location={location[:60]}) "
                    f"-- 通常是代理拦截或认证失败, 不是 Binance 真封锁"
                )
                continue
            r.raise_for_status()
            data = r.json()
            items = []
            for s in data.get("symbols", []):
                if s.get("status") != "TRADING":
                    continue
                if s.get("quoteAsset") != "USDT":
                    continue
                if s.get("contractType") != "PERPETUAL":
                    continue
                items.append({
                    "symbol": s["symbol"],
                    "base": s["baseAsset"],
                    "quote": s["quoteAsset"],
                    "display": f'{s["baseAsset"]}/{s["quoteAsset"]}',
                })
            logger.info("Binance: 成功 endpoint=%s, 加载 %d 个合约", base, len(items))
            _LIST_CACHE["binance"] = items
            return items
        except Exception as e:
            last_err = e
            logger.warning("Binance endpoint %s 失败: %s", base, e)
            # 区域限制 (451) 才立刻终止, 不再试其他镜像
            if "区域限制" in str(e) or "451" in str(e):
                break
            continue
    raise RuntimeError(f"Binance 所有 endpoint 均失败: {last_err}")


async def _binance_kline(symbol: str, interval: str, limit: int) -> List[list]:
    bi = BINANCE_INTERVAL_MAP.get(interval)
    if bi is None:
        raise ValueError(f"Binance 不支持的周期: {interval}")
    bases = os.getenv("BINANCE_BASE_URLS", "").strip()
    urls = [b.strip() for b in bases.split(",") if b.strip()] if bases else DEFAULT_BINANCE_BASES
    last_err = None
    for base in urls:
        try:
            cli = await _binance_client(base)
            params = {"symbol": symbol, "interval": bi, "limit": min(max(limit, 10), 1000)}
            r = await cli.get("/fapi/v1/klines", params=params)
            if r.status_code == 451:
                raise RuntimeError(f"区域限制 HTTP 451 @ {base}")
            if r.status_code == 302:
                last_err = RuntimeError(f"endpoint {base} HTTP 302 (代理拦截?)")
                logger.warning("Binance kline %s @ %s 302, 继续试下一个", symbol, base)
                continue
            r.raise_for_status()
            data = r.json()
            rows = []
            for k in data:
                rows.append([int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])])
            return rows
        except Exception as e:
            last_err = e
            logger.warning("Binance kline %s @ %s 失败: %s", symbol, base, e)
            if "区域限制" in str(e) or "451" in str(e):
                break
            continue
    raise RuntimeError(f"Binance K 线失败 ({symbol}): {last_err}")


# --------- OKX 实现 ---------
async def _okx_list(use_cache: bool = True) -> List[dict]:
    if use_cache and "okx" in _LIST_CACHE:
        return _LIST_CACHE["okx"]
    cli = await _okx_client()
    r = await cli.get("/api/v5/public/instruments", params={"instType": "SWAP"})
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0":
        raise RuntimeError(f"OKX 错误: {data.get('msg')}")
    items = []
    for s in data.get("data", []):
        if s.get("state") != "live":
            continue
        # 只保留 USDT 永续
        if not s["instId"].endswith("-USDT-SWAP"):
            continue
        base = s.get("uly", "").split("-")[0] or s["instId"].split("-")[0]
        items.append({
            "symbol": s["instId"],      # 用 OKX 的 instId 作为 key, 例 "BTC-USDT-SWAP"
            "base": base,
            "quote": "USDT",
            "display": f'{base}/USDT',
        })
    logger.info("OKX: 加载 %d 个 USDT 永续", len(items))
    _LIST_CACHE["okx"] = items
    return items


async def _okx_kline(symbol: str, interval: str, limit: int) -> List[list]:
    bar = OKX_BAR_MAP.get(interval)
    if bar is None:
        raise ValueError(f"OKX 不支持的周期: {interval}")
    cli = await _okx_client()
    params = {"instId": symbol, "bar": bar, "limit": str(min(max(limit, 10), 300))}
    r = await cli.get("/api/v5/market/candles", params=params)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0":
        raise RuntimeError(f"OKX K 线错误: {data.get('msg')}")
    # OKX K 线返回顺序是 [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    # 时间是毫秒
    rows = []
    for k in data.get("data", []):
        rows.append([int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])])
    return rows


# --------- 统一对外接口 ---------
def _pick_provider() -> str:
    """auto: 先试 binance, 失败回退 okx"""
    p = _PROVIDER
    if p != "auto":
        return p
    return "auto"


async def list_futures(use_cache: bool = True, provider: Optional[str] = None) -> List[dict]:
    """获取永续合约列表。provider: binance | okx | auto"""
    p = (provider or _pick_provider()).lower()
    if p in ("auto", "binance"):
        try:
            return await _binance_list(use_cache)
        except Exception as e:
            if p == "binance":
                raise
            logger.warning("Binance 列表不可用, 回退 OKX: %s", e)
    if p in ("auto", "okx"):
        return await _okx_list(use_cache)
    raise ValueError(f"未知 provider: {p}")


async def fetch_kline(
    symbol: str,
    interval: str = "1h",
    limit: int = 200,
    provider: Optional[str] = None,
) -> List[list]:
    p = (provider or _pick_provider()).lower()
    if p in ("auto", "binance"):
        try:
            return await _binance_kline(symbol, interval, limit)
        except Exception as e:
            if p == "binance":
                raise
            logger.warning("Binance K 线 %s 不可用, 回退 OKX: %s", symbol, e)
    if p in ("auto", "okx"):
        return await _okx_kline(symbol, interval, limit)
    raise ValueError(f"未知 provider: {p}")


# --------- 兼容旧 API ---------
async def list_binance_futures(use_cache: bool = True) -> List[dict]:
    return await list_futures(use_cache, "binance")


async def fetch_binance_kline(symbol: str, interval: str = "1h", limit: int = 200) -> List[list]:
    return await fetch_kline(symbol, interval, limit, "binance")
