"""
A 股数据源
=========
主数据源: AKShare (东方财富)
兜底数据源: 新浪财经 (HTTP JSON API, 更稳)

依赖: pip install akshare
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import contextmanager
from typing import List, Optional

import akshare as ak

logger = logging.getLogger(__name__)


@contextmanager
def _no_proxy():
    """临时让 AKShare (基于 requests) 走直连, 不读系统代理。

    Windows 注册表里设的代理, requests.getproxies_registry() 也会读,
    仅清 env vars 不够。 所以一并 patch 掉:
      - HTTP_PROXY/HTTPS_PROXY 等 env vars
      - requests.Session 的 trust_env (关键)

    httpx 客户端 (crypto 用) 我们手动传 proxy, 不依赖 env / requests,
    所以清掉对 crypto 路径零影响。
    """
    import requests

    # 1) 清 env
    saved = {}
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "all_proxy"):
        if k in os.environ:
            saved[k] = os.environ.pop(k)

    # 2) 让 requests.Session 默认 trust_env=False (不读注册表/env 代理)
    orig_session_init = requests.Session.__init__
    def patched_init(self, *a, **kw):
        orig_session_init(self, *a, **kw)
        self.trust_env = False  # 关键: 不从 env/registry 拿代理

    requests.Session.__init__ = patched_init

    try:
        yield
    finally:
        # 恢复
        requests.Session.__init__ = orig_session_init
        for k, v in saved.items():
            os.environ[k] = v


# --------- 时间周期映射 ---------
# 前端传入的 interval 名称 -> AKShare period 字符串
A_SHARE_INTERVAL_MAP = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "60m": "60",
    "1h": "60",
    "1d": "daily",
    "1w": "weekly",
    "1M": "monthly",
}

# Sina 兜底: 仅支持 5m/15m/30m/60m/1d/1w, scale=分钟数
SINA_INTERVAL_MAP = {
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "60m": "60",
    "1h": "60",
    "1d": "240",     # 4 小时 = 240 分钟 = 1 日
    "1w": "1680",    # 7 日
    # 1m / 3d / 1M 暂不支持, 返回 []
}

# A 股对外支持的周期 (前端规则 UI 用)
# - AKShare 全部 A_SHARE_INTERVAL_MAP 的 key
# - Sina 仅子集, 但因为有自动回退, 标 AKShare 的全集
A_SHARE_SUPPORTED_INTERVALS = list(A_SHARE_INTERVAL_MAP.keys())


# --------- 股票列表 ---------
_STOCK_LIST_CACHE: Optional[List[dict]] = None
_HS300_CACHE: Optional[List[dict]] = None


async def list_hs300(use_cache: bool = True) -> List[dict]:
    """沪深 300 成分股, 标准化为 {symbol, code, name, display} 字段。"""
    global _HS300_CACHE
    if use_cache and _HS300_CACHE is not None:
        return _HS300_CACHE

    def _fetch():
        with _no_proxy():
            df = ak.index_stock_cons_weight_csindex(symbol="000300")
        # 期望列包含 '成分券代码' '成分券名称'
        col_code = "成分券代码" if "成分券代码" in df.columns else df.columns[1]
        col_name = "成分券名称" if "成分券名称" in df.columns else df.columns[2]
        items = []
        for _, row in df.iterrows():
            code = str(row[col_code]).zfill(6)
            name = str(row[col_name])
            items.append({
                "symbol": code,
                "code": code,
                "name": name,
                "display": f"{code} {name}",
            })
        return items

    items = await asyncio.to_thread(_fetch)
    _HS300_CACHE = items
    logger.info("已加载沪深 300 成分股: %d", len(items))
    return items


async def list_a_stocks(use_cache: bool = True) -> List[dict]:
    """获取 A 股股票列表 (沪深京)。

    返回示例: [{"code": "000001", "name": "平安银行"}, ...]
    """
    global _STOCK_LIST_CACHE
    if use_cache and _STOCK_LIST_CACHE is not None:
        return _STOCK_LIST_CACHE

    def _fetch():
        with _no_proxy():
            df = ak.stock_info_a_code_name()
        items = []
        for _, row in df.iterrows():
            code = str(row["code"]).zfill(6)
            items.append({
                "symbol": code,      # 规范化字段, 与 crypto 数据源一致
                "code": code,        # 保留旧字段, 兼容前端
                "name": str(row["name"]),
                "display": f"{code} {row['name']}",
            })
        return items

    items = await asyncio.to_thread(_fetch)
    _STOCK_LIST_CACHE = items
    logger.info("已加载 A 股列表: %d 只", len(items))
    return items


# ============== 标签: 派生 (纯本地) + 外部 (AKShare) ==============

def derive_a_share_tags(code: str, name: str) -> List[str]:
    """从代码前缀 / 名称派生出固定标签。 不读网络, 不写缓存。

    标签 (互斥 + 风险):
      exchange: sh / sz / bj
      board:    main / star / chinext / bse
      warning:  st (含 *ST)
    """
    tags: List[str] = []
    c = (code or "").strip()
    n = (name or "").upper()

    # 交易所 + 板块 (代码前缀, 互斥)
    # 沪市主板: 60/601/603/605; 科创板: 688/689; 沪其它: 51/5xxxx 等
    if c.startswith(("60", "601", "603", "605")):
        tags.append("sh")
        tags.append("main")
    elif c.startswith(("688", "689")):
        tags.append("sh")
        tags.append("star")
    # 深市主板/中小板: 00/001/002/003; 创业板: 30x (300/301/302)
    elif c.startswith(("00", "001", "002", "003", "300", "301", "302")):
        tags.append("sz")
        if c.startswith(("300", "301", "302")):
            tags.append("chinext")
        else:
            tags.append("main")
    else:
        # 4/8/92 开头: 北证
        tags.append("bj")
        tags.append("bse")

    # 风险警示
    if "ST" in n:
        tags.append("st")

    return tags


# 缓存最近一次外部 tags 拉取结果 (进程内, 避免每扫一次都拉)
_EXTERNAL_TAGS_CACHE: dict = {"ts": 0.0, "data": {}}
_EXTERNAL_TAGS_TTL = 7 * 24 * 3600  # 7 天


async def fetch_a_share_external_tags() -> dict:
    """从 AKShare 拉 双融 / 蓝筹 / 白马 标签, 返回 {code: ['双融','蓝筹',...]}。

    7 次 AKShare call 全部并行 (asyncio.gather + asyncio.to_thread), 单次失败不影响其他。
    进程内 LRU 缓存 7 天。
    """
    now = time.time()
    if now - _EXTERNAL_TAGS_CACHE["ts"] < _EXTERNAL_TAGS_TTL and _EXTERNAL_TAGS_CACHE["data"]:
        return _EXTERNAL_TAGS_CACHE["data"]

    # 计算最近一个交易日 (YYYYMMDD): 周末/节假日回退到上一个工作日
    trade_date = _last_trade_date_str()

    async def _margin_pa(symbol: str) -> set:
        """拉一份双融标的 (沪/深/北) - 平安证券接口。"""
        def _do():
            with _no_proxy():
                df = ak.stock_margin_ratio_pa(symbol=symbol, date=trade_date)
            codes = set()
            if df is None or df.empty:
                return codes
            for v in df["证券代码"].astype(str).tolist():
                codes.add(v.zfill(6))
            return codes
        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            logger.warning("拉取双融 %s 失败: %s", symbol, e)
            return set()

    async def _index_cons(symbol: str) -> set:
        """拉一份指数成分股 (代码)。"""
        def _do():
            with _no_proxy():
                df = ak.index_stock_cons_weight_csindex(symbol=symbol)
            col = "成分券代码" if "成分券代码" in df.columns else df.columns[1]
            codes = set()
            for v in df[col].astype(str).tolist():
                codes.add(v.zfill(6))
            return codes
        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            logger.warning("拉取指数 %s 失败: %s", symbol, e)
            return set()

    # 并行: 3 个双融 + 2 个蓝筹 (上证50+HS300) + 2 个白马 (HS300+中证100)
    margin_sh, margin_sz, margin_bj, sse50, hs300, csi100 = await asyncio.gather(
        _margin_pa("沪市"),
        _margin_pa("深市"),
        _margin_pa("北交所"),
        _index_cons("000016"),  # 上证50
        _index_cons("000300"),  # 沪深300
        _index_cons("000903"),  # 中证100
    )

    margin_all = margin_sh | margin_sz | margin_bj
    bluechip = sse50 | hs300   # 蓝筹
    whitehorse = hs300 | csi100  # 白马

    result: dict = {}
    for code in margin_all | bluechip | whitehorse:
        tags = []
        if code in margin_all:
            tags.append("双融")
        if code in bluechip:
            tags.append("蓝筹")
        if code in whitehorse:
            tags.append("白马")
        result[code] = tags

    _EXTERNAL_TAGS_CACHE["ts"] = now
    _EXTERNAL_TAGS_CACHE["data"] = result
    logger.info("外部标签就绪: 双融=%d 蓝筹=%d 白马=%d 覆盖=%d",
                len(margin_all), len(bluechip), len(whitehorse), len(result))
    return result


def _last_trade_date_str() -> str:
    """返回最近一个交易日的 YYYYMMDD 字符串 (考虑周末)。"""
    import datetime as _dt
    d = _dt.date.today()
    # 周末回退 (5=周六, 6=周日)
    while d.weekday() >= 5:
        d -= _dt.timedelta(days=1)
    return d.strftime("%Y%m%d")


# --------- 单只 K 线 ---------
async def fetch_a_share_kline(
    symbol: str,
    interval: str = "1d",
    limit: int = 200,
) -> List[list]:
    """获取单只 A 股的 K 线数据。

    主路径: AKShare (东方财富)。 失败时自动回退到新浪财经 (HTTP JSON API)。
    Sina 支持: 5m / 15m / 30m / 1h / 1d / 1w
    其它周期 (1m / 3d / 1M) 仅依赖 AKShare。

    Parameters
    ----------
    symbol : str
        6 位股票代码, 例如 "000001"
    interval : str
        周期, 取值: 1m, 5m, 15m, 30m, 1h, 1d, 1w, 1M
    limit : int
        返回 K 线根数

    Returns
    -------
    list of [time, open, high, low, close, volume, ...]
    """
    period = A_SHARE_INTERVAL_MAP.get(interval)
    if period is None:
        raise ValueError(f"A 股不支持的周期: {interval}")

    def _fetch_ak():
        """主路径: AKShare, 任何异常向上抛。"""
        with _no_proxy():
            if period in {"1", "5", "15", "30", "60"}:
                df = ak.stock_zh_a_hist_min_em(
                    symbol=symbol, period=period, adjust="qfq",
                )
            elif period == "daily":
                df = ak.stock_zh_a_hist(
                    symbol=symbol, period="daily", adjust="qfq",
                )
            elif period == "weekly":
                df = ak.stock_zh_a_hist(
                    symbol=symbol, period="weekly", adjust="qfq",
                )
            elif period == "monthly":
                df = ak.stock_zh_a_hist(
                    symbol=symbol, period="monthly", adjust="qfq",
                )
            else:
                raise ValueError(f"未知周期: {period}")

        if df is None or df.empty:
            return []
        # 列名标准化
        if "时间" in df.columns:
            time_col = "时间"
        else:
            time_col = "日期"
        rows = []
        for _, r in df.iterrows():
            ts = int(pd_to_unix(r[time_col]))
            rows.append([
                ts,
                float(r["开盘"]),
                float(r["最高"]),
                float(r["最低"]),
                float(r["收盘"]),
                float(r["成交量"]),
            ])
        return rows[-limit:]

    def _fetch_sina():
        """兜底: 新浪财经 HTTP JSON API。"""
        import requests

        scale = SINA_INTERVAL_MAP.get(interval)
        if scale is None:
            # 该周期 Sina 不支持, 跳过
            return []

        prefix = _sina_symbol_prefix(symbol)
        sina_symbol = f"{prefix}{symbol}"
        url = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
        params = {"symbol": sina_symbol, "scale": scale, "ma": "no", "datalen": str(limit)}
        with _no_proxy():
            r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        text = r.text.strip()
        if not text or text == "null":
            return []
        try:
            data = json.loads(text)
        except Exception as e:
            raise RuntimeError(f"Sina 返回非 JSON: {text[:80]}")
        if not isinstance(data, list):
            return []
        rows = []
        for k in data:
            ts_str = k.get("day", "")
            try:
                ts = int(pd_to_unix(ts_str))
            except Exception:
                continue
            rows.append([
                ts,
                float(k["open"]),
                float(k["high"]),
                float(k["low"]),
                float(k["close"]),
                float(k.get("volume") or 0),
            ])
        return rows

    def _fetch():
        # 决定主备顺序
        # - Sina 支持的周期 (5m/15m/30m/60m/1d/1w): Sina 优先 (HTTP, 不走 TLS, 更稳)
        # - 其它 (1m/3d/1M): AKShare 优先 (Sina 不支持)
        sina_supports = interval in SINA_INTERVAL_MAP
        if sina_supports:
            primary_name, secondary_name = "Sina", "AKShare"
            primary, secondary = _fetch_sina, _fetch_ak
        else:
            primary_name, secondary_name = "AKShare", "Sina"
            primary, secondary = _fetch_ak, _fetch_sina

        # 试主
        try:
            rows = primary()
            if rows:
                logger.info("[K线] %s 成功: %s %s, %d 行", primary_name, symbol, interval, len(rows))
                return rows
            logger.warning("[K线] %s 返回空, 回退 %s: %s %s", primary_name, secondary_name, symbol, interval)
        except Exception as e:
            logger.warning("[K线] %s 失败, 回退 %s: %s %s: %s", primary_name, secondary_name, symbol, interval, str(e)[:120])

        # 试备用
        try:
            rows = secondary()
            if rows:
                logger.info("[K线] %s 兜底成功: %s %s, %d 行", secondary_name, symbol, interval, len(rows))
                return rows
            logger.warning("[K线] %s 兜底也返回空: %s %s", secondary_name, symbol, interval)
        except Exception as e:
            logger.warning("[K线] %s 兜底也失败: %s %s: %s", secondary_name, symbol, interval, str(e)[:120])

        # 两者都失败 -> 抛清晰错误
        raise RuntimeError(
            f"A 股 K 线拉取失败: {symbol} {interval} "
            f"(Sina + AKShare 都拉不到, 可能是网络/限流)"
        )

    return await asyncio.to_thread(_fetch)


def _sina_symbol_prefix(code: str) -> str:
    """根据 6 位代码推 Sina 用的前缀。

    6xxxxx (沪市主板) / 68xxxx (科创板) / 11/13xxxxx (沪市债券/其它) -> sh
    0xxxxx / 3xxxxx (深市主板/创业板) / 20xxxxx (深市 B 股) -> sz
    4xxxxx / 8xxxxx (北交所) / 92xxxxx (北交所新股) -> bj
    """
    if code.startswith(("60", "68", "11", "13", "5")):
        return "sh"
    if code.startswith(("00", "30", "20")):
        return "sz"
    return "bj"


def supported_intervals() -> List[str]:
    """A 股数据源支持的周期列表 (前端规则 UI 用)。"""
    return list(A_SHARE_SUPPORTED_INTERVALS)


def pd_to_unix(ts) -> int:
    """把 pandas Timestamp / datetime 字符串转成秒级时间戳。"""
    import pandas as pd
    if isinstance(ts, pd.Timestamp):
        return int(ts.timestamp())
    # 字符串: '2024-01-02 09:30:00' 或 '2024-01-02'
    s = str(ts)
    try:
        return int(pd.Timestamp(s).timestamp())
    except Exception:
        return int(pd.Timestamp(s + " 15:00:00").timestamp())
