"""
运行时设置 (持久化在 data/settings.json)
========================================

包含:
  proxy    - HTTP 代理, crypto 路径会用到
  runtime  - 运行时开关 (no_cache 等)

API:
  get_all()             -> 完整 dict
  get_proxy()           -> 当前生效的代理配置
  is_no_cache()         -> bool
  update(proxy=, ...)   -> 写回磁盘

设计上:
  - 线程安全 (RLock)
  - 写时落盘, 读时优先内存
  - 旧字段缺失时回退到默认值, 升级友好
"""
from __future__ import annotations

import json
import logging
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 持久化路径: backend/data/settings.json
DATA_DIR = Path(__file__).resolve().parent / "data"
SETTINGS_PATH = DATA_DIR / "settings.json"


DEFAULTS = {
    "proxy": {
        "enabled": True,                          # 是否启用代理
        "url": "http://127.0.0.1:7897",           # 代理 URL (支持 http://, socks5://)
        "scope": "crypto_only",                   # "crypto_only" | "all"
    },
    "runtime": {
        "no_cache": False,                        # True 时 K 线/标的全走直连, 不读不写缓存
    },
}


_lock = threading.RLock()
_state: Optional[dict] = None


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _deepcopy_default():
    return deepcopy(DEFAULTS)


def _merge_defaults(loaded: dict) -> dict:
    """兼容老配置: 新增字段缺失时补默认值。"""
    out = _deepcopy_default()
    for top in ("proxy", "runtime"):
        if top in loaded and isinstance(loaded[top], dict):
            out[top].update(loaded[top])
    return out


def _load_from_disk() -> dict:
    if not SETTINGS_PATH.exists():
        return _deepcopy_default()
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return _merge_defaults(json.load(f))
    except Exception as e:
        logger.warning("读 settings.json 失败, 用默认值: %s", e)
        return _deepcopy_default()


def _save_to_disk():
    _ensure_dir()
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(_state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("写 settings.json 失败: %s", e)


def _ensure_loaded():
    global _state
    if _state is None:
        _state = _load_from_disk()


def get_all() -> dict:
    with _lock:
        _ensure_loaded()
        return deepcopy(_state)


def update(proxy: Optional[dict] = None, runtime: Optional[dict] = None) -> dict:
    with _lock:
        _ensure_loaded()
        if proxy:
            _state["proxy"].update(proxy)
        if runtime:
            _state["runtime"].update(runtime)
        _save_to_disk()
        return deepcopy(_state)


def get_proxy() -> dict:
    """返回当前生效的代理配置。

    规则:
      - 用户在 settings.json 配了 url -> 用它
      - 否则回退到 env (HTTPS_PROXY / HTTP_PROXY / ALL_PROXY)
      - 都没 -> None
    """
    with _lock:
        _ensure_loaded()
        p = _state["proxy"]
        url = (p.get("url") or "").strip()
        if not url:
            url = (
                os.getenv("HTTPS_PROXY")
                or os.getenv("HTTP_PROXY")
                or os.getenv("ALL_PROXY")
                or ""
            )
        return {
            "enabled": bool(p.get("enabled", True)),
            "url": url or None,
            "scope": p.get("scope", "crypto_only"),
        }


def is_no_cache() -> bool:
    with _lock:
        _ensure_loaded()
        return bool(_state["runtime"].get("no_cache", False))


def reset_to_defaults() -> dict:
    with _lock:
        global _state
        _state = _deepcopy_default()
        _save_to_disk()
        return deepcopy(_state)
