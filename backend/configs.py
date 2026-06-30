"""
规则配置 (rule configs) 持久化
================================

每个配置 = 一个独立市场的规则集合, 不同市场互不共享。
存储: backend/data/configs.json

数据结构:
{
  "configs": [
    {
      "id": "uuid",
      "name": "配置名",
      "market": "a_share",            # 市场标识
      "rules": [{"interval": "1d", "pattern": "cross_mid_up", ...}],
      "created_at": 1234,
      "updated_at": 1234
    }
  ],
  "selected_id": "uuid",
  "selected_by_market": {            # 每个市场各自的选中
    "a_share": "uuid",
    "crypto": "uuid"
  }
}

API:
  list_configs(market=None)  -> 全部 (或按 market 过滤) + 选中 id
  get_config(id)             -> 单个配置
  create_config(name, market, rules)  -> 新建, 返回新配置
  update_config(id, ...)     -> 改名 / 改规则 / 改市场 / 改时间
  delete_config(id)          -> 删除
  select_config(id)          -> 切选中 (同时记录到 selected_by_market)

迁移策略:
  - 后端首次启动若无文件 -> 为每个市场 (a_share / crypto) 各创建一个默认配置
  - 老格式 (configs 无 market 字段) -> 默认归 a_share
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
CONFIGS_PATH = DATA_DIR / "configs.json"


# 支持的市场 (前后端约定的字符串)
SUPPORTED_MARKETS = ("a_share", "crypto", "crypto_binance", "crypto_okx")
DEFAULT_MARKET = "a_share"


DEFAULT_CONFIG_RULES = [
    {
        "interval": "",
        "indicator": "",
        "pattern": "",
        "value": None,
        "lookback": 200,
        "match_count": 1,
    },
]


_lock = threading.RLock()
_state: Optional[dict] = None


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _make_default_config(name: str, market: str) -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "market": market,
        "rules": deepcopy(DEFAULT_CONFIG_RULES),
        "created_at": time.time(),
        "updated_at": time.time(),
    }


def _default_state() -> dict:
    """首次启动: 为 a_share / crypto 各建一个默认配置。"""
    cfg_a = _make_default_config("默认配置", "a_share")
    cfg_c = _make_default_config("默认配置", "crypto")
    return {
        "configs": [cfg_a, cfg_c],
        "selected_id": cfg_a["id"],
        "selected_by_market": {
            "a_share": cfg_a["id"],
            "crypto": cfg_c["id"],
        },
    }


def _migrate_loaded(loaded: dict) -> dict:
    """老数据 -> 新结构补全 market + selected_by_market 字段。"""
    if "selected_by_market" not in loaded:
        loaded["selected_by_market"] = {}
    for c in loaded.get("configs", []):
        if "market" not in c:
            c["market"] = DEFAULT_MARKET
    # 选中按市场分布: 如果没指定, 用 selected_id
    sel = loaded.get("selected_id")
    if sel:
        sel_cfg = next((c for c in loaded["configs"] if c["id"] == sel), None)
        if sel_cfg:
            m = sel_cfg.get("market", DEFAULT_MARKET)
            if not loaded["selected_by_market"].get(m):
                loaded["selected_by_market"][m] = sel
    # 每个市场至少要有一个配置
    for m in (DEFAULT_MARKET, "crypto"):
        if not any(c.get("market") == m for c in loaded["configs"]):
            new = _make_default_config("默认配置", m)
            loaded["configs"].append(new)
            if not loaded["selected_by_market"].get(m):
                loaded["selected_by_market"][m] = new["id"]
    return loaded


def _load_from_disk_with_migration_flag() -> tuple[dict, bool]:
    """返回 (state, migrated)。 migrated=True 表示加载时发生了格式迁移, 应当落盘。"""
    if not CONFIGS_PATH.exists():
        return _default_state(), True
    try:
        with open(CONFIGS_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, list):
            logger.info("configs.json 是老格式, 迁移为新格式")
            return _default_state(), True
        if "configs" not in loaded or not loaded["configs"]:
            return _default_state(), True
        # 标记是否需要回写 (迁移时新增了 market/selected_by_market 字段)
        before = json.dumps(loaded, sort_keys=True, ensure_ascii=False)
        loaded = _migrate_loaded(loaded)
        after = json.dumps(loaded, sort_keys=True, ensure_ascii=False)
        migrated = (before != after)
        # 校验: 确保 selected_id 指向一个存在的配置
        ids = {c["id"] for c in loaded["configs"]}
        if loaded.get("selected_id") not in ids:
            loaded["selected_id"] = loaded["configs"][0]["id"]
            migrated = True
        if migrated:
            logger.info("configs.json 已迁移到新格式 (含 market / selected_by_market)")
        return loaded, migrated
    except Exception as e:
        logger.warning("读 configs.json 失败, 用默认: %s", e)
        return _default_state(), True


def _load_from_disk() -> dict:
    state, _ = _load_from_disk_with_migration_flag()
    return state


def _save_to_disk():
    _ensure_dir()
    try:
        with open(CONFIGS_PATH, "w", encoding="utf-8") as f:
            json.dump(_state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("写 configs.json 失败: %s", e)


def _ensure_loaded():
    global _state
    if _state is None:
        _state, migrated = _load_from_disk_with_migration_flag()
        # 首次启动 或 老格式迁移 -> 落盘
        if not CONFIGS_PATH.exists() or migrated:
            _save_to_disk()


# ============== 公共 API ==============
def list_configs(market: Optional[str] = None) -> dict:
    """返回 {configs: [...], selected_id: '...', selected_by_market: {...}} (深拷贝)。

    market: 给定时只返回该市场的配置, selected_id 也会按市场选。
    """
    with _lock:
        _ensure_loaded()
        if market:
            cfgs = [c for c in _state["configs"] if c.get("market") == market]
            sel = _state.get("selected_by_market", {}).get(market)
            if not sel or sel not in {c["id"] for c in cfgs}:
                sel = cfgs[0]["id"] if cfgs else None
            return {
                "configs": deepcopy(cfgs),
                "selected_id": sel,
                "selected_by_market": deepcopy(_state.get("selected_by_market", {})),
                "market": market,
            }
        return {
            "configs": deepcopy(_state["configs"]),
            "selected_id": _state.get("selected_id"),
            "selected_by_market": deepcopy(_state.get("selected_by_market", {})),
        }


def get_config(cid: str) -> Optional[dict]:
    with _lock:
        _ensure_loaded()
        for c in _state["configs"]:
            if c["id"] == cid:
                return deepcopy(c)
        return None


def get_selected_config(market: Optional[str] = None) -> Optional[dict]:
    with _lock:
        _ensure_loaded()
        if market:
            cid = _state.get("selected_by_market", {}).get(market)
        else:
            cid = _state.get("selected_id")
        if not cid:
            return None
        return get_config(cid)


def create_config(name: str = "新配置", market: str = DEFAULT_MARKET,
                  rules: Optional[List[dict]] = None) -> dict:
    with _lock:
        _ensure_loaded()
        if market not in SUPPORTED_MARKETS:
            market = DEFAULT_MARKET
        cid = uuid.uuid4().hex[:12]
        now = time.time()
        if rules is None:
            rules = deepcopy(DEFAULT_CONFIG_RULES)
        cfg = {
            "id": cid,
            "name": (name or "新配置").strip()[:50],
            "market": market,
            "rules": rules,
            "created_at": now,
            "updated_at": now,
        }
        _state["configs"].append(cfg)
        # 首次创建 -> 自动选中新配置
        if not _state.get("selected_id"):
            _state["selected_id"] = cid
        # 该市场之前没选中 -> 默认选这个
        if not _state.get("selected_by_market", {}).get(market):
            _state.setdefault("selected_by_market", {})[market] = cid
        _save_to_disk()
        return deepcopy(cfg)


def update_config(cid: str,
                  name: Optional[str] = None,
                  rules: Optional[List[dict]] = None,
                  market: Optional[str] = None) -> Optional[dict]:
    """改名 / 改规则 / 改市场, 自动更新 updated_at。"""
    with _lock:
        _ensure_loaded()
        for c in _state["configs"]:
            if c["id"] == cid:
                if name is not None:
                    c["name"] = name.strip()[:50] or c["name"]
                if rules is not None:
                    c["rules"] = rules
                if market is not None and market in SUPPORTED_MARKETS:
                    c["market"] = market
                c["updated_at"] = time.time()
                _save_to_disk()
                return deepcopy(c)
        return None


def delete_config(cid: str) -> dict:
    """删除配置, 自动选同市场的另一个; 同市场最后一个不删。"""
    with _lock:
        _ensure_loaded()
        target = next((c for c in _state["configs"] if c["id"] == cid), None)
        if not target:
            return {"deleted": 0, "selected_id": _state.get("selected_id")}
        # 同市场至少保留 1 个
        same_market = [c for c in _state["configs"]
                       if c.get("market") == target.get("market") and c["id"] != cid]
        if not same_market:
            return {"deleted": 0, "error": f"市场 {target.get('market')} 至少保留 1 个配置", "selected_id": _state.get("selected_id")}
        _state["configs"] = [c for c in _state["configs"] if c["id"] != cid]
        # 重新计算 selected_id
        sel_by_mkt = _state.setdefault("selected_by_market", {})
        if _state.get("selected_id") == cid:
            _state["selected_id"] = _state["configs"][0]["id"] if _state["configs"] else None
        for m, sel_id in list(sel_by_mkt.items()):
            if sel_id == cid:
                # 找该市场第一个剩下的
                m_cfg = next((c for c in _state["configs"] if c.get("market") == m), None)
                sel_by_mkt[m] = m_cfg["id"] if m_cfg else None
        _save_to_disk()
        return {"deleted": 1, "selected_id": _state.get("selected_id")}


def select_config(cid: str) -> bool:
    with _lock:
        _ensure_loaded()
        target = next((c for c in _state["configs"] if c["id"] == cid), None)
        if not target:
            return False
        _state["selected_id"] = cid
        m = target.get("market", DEFAULT_MARKET)
        _state.setdefault("selected_by_market", {})[m] = cid
        _save_to_disk()
        return True


def reset_to_defaults() -> dict:
    """测试用, 重置为初始状态。"""
    global _state
    with _lock:
        _state = _default_state()
        _save_to_disk()
        return deepcopy(_state)
