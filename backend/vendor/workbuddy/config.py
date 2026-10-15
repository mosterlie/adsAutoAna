"""配置加载：环境变量 + 极简 .env 解析（仅使用标准库）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------- 调用模式

#: 正常调用：注入系统提示词，按 WorkBuddy 的标准方式组装请求
MODE_NORMAL = "normal"
#: 提示词透传：原样发送给基座模型，不注入系统提示词、不做任何加工
MODE_PASSTHROUGH = "passthrough"

DEFAULT_BASE_URL = "https://copilot.tencent.com/v2/chat/completions"
DEFAULT_MODEL = "auto"
DEFAULT_MODE = MODE_NORMAL
DEFAULT_TIMEOUT = 120.0

_MODE_ALIASES = {
    "normal": MODE_NORMAL,
    "default": MODE_NORMAL,
    "正常": MODE_NORMAL,
    "正常调用": MODE_NORMAL,
    "passthrough": MODE_PASSTHROUGH,
    "raw": MODE_PASSTHROUGH,
    "direct": MODE_PASSTHROUGH,
    "透传": MODE_PASSTHROUGH,
    "提示词透传": MODE_PASSTHROUGH,
}


def normalize_mode(mode: str | None) -> str:
    """把用户传入的模式（含中文别名）归一化为 ``normal`` / ``passthrough``。"""
    if not mode:
        return DEFAULT_MODE
    normalized = _MODE_ALIASES.get(str(mode).strip().lower())
    if normalized is None:
        raise ValueError(
            f"未知的调用模式：{mode}（可选：{MODE_NORMAL} | {MODE_PASSTHROUGH}）"
        )
    return normalized


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    mode: str = DEFAULT_MODE
    timeout: float = DEFAULT_TIMEOUT
    system_prompt: str | None = None


def load_env_file(file: str | os.PathLike[str] | None = None) -> None:
    """把 .env 中的键值写入 ``os.environ``（已存在的环境变量优先）。"""
    path = Path(file) if file else Path.cwd() / ".env"
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if (key := key.strip()) and key not in os.environ:
            os.environ[key] = value.strip().strip("'\"")


def load_config(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    mode: str | None = None,
    system_prompt: str | None = None,
    timeout: float | None = None,
    env_file: str | os.PathLike[str] | None = None,
) -> Config:
    """按「显式参数 > 环境变量 > 默认值」的优先级组装配置。"""
    load_env_file(env_file or os.environ.get("WORKBUDDY_ENV_FILE"))

    resolved_key = (api_key or os.environ.get("WORKBUDDY_API_KEY") or "").strip()
    if not resolved_key:
        raise ValueError(
            "缺少 WorkBuddy API Key：请设置 WORKBUDDY_API_KEY 环境变量或在 .env 中配置"
        )

    resolved_timeout = timeout
    if resolved_timeout is None:
        resolved_timeout = (
            float(os.environ.get("WORKBUDDY_TIMEOUT_MS", DEFAULT_TIMEOUT * 1000)) / 1000
        )

    return Config(
        api_key=resolved_key,
        base_url=(base_url or os.environ.get("WORKBUDDY_BASE_URL") or DEFAULT_BASE_URL).strip(),
        model=(model or os.environ.get("WORKBUDDY_MODEL") or DEFAULT_MODEL).strip(),
        mode=normalize_mode(mode or os.environ.get("WORKBUDDY_MODE")),
        timeout=float(resolved_timeout),
        system_prompt=(system_prompt or os.environ.get("WORKBUDDY_SYSTEM_PROMPT") or None),
    )
