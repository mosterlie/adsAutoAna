# -*- coding: utf-8 -*-
"""WorkBuddy 调用参数的唯一解析入口 (跨平台)

全项目的 workbuddy 调用(品名解析 / 搜索词翻译 / 相关性语义判断 / 深度检测)
都从这里取参数, 通道统一走 `backend.vendor.workbuddy` 的**透传模式**。

参数与优先级:
  地址 base_url : 「配置」里存的 workbuddy_base > config.WORKBUDDY_BASE
  Key  api_key  : 显式传入 > 环境变量 WORKBUDDY_API_KEY
                  > 项目「配置」里已存的 workbuddy key > config.WB_ENV_FILE 的 .env
  底座模型      : online_meta.wb_model(=「配置管理」页) > 项目「配置」里存的
                  workbuddy_model > config.WORKBUDDY_MODEL
                  视觉模型: online_meta.wb_vision_model > config.WB_VISION_MODEL
  超时/温度     : config.WORKBUDDY_TIMEOUT / WORKBUDDY_TEMPERATURE

「配置管理」页与「配置」弹窗写的是**同一个值**(见 set_model): 两处都会同步,
所以改哪一处都对全部功能生效, 不会出现两套配置打架。
"""
import os
import threading
from typing import Any, Dict, Optional

import config

# app_settings 里存「配置」弹窗那份配置的键(backend/ai_label.py 也用)
AI_SETTING_KEY = "ai_config"


# ---------------------------------------------------------------------------
# 连接参数
# ---------------------------------------------------------------------------
def env_file() -> Optional[str]:
    """返回可用的 .env 路径; 不存在则返回 None(客户端会跳过)"""
    p = (getattr(config, "WB_ENV_FILE", "") or "").strip()
    return p if p and os.path.isfile(p) else None


def _ai_cfg() -> Dict[str, Any]:
    """读项目「配置」弹窗保存的那份配置(读不到就空字典)"""
    try:
        from backend import database as db
        cfg = db.get_setting(AI_SETTING_KEY) or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception:       # noqa: BLE001  库还没建
        return {}


def api_key(explicit: str = "") -> str:
    """解析 API Key; 都拿不到时返回空串(客户端会报「缺少 API Key」)"""
    k = (explicit or os.environ.get("WORKBUDDY_API_KEY") or "").strip()
    if k:
        return k
    k = str(_ai_cfg().get("workbuddy_key") or "").strip()
    if k:
        return k
    return ""               # 最后回落到 .env(客户端内部会读)


def base_url() -> str:
    return str(_ai_cfg().get("workbuddy_base")
               or getattr(config, "WORKBUDDY_BASE", "")).strip()


def timeout(kind: str = "text") -> float:
    if kind == "vision":
        return float(config.WB_VISION_TIMEOUT)
    return float(config.WORKBUDDY_TIMEOUT)


def temperature(kind: str = "text") -> float:
    return float(getattr(config, "WORKBUDDY_TEMPERATURE", 0.2)
                 if kind != "vision" else 0.1)


def max_tokens(kind: str = "text") -> int:
    """输出上限: text=品名/翻译(长) · verdict=相关性JSON(短) · vision=图片判断"""
    return {"vision": config.WB_VISION_MAX_TOKENS,
            "verdict": config.WB_MAX_TOKENS}.get(kind, config.WORKBUDDY_MAX_TOKENS)


def fallback_models() -> list:
    return list(getattr(config, "WORKBUDDY_FALLBACK_MODELS", []) or [])


# ---------------------------------------------------------------------------
# 模型: 读取(唯一解析) + 写入(两处同步)
# ---------------------------------------------------------------------------
def _online(key: str) -> str:
    try:
        from backend import database as db
        v = db.get_online_meta(key)
        return v.strip() if isinstance(v, str) else ""
    except Exception:       # noqa: BLE001
        return ""


def model(kind: str = "text") -> str:
    """当前生效的模型

    kind: text(相关性/品名/翻译) · vision(深度检测图片判断) · ollama(本地兜底)
    """
    if kind == "vision":
        return _online("wb_vision_model") or config.WB_VISION_MODEL
    if kind == "ollama":
        return _online("ollama_model") or config.OLLAMA_MODEL
    return (_online("wb_model")
            or str(_ai_cfg().get("workbuddy_model") or "").strip()
            or config.WORKBUDDY_MODEL)


def set_model(value: str, kind: str = "text") -> None:
    """写模型设置; 两个存储一起写, 保证「配置管理」页与「配置」弹窗一致"""
    val = str(value or "").strip()
    if not val:
        return
    from backend import database as db
    if kind == "vision":
        db.save_online_meta("wb_vision_model", val)
        return
    if kind == "ollama":
        db.save_online_meta("ollama_model", val)
        return
    db.save_online_meta("wb_model", val)
    cfg = _ai_cfg()
    cfg["workbuddy_model"] = val
    db.set_setting(AI_SETTING_KEY, cfg)


# ---------------------------------------------------------------------------
# 客户端: 全项目唯一的构造/缓存入口 (透传模式)
# ---------------------------------------------------------------------------
_CLIENTS: Dict[tuple, Any] = {}
_CLIENT_LOCK = threading.Lock()


def client(model: str, kind: str = "text", timeout_sec: Optional[float] = None,
           key: str = "", base: str = "") -> Any:
    """取(并缓存)透传模式客户端

    参数变化(模型/地址/Key/超时)会自动重建; key/base 传入时优先(供「配置」弹窗
    未保存前做连通性测试用, 不写库即可生效)。
    """
    from backend.vendor.workbuddy import WorkBuddyClient

    b = (base or base_url()).strip()
    k = api_key(key)
    t = float(timeout_sec if timeout_sec else timeout(kind))
    ck = (model, b, k, t)
    with _CLIENT_LOCK:
        c = _CLIENTS.get(ck)
        if c is None:
            c = WorkBuddyClient(mode="passthrough", model=model, base_url=b,
                                api_key=k, env_file=env_file(), timeout=t)
            _CLIENTS[ck] = c
        return c
