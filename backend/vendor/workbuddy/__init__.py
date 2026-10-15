# -*- coding: utf-8 -*-
"""WorkBuddy 客户端 (内置副本)

原实现位于开发机的 macOS 目录 `/Users/gx/Desktop/mypro/testApi/workbuddy`,
项目根 `参考/` 目录保留了它的源码 (client.py + config.py), 这里**原样收录**,
使相关性判断/深度检测不必依赖开发机绝对路径。

用法::

    from backend.vendor.workbuddy import WorkBuddyClient

    c = WorkBuddyClient(model="deepseek-v4.1-flash", mode="passthrough",
                        api_key=<key>, timeout=60)
    print(c.chat_raw("你好").content)

上游: https://copilot.tencent.com/v2/chat/completions (OpenAI 兼容, 仅接受流式)
"""
from .client import (  # noqa: F401
    ChatResult,
    DEFAULT_SYSTEM_PROMPT,
    KNOWN_MODELS,
    MODE_NORMAL,
    MODE_PASSTHROUGH,
    RESTRICTED_MODELS,
    StreamEvent,
    WorkBuddyClient,
    WorkBuddyError,
    create_client,
    format_payload,
    print_payload,
)
from .config import (  # noqa: F401
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT,
    Config,
    load_config,
    normalize_mode,
)

__all__ = [
    "ChatResult",
    "Config",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_SYSTEM_PROMPT",
    "DEFAULT_TIMEOUT",
    "KNOWN_MODELS",
    "MODE_NORMAL",
    "MODE_PASSTHROUGH",
    "RESTRICTED_MODELS",
    "StreamEvent",
    "WorkBuddyClient",
    "WorkBuddyError",
    "create_client",
    "format_payload",
    "load_config",
    "normalize_mode",
    "print_payload",
]
