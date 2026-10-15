# -*- coding: utf-8 -*-
"""WorkBuddy 大模型调用的公共入口

模型名统一从「配置管理」读取:
  - text   : 文本判断(相关性 / 标题同类 / 词级结论)
  - vision : 图片判断(主图相似度)
未配置时回落 config 默认值。
"""
import json
import re
import sys
import threading
from typing import Any, Dict, Optional

import config

if "/Users/gx/Desktop/mypro/testApi" not in sys.path:
    sys.path.insert(0, "/Users/gx/Desktop/mypro/testApi")

_CLIENTS: Dict[str, Any] = {}
_LOCK = threading.Lock()


def model_name(kind: str = "text") -> str:
    from backend.relevance import current_model
    return current_model("vision" if kind == "vision" else "workbuddy")


def client(model: str, timeout: Optional[float] = None):
    with _LOCK:
        c = _CLIENTS.get(model)
        if c is None:
            from workbuddy import WorkBuddyClient
            c = WorkBuddyClient(
                mode="passthrough", model=model, env_file=config.WB_ENV_FILE,
                timeout=timeout or (config.WB_VISION_TIMEOUT
                                    if "v" in model else config.WB_TIMEOUT))
            _CLIENTS[model] = c
        return c


def ask(prompt: str, model: Optional[str] = None, kind: str = "text",
        max_tokens: Optional[int] = None) -> str:
    """单轮对话, 返回文本"""
    m = model or model_name(kind)
    mt = max_tokens or (config.WB_VISION_MAX_TOKENS if kind == "vision"
                        else config.WB_MAX_TOKENS)
    r = client(m).chat_raw(prompt, temperature=config.WB_TEMPERATURE,
                           max_tokens=mt)
    return (r.content or "").strip()


def ask_json(prompt: str, model: Optional[str] = None, kind: str = "text",
             max_tokens: Optional[int] = None) -> Dict[str, Any]:
    """要求模型输出 JSON, 宽松解析"""
    raw = ask(prompt, model, kind, max_tokens)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise RuntimeError(f"模型未返回 JSON: {raw[:120]}")
    return json.loads(m.group(0))


def vision_messages(prompt: str, b64_list: list) -> Dict[str, Any]:
    """组装视觉请求体 (prompt + 若干 base64 图)"""
    content = [{"type": "text", "text": prompt}]
    for b in b64_list:
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + b}})
    return {"messages": [{"role": "user", "content": content}]}
